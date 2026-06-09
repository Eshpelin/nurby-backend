"""Read queries over person_action_segments for the camera activity timeline.

The operator-facing camera dashboard reads merged action segments for a camera over a time
window. Guardian-facing reads stay in services.guardian.wellbeing (delay + consent + reveal
gated); this module is the operator/admin view and the shared serializer.

Pure DB queries; no model inference. Empty until HAR (guardian_har_enabled) is on and the
ingestion runner is writing segments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from shared.models import Camera, PersonActionSegment


def serialize(seg: PersonActionSegment, zone: str | None = None) -> dict[str, Any]:
    return {
        "id": str(seg.id),
        "camera_id": str(seg.camera_id),
        "person_id": str(seg.person_id) if seg.person_id else None,
        "person_name": seg.person_name,
        "track_id": seg.track_id,
        "action": seg.action,
        "confidence_avg": seg.confidence_avg,
        "started_at": seg.started_at.isoformat() if seg.started_at else None,
        "ended_at": seg.ended_at.isoformat() if seg.ended_at else None,
        "source": seg.source,
        "zone": zone,
    }


async def camera_segments(
    db,
    camera_id,
    *,
    since: datetime,
    until: datetime | None = None,
    action: str | None = None,
    person_id=None,
    limit: int = 500,
) -> list[dict]:
    """Action segments for one camera in a time window, newest first."""
    limit = max(1, min(2000, int(limit)))
    q = (
        select(PersonActionSegment)
        .where(PersonActionSegment.camera_id == camera_id)
        .where(PersonActionSegment.started_at >= since)
    )
    if until is not None:
        q = q.where(PersonActionSegment.started_at <= until)
    if action:
        q = q.where(PersonActionSegment.action == action)
    if person_id is not None:
        q = q.where(PersonActionSegment.person_id == person_id)
    q = q.order_by(PersonActionSegment.started_at.desc()).limit(limit)

    rows = (await db.execute(q)).scalars().all()
    cam = await db.get(Camera, camera_id)
    zone = (cam.location_label or cam.name) if cam else None
    return [serialize(r, zone=zone) for r in rows]
