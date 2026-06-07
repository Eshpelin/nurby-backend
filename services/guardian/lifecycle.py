"""Perception -> Guardian lifecycle bridge.

The journey tracker calls these on a person's arrival (journey opened) and
departure (journey finalized). We resolve the bound Person, find that person's
active guardian links, and fan an alert out (which delivers per-guardian and
records a notification). Everything is fully guarded so a guardian-side error
never disturbs the perception pipeline.

This module imports only shared.* and sibling services.guardian.*; it never
imports services.perception, so wiring it into the tracker introduces no cycle.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from services.guardian import alerts as alerts_mod
from services.guardian import entitlements as ent
from shared.database import async_session
from shared.models import Camera, GuardianLink, Person

logger = logging.getLogger("nurby.guardian.lifecycle")


def _coerce_uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def notify_journey_event(
    kind: str,
    subject_kind: str | None,
    subject_key: str | None,
    camera_id=None,
) -> dict | None:
    """Emit an arrived/departed guardian alert for a journey subject.

    Only fires for person subjects (face clusters and other kinds are skipped).
    No-ops silently when the person is unknown or has no active guardian links.
    Returns the emit result (for tests) or None.
    """
    if kind not in ("arrived", "departed"):
        return None
    if subject_kind != "person" or not subject_key:
        return None
    try:
        async with async_session() as db:
            person = (
                await db.execute(
                    select(Person).where(Person.display_name == subject_key)
                )
            ).scalars().first()
            if person is None:
                return None
            links = (
                await db.execute(
                    select(GuardianLink).where(GuardianLink.person_id == person.id)
                )
            ).scalars().all()
            active = [link for link in links if ent.is_active(link)]
            if not active:
                return None
            cam_uuid = _coerce_uuid(camera_id)
            zone = None
            if cam_uuid is not None:
                cam = await db.get(Camera, cam_uuid)
                if cam is not None:
                    zone = cam.location_label or cam.name
            return await alerts_mod.emit(
                db, person, kind, active, zone=zone, camera_id=cam_uuid
            )
    except Exception:  # noqa: BLE001
        logger.debug("guardian lifecycle %s failed", kind, exc_info=True)
        return None
