import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from services.search.query import search_observations, answer_question
from services.search.digest import generate_digest
from services.search.embeddings import get_embedding_provider

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
    period: str = Query(default="daily", pattern="^(hourly|daily)$"),
    camera_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Generate an activity digest for the given period."""
    provider = await get_embedding_provider()
    return await generate_digest(db, period=period, camera_id=camera_id, provider=provider)
