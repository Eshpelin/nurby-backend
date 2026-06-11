"""Read API for cross-camera journey tracking.

A journey groups incidents for the same subject across multiple
cameras within an idle window. Each row carries a time-ordered
segment list and a list of camera-to-camera transitions so the UI
can render a path without joining back to incidents.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user
from shared.database import get_db
from shared.models import Incident, Journey, User

router = APIRouter()


def _serialize(j: Journey) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "subject_kind": j.subject_kind,
        "subject_key": j.subject_key,
        "started_at": j.started_at.isoformat(),
        "last_seen_at": j.last_seen_at.isoformat(),
        "ended_at": j.ended_at.isoformat() if j.ended_at else None,
        "finalized": j.finalized,
        "segments": j.segments or [],
        "transitions": j.transitions or [],
        "cameras_seen_count": j.cameras_seen_count,
        "incidents_count": j.incidents_count,
        "summary_text": j.summary_text,
        "summary_provider_name": j.summary_provider_name,
        "created_at": j.created_at.isoformat(),
    }


@router.get("")
async def list_journeys(
    subject_kind: str | None = Query(default=None),
    subject_key: str | None = Query(default=None),
    finalized: bool | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Journey).order_by(Journey.last_seen_at.desc())
    if subject_kind:
        q = q.where(Journey.subject_kind == subject_kind)
    if subject_key:
        q = q.where(Journey.subject_key == subject_key)
    if finalized is not None:
        q = q.where(Journey.finalized.is_(finalized))
    if from_:
        q = q.where(Journey.started_at >= from_)
    if to:
        q = q.where(Journey.started_at <= to)
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/{journey_id}")
async def get_journey(
    journey_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Journey, journey_id)
    if row is None:
        raise HTTPException(status_code=404, detail="journey not found")
    payload = _serialize(row)
    # Hydrate linked incidents for the detail view so the UI can show
    # per-camera occurrence counts + thumbnails without a second
    # round-trip.
    incs = (
        await db.execute(
            select(Incident)
            .where(Incident.journey_id == journey_id)
            .order_by(Incident.started_at.asc())
        )
    ).scalars().all()
    payload["incidents"] = [
        {
            "id": str(i.id),
            "camera_id": str(i.camera_id),
            "started_at": i.started_at.isoformat(),
            "last_seen_at": i.last_seen_at.isoformat(),
            "occurrence_count": i.occurrence_count,
            "finalized": i.finalized,
            "summary_text": i.summary_text,
            "thumbnails": i.thumbnails,
            "peak_observation_id": str(i.peak_observation_id) if i.peak_observation_id else None,
        }
        for i in incs
    ]
    return payload


class ReinterpretRequest(BaseModel):
    provider_id: uuid.UUID | None = None


@router.post("/{journey_id}/reinterpret")
@router.post("/{journey_id}/resummarize")
async def reinterpret_journey(
    journey_id: uuid.UUID,
    body: ReinterpretRequest | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Journey, journey_id)
    if row is None:
        raise HTTPException(status_code=404, detail="journey not found")

    from services.perception.journey_tracker import JourneyFinalizer
    from shared.models import Provider

    finalizer = JourneyFinalizer()
    provider: Provider | None = None
    if body and body.provider_id:
        provider = await db.get(Provider, body.provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        db.expunge(provider)
    else:
        provider = await finalizer._resolve_provider()  # noqa: SLF001
    if provider is None:
        raise HTTPException(status_code=500, detail="no provider configured")
    text = await finalizer._build_summary(provider, row)  # noqa: SLF001
    if not text or text.strip().upper().startswith("SKIP"):
        raise HTTPException(status_code=502, detail="summary returned empty")
    await finalizer._patch_summary(  # noqa: SLF001
        jid=journey_id,
        summary_text=text.strip(),
        provider_name=provider.name,
    )
    refreshed = await db.get(Journey, journey_id)
    return _serialize(refreshed) if refreshed else _serialize(row)
