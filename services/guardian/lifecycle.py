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


async def _resolve_departure(db, person, active_links, cam_uuid):
    """Decide whether a departure is a verified pickup.

    Returns (kind, pickup_dict) when an escort is detected ("picked_up"), or
    None to fall through to a plain "departed". Pickup detection is gated by a
    system setting; off => always plain departed.
    """
    from shared.app_settings import get_setting

    try:
        if not await get_setting("guardian_pickup_detection_enabled", True):
            return None
    except Exception:  # noqa: BLE001
        return None
    if cam_uuid is None:
        return None

    from datetime import datetime, timezone

    from services.guardian import alerts as _alerts
    from services.guardian import pickup as pickup_mod
    from shared.models import ApprovedPickup

    window = int(await get_setting("guardian_pickup_window_seconds", 120))
    escort = await pickup_mod.detect_escort(
        db, person.id, cam_uuid, datetime.now(timezone.utc), window
    )
    if not escort.get("has_escort"):
        return None

    pickups = (
        await db.execute(
            select(ApprovedPickup).where(ApprovedPickup.person_id == person.id)
        )
    ).scalars().all()
    verdict = _alerts.verify_pickup(
        pickups,
        detected_person_id=escort.get("escort_person_id"),
        detected_plate=escort.get("escort_plate"),
    )
    return "picked_up", verdict


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

            # On departure, try to infer who they left with and upgrade the
            # alert to a verified "picked_up" when an escort is found.
            if kind == "departed":
                resolved = await _resolve_departure(db, person, active, cam_uuid)
                if resolved is not None:
                    emit_kind, pickup = resolved
                    return await alerts_mod.emit(
                        db, person, emit_kind, active,
                        zone=zone, camera_id=cam_uuid, pickup=pickup,
                    )

            return await alerts_mod.emit(
                db, person, kind, active, zone=zone, camera_id=cam_uuid
            )
    except Exception:  # noqa: BLE001
        logger.debug("guardian lifecycle %s failed", kind, exc_info=True)
        return None
