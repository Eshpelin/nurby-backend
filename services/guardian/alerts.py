"""Guardian alert fan-out and verified-pickup logic.

The product-critical decisions live here as pure functions (who should be
told, was a pickup approved, what does the message say) so they are fully
unit-tested. The thin async ``emit`` wires them to delivery.

Delivery note (V1): in-app notifications are household-wide in the current
schema, so ``emit`` records a tagged Notification the panel surfaces and
returns the per-guardian recipient list. Per-guardian push transport
(Telegram/email per link) is a deliberate next increment; the decision logic it
needs is already complete and tested here.
"""

from __future__ import annotations

import re
from typing import Iterable

from services.guardian import entitlements as ent

# Alerts are deliverable to any tier (even alerts_only). The tier gates the
# *view* surfaces (status/image/etc), not the green safety alerts.
ALERT_DELIVERABLE_TIERS = {"full", "summary", "alerts_only"}


def _norm_plate(plate: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (plate or "").lower())


def recipients_for(links: Iterable, kind: str, now=None) -> list:
    """Active links on a person that opted into ``kind``. Honors revoke/expiry
    and the per-link alert prefs. alerts_only links still receive alerts."""
    out = []
    for link in links:
        if not ent.is_active(link, now):
            continue
        if getattr(link, "tier", "full") not in ALERT_DELIVERABLE_TIERS:
            continue
        if ent.alert_enabled(link, kind):
            out.append(link)
    return out


def verify_pickup(
    pickups: Iterable,
    *,
    detected_person_id=None,
    detected_plate: str | None = None,
) -> dict:
    """Check a departure escort against the approved-pickup registry.

    Returns {matched: bool, approved_name: str|None, by: 'person'|'vehicle'|None}.
    Precision over recall: an unrecognized escort is a yellow signal, never a
    silent pass. Only active pickups count.
    """
    plate_norm = _norm_plate(detected_plate)
    for p in pickups:
        if not getattr(p, "active", True):
            continue
        if detected_person_id is not None and getattr(p, "linked_person_id", None) is not None:
            if str(p.linked_person_id) == str(detected_person_id):
                return {"matched": True, "approved_name": p.name, "by": "person"}
        if plate_norm and _norm_plate(getattr(p, "vehicle_plate", None)) == plate_norm:
            return {"matched": True, "approved_name": p.name, "by": "vehicle"}
    return {"matched": False, "approved_name": None, "by": None}


def severity_for(kind: str, pickup_matched: bool | None = None) -> str:
    """Green alerts are 'info'. An unrecognized pickup is 'warning' (yellow)."""
    if kind == "picked_up" and pickup_matched is False:
        return "warning"
    if kind == "not_seen":
        return "warning"
    return "info"


def compose_message(kind: str, display_name: str, *, zone: str | None = None,
                    approved_name: str | None = None, pickup_matched: bool | None = None,
                    minutes: int | None = None) -> str:
    """Plain-language, calm alert text. No alarmism."""
    where = f" at {zone}" if zone else ""
    if kind == "arrived":
        return f"{display_name} arrived{where}."
    if kind == "departed":
        return f"{display_name} left{where}."
    if kind == "picked_up":
        if pickup_matched and approved_name:
            return f"{display_name} was picked up by {approved_name}."
        return f"{display_name} left with someone not on the approved-pickup list."
    if kind == "entered_zone":
        return f"{display_name} entered {zone or 'a monitored area'}."
    if kind == "left_zone":
        return f"{display_name} left {zone or 'a monitored area'}."
    if kind == "not_seen":
        m = f" for {minutes} minutes" if minutes else ""
        return f"{display_name} has not been seen{m}."
    return f"Update about {display_name}."


async def emit(
    db,
    person,
    kind: str,
    links: Iterable,
    *,
    zone: str | None = None,
    camera_id=None,
    observation_id=None,
    pickup: dict | None = None,
    now=None,
) -> dict:
    """Fan an alert out to the opted-in guardians of a person.

    Records a household Notification (so it is visible in-app) and returns the
    list of recipient link ids the alert is intended for. The actual per-link
    transport (push/email) plugs in here later without changing callers.
    """
    from shared.models import Notification

    recipients = recipients_for(links, kind, now)
    if not recipients:
        return {"recipients": [], "notification_id": None}

    display = getattr(person, "nickname", None) or person.display_name
    matched = pickup.get("matched") if pickup else None
    message = compose_message(
        kind,
        display,
        zone=zone,
        approved_name=(pickup or {}).get("approved_name"),
        pickup_matched=matched,
    )
    severity = severity_for(kind, matched)
    notif = Notification(
        message=message,
        severity=severity,
        camera_id=camera_id,
        observation_id=observation_id,
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    # Live in-app update. Best-effort.
    try:
        from services.api.ws import broadcast

        await broadcast(
            {
                "type": "notification",
                "id": str(notif.id),
                "message": message,
                "severity": severity,
                "camera_id": str(camera_id) if camera_id else None,
                "guardian": True,
            }
        )
    except Exception:  # noqa: BLE001
        pass

    # Per-guardian push (Telegram + email). Best-effort, isolated from the
    # caller. Skipped silently in unit tests whose db does not support it.
    delivery = {"telegram_sent": 0, "email_sent": 0, "guardians": 0}
    try:
        from services.guardian.delivery import deliver_to_guardians

        delivery = await deliver_to_guardians(
            db, recipients, message=message, subject=f"Nurby. {display}"
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "recipients": [str(getattr(link, "id")) for link in recipients],
        "notification_id": str(notif.id),
        "message": message,
        "severity": severity,
        "delivery": delivery,
    }
