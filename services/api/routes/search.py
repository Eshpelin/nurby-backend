import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models import Camera, DigestEntry, Provider
from shared.schemas import DigestEntryResponse
from services.search.query import search_observations, answer_question
from services.search.digest import generate_digest
from services.search.embeddings import get_embedding_provider
from services.search.backfill import backfill_embeddings

router = APIRouter()


class SearchResponse(BaseModel):
    results: list[dict]
    total: int


class QuestionRequest(BaseModel):
    question: str


class QuestionResponse(BaseModel):
    answer: str | None
    sources: list[dict]
    note: str | None = None


class BackfillResponse(BaseModel):
    updated: int
    message: str


@router.get("", response_model=SearchResponse)
async def search(
    q: str | None = Query(default=None, description="Text query"),
    camera_id: uuid.UUID | None = Query(default=None),
    person: str | None = Query(default=None, description="Person name filter"),
    object: str | None = Query(default=None, description="Object label filter"),
    time_from: datetime | None = Query(default=None),
    time_to: datetime | None = Query(default=None),
    limit: int = Query(default=30, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Search observations with structured filters and text matching."""
    results = await search_observations(
        db,
        query=q,
        camera_id=camera_id,
        person_name=person,
        object_label=object,
        time_from=time_from,
        time_to=time_to,
        limit=limit,
        offset=offset,
    )
    return SearchResponse(results=results, total=len(results))


@router.post("/ask", response_model=QuestionResponse)
async def ask_question(
    body: QuestionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Answer a natural language question grounded in observation history."""
    result = await answer_question(db, body.question)
    return QuestionResponse(**result)


@router.get("/digest")
async def get_digest(
    period: str = Query(default="daily", pattern="^(hourly|daily|1h|6h|12h|24h|48h|7d)$"),
    camera_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Generate an activity digest for the given period (on demand)."""
    custom_prompt = None
    provider = None

    # Use per-camera digest config if camera specified
    if camera_id:
        cam = await db.get(Camera, camera_id)
        if cam:
            custom_prompt = cam.digest_prompt
            if cam.digest_provider_id:
                provider = await db.get(Provider, cam.digest_provider_id)

    if not provider:
        provider = await get_embedding_provider()

    return await generate_digest(
        db, period=period, camera_id=camera_id,
        provider=provider, custom_prompt=custom_prompt,
    )


@router.get("/digests", response_model=list[DigestEntryResponse])
async def list_digests(
    camera_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List stored digests with optional camera_id filter, newest first."""
    stmt = select(DigestEntry).order_by(DigestEntry.generated_at.desc())

    if camera_id is not None:
        stmt = stmt.where(DigestEntry.camera_id == camera_id)

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/digests/latest", response_model=DigestEntryResponse | None)
async def get_latest_digest(
    camera_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent stored digest, optionally filtered by camera."""
    stmt = select(DigestEntry).order_by(DigestEntry.generated_at.desc()).limit(1)

    if camera_id is not None:
        stmt = stmt.where(DigestEntry.camera_id == camera_id)

    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()
    return entry


@router.post("/backfill", response_model=BackfillResponse)
async def run_backfill(
    batch_size: int = Query(default=50, ge=1, le=500),
):
    """Backfill description embeddings for observations that have VLM descriptions
    but no embedding yet. Intended for admin use."""
    updated = await backfill_embeddings(batch_size=batch_size)
    return BackfillResponse(
        updated=updated,
        message=f"Backfill complete. {updated} observations updated with embeddings.",
    )
