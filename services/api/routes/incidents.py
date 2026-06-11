"""Read API for connected-incident tracking.

Incidents group consecutive observations on a single camera by
identity signature (named person, face cluster, top object set)
within an idle window. Each row has a stable id so the dashboard
can render a single rolling card across page reloads and across
sessions, and so notifications can dedup against incidents instead
of individual frames.
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
from shared.models import Incident, Observation, User

router = APIRouter()


def _serialize(i: Incident) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "camera_id": str(i.camera_id),
        "signature_kind": i.signature_kind,
        "signature_key": i.signature_key,
        "started_at": i.started_at.isoformat(),
        "last_seen_at": i.last_seen_at.isoformat(),
        "ended_at": i.ended_at.isoformat() if i.ended_at else None,
        "finalized": i.finalized,
        "occurrence_count": i.occurrence_count,
        "peak_observation_id": str(i.peak_observation_id) if i.peak_observation_id else None,
        "observation_ids": i.observation_ids,
        "thumbnails": i.thumbnails,
        "summary_text": i.summary_text,
        "summary_provider_name": i.summary_provider_name,
        "conversation_id": str(i.conversation_id) if i.conversation_id else None,
        "created_at": i.created_at.isoformat(),
    }


def _serialize_obs(o: Observation) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "camera_id": str(o.camera_id),
        "started_at": o.started_at.isoformat(),
        "ended_at": o.ended_at.isoformat() if o.ended_at else None,
        "vlm_description": o.vlm_description,
        "vlm_provider": o.vlm_provider,
        "thumbnail_path": o.thumbnail_path,
        "object_detections": o.object_detections,
        "person_detections": o.person_detections,
        "primary_vlm_description": o.primary_vlm_description,
        "refined_by_provider_name": o.refined_by_provider_name,
        "refined_at": o.refined_at.isoformat() if o.refined_at else None,
        "incident_id": str(o.incident_id) if o.incident_id else None,
    }


@router.get("")
async def list_incidents(
    camera_id: uuid.UUID | None = Query(default=None),
    finalized: bool | None = Query(default=None),
    signature_kind: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Incident).order_by(Incident.last_seen_at.desc())
    if camera_id:
        q = q.where(Incident.camera_id == camera_id)
    if finalized is not None:
        q = q.where(Incident.finalized.is_(finalized))
    if signature_kind:
        q = q.where(Incident.signature_kind == signature_kind)
    if from_:
        q = q.where(Incident.started_at >= from_)
    if to:
        q = q.where(Incident.started_at <= to)
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/{incident_id}")
async def get_incident(
    incident_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Incident, incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="incident not found")
    obs_rows: list[Observation] = []
    obs_ids = row.observation_ids or []
    parsed = []
    for x in obs_ids:
        try:
            parsed.append(uuid.UUID(str(x)))
        except (TypeError, ValueError):
            continue
    if parsed:
        obs_rows = list(
            (
                await db.execute(
                    select(Observation)
                    .where(Observation.id.in_(parsed))
                    .order_by(Observation.started_at.asc())
                )
            ).scalars().all()
        )
    payload = _serialize(row)
    payload["observations"] = [_serialize_obs(o) for o in obs_rows]
    return payload


class ReinterpretRequest(BaseModel):
    provider_id: uuid.UUID | None = None


@router.post("/{incident_id}/reinterpret")
@router.post("/{incident_id}/resummarize")
async def reinterpret_incident(
    incident_id: uuid.UUID,
    body: ReinterpretRequest | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-interpret an incident with an optional model override."""
    row = await db.get(Incident, incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="incident not found")

    from services.perception.incident_tracker import IncidentFinalizer
    from shared.models import Camera, Provider

    cam = await db.get(Camera, row.camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    obs_ids = row.observation_ids or []
    parsed = []
    for x in obs_ids:
        try:
            parsed.append(uuid.UUID(str(x)))
        except (TypeError, ValueError):
            continue
    obs_rows: list[Observation] = []
    if parsed:
        obs_rows = list(
            (
                await db.execute(
                    select(Observation)
                    .where(Observation.id.in_(parsed))
                    .order_by(Observation.started_at.asc())
                )
            ).scalars().all()
        )
    if not obs_rows:
        raise HTTPException(status_code=400, detail="no observations to summarize")

    finalizer = IncidentFinalizer()
    provider: Provider | None = None
    if body and body.provider_id:
        provider = await db.get(Provider, body.provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        db.expunge(provider)
    else:
        provider = await finalizer._resolve_provider(cam)  # noqa: SLF001
    if provider is None:
        raise HTTPException(status_code=500, detail="no provider configured")
    text = await finalizer._build_summary(provider, cam, row, obs_rows)  # noqa: SLF001
    if not text or text.strip().upper().startswith("SKIP"):
        raise HTTPException(status_code=502, detail="summary returned empty")
    await finalizer._patch_summary(  # noqa: SLF001
        inc_id=incident_id,
        summary_text=text.strip(),
        provider_name=provider.name,
    )
    refreshed = await db.get(Incident, incident_id)
    return _serialize(refreshed) if refreshed else _serialize(row)
