"""Read-side queries over observation_actions for guardian wellbeing views.

The perception pipeline writes one ``ObservationAction`` row per recognised
dependant per frame (closed action vocabulary). This module turns those rows
into the two shapes guardians actually ask for, shared by the REST API and the
MCP tools so neither reimplements the query.

- ``recent_actions``. A flat, time-ordered list of a dependant's actions for a
  day-timeline ("what did Mum do today").
- ``wellbeing_summary``. A rollup ("did Dad eat, did he fall") with per-action
  counts, meals attended today, and the most recent fall.

Both take a ``cutoff`` so the caller can apply the same free-tier delay the rest
of Guardian honors. Nothing here forks identity or entitlement logic; callers
scope to the user's own active links before calling.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from shared.models import Camera, ObservationAction

# How far back a wellbeing view looks by default. Matches the timeline horizon.
WELLBEING_LOOKBACK_DAYS = 7


async def recent_actions(
    db,
    person_id,
    *,
    cutoff: datetime,
    lookback_days: int = WELLBEING_LOOKBACK_DAYS,
    limit: int = 50,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """A dependant's recent classified actions, newest first, clamped to
    ``cutoff`` (the delay horizon) and ``lookback_days``. Optional single-action
    filter (for example only ``fallen``)."""
    limit = max(1, min(200, int(limit)))
    since = cutoff - timedelta(days=lookback_days)
    q = (
        select(ObservationAction)
        .where(ObservationAction.person_id == person_id)
        .where(ObservationAction.observed_at <= cutoff)
        .where(ObservationAction.observed_at >= since)
    )
    if action:
        q = q.where(ObservationAction.action == action)
    q = q.order_by(ObservationAction.observed_at.desc()).limit(limit)

    rows = (await db.execute(q)).scalars().all()
    cam_cache: dict[Any, Any] = {}
    out: list[dict[str, Any]] = []
    for r in rows:
        cam = cam_cache.get(r.camera_id)
        if cam is None and r.camera_id is not None:
            cam = await db.get(Camera, r.camera_id)
            cam_cache[r.camera_id] = cam
        out.append(
            {
                "observation_id": str(r.observation_id),
                "action": r.action,
                "posture": r.posture,
                "confidence": r.confidence,
                "detail": getattr(r, "detail", None),
                "at": r.observed_at.isoformat(),
                "zone": (cam.location_label or cam.name) if cam else None,
            }
        )
    return out


async def wellbeing_summary(
    db,
    person_id,
    *,
    cutoff: datetime,
    lookback_days: int = WELLBEING_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Rollup of a dependant's actions over the window ending at ``cutoff``.

    Returns per-action counts, the distinct meals (breakfast/lunch/dinner sense
    is left to the meal layer; here we count ``eating`` rows) seen today, and the
    most recent ``fallen`` time if any. All clamped to the delay cutoff."""
    since = cutoff - timedelta(days=lookback_days)
    rows = (
        await db.execute(
            select(ObservationAction)
            .where(ObservationAction.person_id == person_id)
            .where(ObservationAction.observed_at <= cutoff)
            .where(ObservationAction.observed_at >= since)
            .order_by(ObservationAction.observed_at.desc())
        )
    ).scalars().all()

    counts: dict[str, int] = {}
    last_fall_at: str | None = None
    last_action: dict[str, Any] | None = None
    eating_today = 0
    today = cutoff.date()

    for r in rows:
        counts[r.action] = counts.get(r.action, 0) + 1
        if last_action is None:
            last_action = {"action": r.action, "at": r.observed_at.isoformat()}
        if r.action == "fallen" and last_fall_at is None:
            last_fall_at = r.observed_at.isoformat()
        if r.action == "eating" and r.observed_at.date() == today:
            eating_today += 1

    return {
        "counts": counts,
        "ate_today": eating_today > 0,
        "eating_events_today": eating_today,
        "last_fall_at": last_fall_at,
        "last_action": last_action,
        "window_days": lookback_days,
        "as_of": cutoff.isoformat(),
    }
