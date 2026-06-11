"""Read-only summaries API.

The summarizer worker writes rows. The frontend reads them via this
router. Filters mirror the timeline route. ``camera_id`` and a
date-range window are the only meaningful pivots for now.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user
from shared.database import get_db
from shared.models import Camera, Summary, User

router = APIRouter()


def _serialize(s: Summary) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "camera_id": str(s.camera_id),
        "kind": s.kind,
        "started_at": s.started_at.isoformat(),
        "ended_at": s.ended_at.isoformat(),
        "provider_name": s.provider_name,
        "trigger_reason": s.trigger_reason,
        "summary_text": s.summary_text,
        "people_seen": s.people_seen,
        "plates_seen": s.plates_seen,
        "object_counts": s.object_counts,
        "source_observation_ids": s.source_observation_ids,
        "source_transcript_ids": s.source_transcript_ids,
        "created_at": s.created_at.isoformat(),
    }


@router.get("")
async def list_summaries(
    camera_id: uuid.UUID | None = Query(default=None),
    kind: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Summary).order_by(Summary.started_at.desc())
    if camera_id:
        q = q.where(Summary.camera_id == camera_id)
    if kind:
        q = q.where(Summary.kind == kind)
    if from_:
        q = q.where(Summary.started_at >= from_)
    if to:
        q = q.where(Summary.started_at <= to)
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return [_serialize(r) for r in rows]


class RunSummaryRequest(BaseModel):
    camera_id: uuid.UUID
    window_minutes: int = Field(default=30, ge=1, le=1440)


@router.post("/run")
async def run_summary_now(
    body: RunSummaryRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a one-shot summary over the last N minutes for a camera.

    Reuses the same code path the periodic worker takes. Useful when the
    user wants a recap on demand without waiting for the next tick.
    """
    cam = await db.get(Camera, body.camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")

    # Lazy import to avoid pulling perception deps at API import time.
    from services.api.ws import broadcast as ws_broadcast
    from services.perception.summarizer import CameraSummarizer

    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=body.window_minutes)
    summarizer = CameraSummarizer(broadcast_fn=ws_broadcast)
    db.expunge(cam)
    await summarizer.summarize_window(
        cam=cam,
        kind="periodic",
        window_start=started,
        window_end=now,
        trigger_reason="manual",
    )
    return {"status": "ok", "window_start": started.isoformat(), "window_end": now.isoformat()}


@router.get("/{summary_id}")
async def get_summary(
    summary_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Summary, summary_id)
    if row is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return _serialize(row)
