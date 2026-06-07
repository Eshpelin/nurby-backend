"""Dependant presence for Guardian by Nurby.

Answers the three questions a guardian actually has, reusing the existing
Observation rows. Never forks identity logic; it filters the engine's output
to one bound Person and to what the link's entitlements permit.

Two hard rules from the brief:
- Never invent a location. If the person has not been seen, say so calmly.
- Free data is delayed. Every query is clamped to the link's cutoff, so a
  non-paid guardian only ever sees state at or before (now - delay).

The state-derivation logic is split into a pure function so it is unit-tested
without a database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import String as SAString
from sqlalchemy import cast, select

from services.guardian import entitlements as ent
from shared.models import Camera, Observation

# A dependant counts as "present" when their most recent visible sighting is
# within this window of the link's cutoff. Older than this (but seen at all
# today) reads as "away"; never seen reads as "unknown".
PRESENCE_FRESH_SECONDS = 600

# How far back to look for a sighting before giving up with "unknown".
PRESENCE_LOOKBACK_DAYS = 7


def derive_state(
    last_seen_at: datetime | None,
    cutoff: datetime,
    now: datetime,
    fresh_seconds: int = PRESENCE_FRESH_SECONDS,
) -> dict:
    """Pure presence-state classifier.

    ``cutoff`` is the newest moment the guardian may observe (now for paid,
    now-delay for free). ``last_seen_at`` is the timestamp of the freshest
    sighting at or before the cutoff.

    Returns state in {present, away, unknown}, the honest seconds-ago relative
    to real ``now``, and whether the underlying data is delayed.
    """
    delayed = cutoff < now
    if last_seen_at is None:
        return {
            "state": "unknown",
            "last_seen_at": None,
            "seconds_ago": None,
            "delayed": delayed,
            "as_of": cutoff,
        }
    age_vs_cutoff = (cutoff - last_seen_at).total_seconds()
    seconds_ago = int((now - last_seen_at).total_seconds())
    state = "present" if age_vs_cutoff <= fresh_seconds else "away"
    return {
        "state": state,
        "last_seen_at": last_seen_at,
        "seconds_ago": seconds_ago,
        "delayed": delayed,
        "as_of": cutoff,
    }


def _person_needle(person_id: uuid.UUID) -> str:
    # person_detections JSON shape: {"persons": [{"person_id": "<uuid>", ...}]}
    return f'%"person_id": "{person_id}"%'


async def _latest_observation_with_person(
    db,
    person_id: uuid.UUID,
    cutoff: datetime,
    allowed_camera_ids: Iterable[uuid.UUID] | None,
):
    """Most recent Observation containing the bound person, at or before the
    cutoff, restricted to the facility's exposed cameras. Returns the row or
    None. Only ever matches the one bound person, never anyone else."""
    from datetime import timedelta

    lookback = cutoff - timedelta(days=PRESENCE_LOOKBACK_DAYS)
    q = (
        select(Observation)
        .where(Observation.started_at <= cutoff)
        .where(Observation.started_at >= lookback)
        .where(cast(Observation.person_detections, SAString).ilike(_person_needle(person_id)))
        .order_by(Observation.started_at.desc())
        .limit(1)
    )
    allowed = list(allowed_camera_ids) if allowed_camera_ids is not None else None
    if allowed is not None:
        if not allowed:
            return None
        q = q.where(Observation.camera_id.in_(allowed))
    return (await db.execute(q)).scalar_one_or_none()


async def _zone_label(db, camera_id: uuid.UUID) -> tuple[str | None, str | None]:
    cam = (await db.execute(select(Camera).where(Camera.id == camera_id))).scalar_one_or_none()
    if cam is None:
        return None, None
    return (cam.location_label or cam.name), cam.name


async def dependant_status(
    db,
    link,
    person,
    now: datetime | None = None,
    *,
    free_delay_seconds: int,
    allowed_camera_ids: Iterable[uuid.UUID] | None = None,
) -> dict:
    """Compute the calm 10-second-check status for a dependant.

    Honors the link delay (free vs live_presence) and only ever references the
    bound person. The returned ``zone`` is the camera's human location label.
    Never fabricated: absent data yields state=unknown.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = ent.cutoff_time(link, free_delay_seconds, now)
    obs = await _latest_observation_with_person(db, person.id, cutoff, allowed_camera_ids)

    last_seen_at = obs.started_at if obs is not None else None
    state = derive_state(last_seen_at, cutoff, now)

    zone = camera_name = last_camera_id = None
    if obs is not None:
        zone, camera_name = await _zone_label(db, obs.camera_id)
        last_camera_id = str(obs.camera_id)

    display = getattr(person, "nickname", None) or person.display_name
    return {
        **state,
        "person_id": str(person.id),
        "display_name": display,
        "zone": zone,
        "camera_name": camera_name,
        "last_camera_id": last_camera_id,
        "observation_id": str(obs.id) if obs is not None else None,
    }


async def latest_image(
    db,
    link,
    person,
    now: datetime | None = None,
    *,
    free_delay_seconds: int,
    allowed_camera_ids: Iterable[uuid.UUID] | None = None,
) -> dict | None:
    """The freshest observation thumbnail containing the dependant, at or
    before the cutoff. Returns {observation_id, thumbnail_path, captured_at} or
    None. Throttle + entitlement checks happen in the route, not here."""
    now = now or datetime.now(timezone.utc)
    cutoff = ent.cutoff_time(link, free_delay_seconds, now)
    obs = await _latest_observation_with_person(db, person.id, cutoff, allowed_camera_ids)
    if obs is None or not obs.thumbnail_path:
        return None
    return {
        "observation_id": str(obs.id),
        "thumbnail_path": obs.thumbnail_path,
        "captured_at": obs.started_at,
    }


async def latest_clip(
    db,
    link,
    person,
    now: datetime | None = None,
    *,
    free_delay_seconds: int,
    allowed_camera_ids: Iterable[uuid.UUID] | None = None,
) -> dict | None:
    """The freshest observation recording clip containing the dependant, at or
    before the cutoff. Returns {observation_id, clip_path, captured_at} or None."""
    now = now or datetime.now(timezone.utc)
    cutoff = ent.cutoff_time(link, free_delay_seconds, now)
    obs = await _latest_observation_with_person(db, person.id, cutoff, allowed_camera_ids)
    if obs is None or not getattr(obs, "clip_path", None):
        return None
    return {
        "observation_id": str(obs.id),
        "clip_path": obs.clip_path,
        "captured_at": obs.started_at,
    }
