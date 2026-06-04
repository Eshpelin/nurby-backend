"""Merged timeline feed.

Returns observations and transcripts interleaved, sorted by
``started_at`` desc. Pagination is by absolute offset for now. The
client can switch to keyset pagination once we hit 100k rows per
camera.

Each item carries a ``kind`` discriminator so the UI can pick the right
card component without inspecting fields.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user
from shared.database import get_db
from shared.models import Observation, Transcript, User


class SummarizeWindowBody(BaseModel):
    window_start: datetime
    window_end: datetime

router = APIRouter()


@router.post("/summarize")
async def summarize_window(
    body: SummarizeWindowBody,
    _current_user: User = Depends(get_current_user),
):
    """Narrative VLM recap of a specific time window (one timeline hour).

    Reuses the morning-brief pipeline so the result reads like the daily
    brief. on-demand, not persisted. Returns {summary, notable_events}.
    """
    if body.window_end <= body.window_start:
        raise HTTPException(status_code=400, detail="window_end must be after window_start")
    span_hours = (body.window_end - body.window_start).total_seconds() / 3600
    if span_hours > 26:
        raise HTTPException(status_code=400, detail="Window too large (max 26 hours)")
    from services.perception.daily_digest import narrate_window
    return await narrate_window(body.window_start, body.window_end)


@router.get("")
async def get_timeline(
    camera_id: uuid.UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    obs_q = select(Observation).order_by(Observation.started_at.desc())
    tx_q = select(Transcript).where(Transcript.filtered.is_(False)).order_by(
        Transcript.started_at.desc()
    )

    obs_clauses: list = []
    tx_clauses: list = [Transcript.filtered.is_(False)]
    if camera_id:
        obs_clauses.append(Observation.camera_id == camera_id)
        tx_clauses.append(Transcript.camera_id == camera_id)
    if from_:
        obs_clauses.append(Observation.started_at >= from_)
        tx_clauses.append(Transcript.started_at >= from_)
    if to:
        obs_clauses.append(Observation.started_at <= to)
        tx_clauses.append(Transcript.started_at <= to)
    if obs_clauses:
        obs_q = obs_q.where(and_(*obs_clauses))
    tx_q = tx_q.where(and_(*tx_clauses))

    # Pull a window large enough that any interleave order is covered.
    window = limit + offset
    obs_rows = (await db.execute(obs_q.limit(window))).scalars().all()
    tx_rows = (await db.execute(tx_q.limit(window))).scalars().all()

    items: list[dict[str, Any]] = []
    for o in obs_rows:
        items.append(
            {
                "kind": "observation",
                "id": str(o.id),
                "camera_id": str(o.camera_id),
                "started_at": o.started_at.isoformat(),
                "ended_at": o.ended_at.isoformat() if o.ended_at else None,
                "vlm_description": o.vlm_description,
                "thumbnail_path": o.thumbnail_path,
                "object_detections": o.object_detections,
                "person_detections": o.person_detections,
            }
        )
    for t in tx_rows:
        items.append(
            {
                "kind": "transcript",
                "id": str(t.id),
                "camera_id": str(t.camera_id),
                "started_at": t.started_at.isoformat(),
                "ended_at": t.ended_at.isoformat(),
                "text": t.text,
                "audio_capture_id": str(t.audio_capture_id) if t.audio_capture_id else None,
                "language": t.language,
                "provider": t.provider,
            }
        )
    items.sort(key=lambda x: x["started_at"], reverse=True)
    return {"items": items[offset : offset + limit], "total_seen": len(items)}
