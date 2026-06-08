"""Meal attendance for guardian eldercare.

When a recognised dependant is seen inside a dining zone during a meal window,
we record an "attended a meal" event, once per meal per day. This answers the
common eldercare question, did they come to lunch. It is presence at the table,
not intake: we never claim to measure how much someone actually ate. Measuring
intake is a separate, harder problem and is out of scope here.

Dining zones are the camera's named polygon zones whose name contains a dining
keyword (the same motion_zones used for loiter and tripwire rules). Meal windows
are local-time hour ranges. State dedupes per (person, day, meal).
"""

from __future__ import annotations

import logging
from datetime import datetime

from services.perception.guardian_zones import _bbox_center, zones_for_point

logger = logging.getLogger(__name__)

DINING_KEYWORDS = ("dining", "canteen", "mess hall", "cafeteria", "meal")

# meal name -> [start_hour, end_hour) in local time.
DEFAULT_MEAL_WINDOWS = {
    "breakfast": (7, 10),
    "lunch": (12, 15),
    "dinner": (18, 21),
}

# (person_id, day_iso, meal) already recorded.
_state: set[tuple[str, str, str]] = set()


def is_dining_zone(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in DINING_KEYWORDS)


def current_meal(now: datetime, windows: dict | None = None) -> str | None:
    """The meal whose window contains ``now``'s hour, or None."""
    windows = windows or DEFAULT_MEAL_WINDOWS
    h = now.hour
    for meal, (start, end) in windows.items():
        if start <= h < end:
            return meal
    return None


def dining_zones_for_point(center, motion_zones) -> set[str]:
    """Dining-named zones that contain ``center``."""
    return {z for z in zones_for_point(center, motion_zones) if is_dining_zone(z)}


def reset_state() -> None:
    _state.clear()


async def process(camera, faces, *, now: datetime | None = None) -> list[dict]:
    """Record meal attendance for recognised dependants seen in a dining zone
    during a meal window, deduped per person per meal per day. Returns the
    emitted events (for tests and telemetry)."""
    now = now or datetime.now()
    meal = current_meal(now)
    if meal is None:
        return []
    motion_zones = getattr(camera, "motion_zones", None)
    if not motion_zones or not faces:
        return []

    day = now.date().isoformat()
    emitted: list[dict] = []
    for f in faces:
        if not isinstance(f, dict):
            continue
        pid = f.get("person_id")
        name = f.get("person_name")
        bbox = f.get("bbox")
        if not pid or not name or not bbox or len(bbox) != 4:
            continue
        if not dining_zones_for_point(_bbox_center(bbox), motion_zones):
            continue
        key = (str(pid), day, meal)
        if key in _state:
            continue
        _state.add(key)
        await _safe_emit(name, camera, meal)
        emitted.append({"kind": "attended_meal", "person": name, "meal": meal})
    return emitted


async def _safe_emit(person_name: str, camera, meal: str) -> None:
    from services.guardian.lifecycle import notify_journey_event

    try:
        await notify_journey_event(
            "attended_meal", "person", person_name, getattr(camera, "id", None), zone=meal
        )
    except Exception:  # noqa: BLE001
        logger.debug("guardian meal emit failed", exc_info=True)
