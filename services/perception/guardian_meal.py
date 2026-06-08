"""Meal attendance for guardian eldercare.

We track the ACT of eating, not just being in a dining room. When the
vision-language model's caption of an observation describes a recognised
dependant eating during a meal window, we record an attended-meal event, once
per meal per day. Because it reads the caption the pipeline already produces, it
works anywhere the dependant is visible eating, including their own room, and
adds no extra VLM call. It confirms attendance and the act of eating, not how
much was eaten: intake stays out of scope.

The dependant's local time decides the meal window, so the caller passes an
already-localised timestamp. State dedupes per (person, day, meal). The pure
helpers are side-effect free for testing.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Caption phrases that indicate the act of eating. Kept to action cues so a
# caption that merely mentions "a table" or "a kitchen" does not count.
EATING_CUES = (
    "eating",
    "is eating",
    "eats",
    "having a meal",
    "having lunch",
    "having dinner",
    "having breakfast",
    "feeding",
    "being fed",
    "plate of food",
    "bowl of food",
    "spoon to",
    "fork to",
    "at the table eating",
)

# meal name -> [start_hour, end_hour) in local time.
DEFAULT_MEAL_WINDOWS = {
    "breakfast": (7, 10),
    "lunch": (12, 15),
    "dinner": (18, 21),
}

# (person_id, day_iso, meal) already recorded.
_state: set[tuple[str, str, str]] = set()


def looks_like_eating(text: str | None) -> bool:
    t = (text or "").lower()
    return any(cue in t for cue in EATING_CUES)


def current_meal(now: datetime, windows: dict | None = None) -> str | None:
    """The meal whose window contains ``now``'s local hour, or None."""
    windows = windows or DEFAULT_MEAL_WINDOWS
    h = now.hour
    for meal, (start, end) in windows.items():
        if start <= h < end:
            return meal
    return None


def _dependants(person_detections) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    faces = (person_detections or {}).get("faces") if isinstance(person_detections, dict) else None
    for f in faces or []:
        if not isinstance(f, dict):
            continue
        pid = f.get("person_id")
        name = f.get("person_name")
        if pid and name:
            out.append((str(pid), str(name)))
    return out


def reset_state() -> None:
    _state.clear()


async def process_caption(
    vlm_description: str | None,
    person_detections,
    camera_id,
    when_local: datetime,
) -> list[dict]:
    """Record meal attendance when the VLM caption shows a recognised dependant
    eating during a meal window. ``when_local`` is the observation time in the
    facility's local timezone. Deduped per person per meal per day. Returns the
    emitted events (for tests and telemetry)."""
    meal = current_meal(when_local)
    if meal is None:
        return []
    if not looks_like_eating(vlm_description):
        return []
    day = when_local.date().isoformat()
    emitted: list[dict] = []
    for pid, name in _dependants(person_detections):
        key = (pid, day, meal)
        if key in _state:
            continue
        _state.add(key)
        await _safe_emit(name, camera_id, meal)
        emitted.append({"kind": "attended_meal", "person": name, "meal": meal})
    return emitted


async def _safe_emit(person_name: str, camera_id, meal: str) -> None:
    from services.guardian.lifecycle import notify_journey_event

    try:
        await notify_journey_event(
            "attended_meal", "person", person_name, camera_id, zone=meal
        )
    except Exception:  # noqa: BLE001
        logger.debug("guardian meal emit failed", exc_info=True)
