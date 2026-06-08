"""Standardised per-person action recognition for guardian signals.

The perception pipeline produces a free-text VLM caption per observation. That
caption is great for a timeline and for search, but it is prose: brittle to
keyword-match and impossible to query ("show me every meal Mum ate"). This module
adds a parallel, STRUCTURED signal. For each recognised dependant in a frame we
ask the VLM a single, constrained question and force the answer into one of a
closed vocabulary of actions plus a coarse posture.

Why per-person crops instead of one multi-person call: small local VLMs (for
example moondream) cannot reliably index several people in one image, so asking
"person 3 is doing X" hallucinates. We instead crop to one dependant's body box
and ask "what is THIS person doing", which is robust and gives us attribution
for free, because we already know whose crop it is.

The same primitive serves three consumers.
- Fall confirm. ``action == "fallen"`` gates the fall alert, so a resident who
  is merely ``lying_down`` or ``sleeping`` in bed no longer trips it.
- Meal attendance. ``action == "eating"`` during a meal window records the meal.
- The ``observation_actions`` table. One row per dependant per observation, so
  wellbeing questions become real queries instead of caption grep.

The parsing helpers are pure and side-effect free so the vocabulary contract is
unit-testable without a VLM or a pipeline.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Closed action vocabulary. The VLM MUST pick one of these. "unknown" is the
# safe sink when the model is unsure or the crop is unreadable. Order is not
# significant, but keep "fallen" distinct from "lying_down"/"sleeping": that
# separation is the whole point of the fall false-positive fix.
ACTIONS: tuple[str, ...] = (
    "standing",
    "walking",
    "sitting",
    "lying_down",
    "fallen",
    "eating",
    "drinking",
    "sleeping",
    "playing",
    "interacting",
    "unknown",
)

# Coarse body posture, kept separate from the action. A person can be
# ``lying_down`` (posture horizontal) yet ``sleeping`` (fine) versus ``fallen``
# (alert). Posture is advisory context, not an alert trigger on its own.
POSTURES: tuple[str, ...] = (
    "upright",
    "sitting",
    "crouching",
    "horizontal",
    "unknown",
)

# Actions that, on their own, must NEVER raise a fall alert even when the body
# box geometry looks horizontal and low. This is the explicit "sleeping person
# lying down" carve-out.
NON_FALL_ACTIONS: frozenset[str] = frozenset({"sleeping", "lying_down", "sitting", "unknown"})

# JSON schema the VLM answer is validated against. Used for documentation and by
# any provider path that supports structured output; the lenient parser below is
# the safety net for providers/models that ignore the schema.
ACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "posture": {"type": "string", "enum": list(POSTURES)},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["action"],
    "additionalProperties": False,
}

# System prompt forcing a single-person classification into the closed
# vocabulary. Kept terse so small models stay on task.
CLASSIFY_SYSTEM_PROMPT = (
    "You label what ONE person in a cropped security-camera image is doing. "
    "Reply with a single JSON object and nothing else, in the form "
    '{"action": "<one action>", "posture": "<one posture>", "confidence": <0..1>}. '
    "Choose action from exactly this list: "
    + ", ".join(ACTIONS)
    + ". Choose posture from exactly this list: "
    + ", ".join(POSTURES)
    + ". Use \"fallen\" ONLY for a person who has collapsed or fallen to the "
    "floor and looks unable to get up. A person resting in a bed, on a sofa, or "
    "asleep is \"sleeping\" or \"lying_down\", NOT \"fallen\". If you cannot tell, "
    'use "unknown" with low confidence.'
)

CLASSIFY_USER_PROMPT = "What is this person doing? Answer with the JSON object only."


def _coerce_action(value) -> str:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in ACTIONS else ""


def _coerce_posture(value) -> str:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in POSTURES else "unknown"


def _coerce_confidence(value) -> float | None:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, c))


def parse_action(raw: str | None) -> dict:
    """Turn a raw VLM reply into ``{action, posture, confidence}``.

    Tries strict JSON first. Falls back to scanning the text for a known action
    word so a chatty or non-compliant model still yields a usable label. Always
    returns a dict; an unreadable reply degrades to ``action="unknown"`` rather
    than raising, because a parse failure must not silence downstream logic that
    fails open (for example fall confirm)."""
    text = (raw or "").strip()
    out = {"action": "unknown", "posture": "unknown", "confidence": None}
    if not text:
        return out

    # 1. Strict-ish JSON. Pull the first {...} block out of any surrounding prose
    #    or code fences before parsing.
    blob = text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        blob = m.group(0)
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            action = _coerce_action(data.get("action"))
            if action:
                out["action"] = action
                out["posture"] = _coerce_posture(data.get("posture"))
                out["confidence"] = _coerce_confidence(data.get("confidence"))
                return out
    except (ValueError, TypeError):
        pass

    # 2. Keyword fallback. Scan the raw text for the first action term that
    #    appears. Multi-word source phrases ("lying down") are normalised.
    lowered = text.lower()
    phrase_map = {
        "lying down": "lying_down",
        "lay down": "lying_down",
        "fallen": "fallen",
        "fell": "fallen",
        "collapsed": "fallen",
        "on the floor": "fallen",
        "eating": "eating",
        "drinking": "drinking",
        "sleeping": "sleeping",
        "asleep": "sleeping",
        "walking": "walking",
        "standing": "standing",
        "sitting": "sitting",
        "playing": "playing",
        "interacting": "interacting",
    }
    for phrase, action in phrase_map.items():
        if phrase in lowered:
            out["action"] = action
            return out
    return out


# Caption phrases mapped to a coarse action, highest-signal first. Used to
# backfill the actions table from existing prose captions without re-running the
# VLM, and as a cheap text-only classifier. Order matters: a caption saying
# "fallen on the floor" must map to "fallen" before any weaker cue.
_CAPTION_ACTION_CUES: tuple[tuple[str, str], ...] = (
    ("collapsed", "fallen"),
    ("on the floor", "fallen"),
    ("on the ground", "fallen"),
    ("has fallen", "fallen"),
    ("fallen", "fallen"),
    ("fell down", "fallen"),
    ("lying on the floor", "fallen"),
    ("being fed", "eating"),
    ("having lunch", "eating"),
    ("having dinner", "eating"),
    ("having breakfast", "eating"),
    ("having a meal", "eating"),
    ("eating", "eating"),
    ("is eating", "eating"),
    ("drinking", "drinking"),
    ("asleep", "sleeping"),
    ("sleeping", "sleeping"),
    ("lying in bed", "sleeping"),
    ("lying down", "lying_down"),
    ("walking", "walking"),
    ("playing", "playing"),
    ("sitting", "sitting"),
    ("standing", "standing"),
)


def coarse_action_from_caption(text: str | None) -> str | None:
    """Best-effort single action from a prose caption, or ``None`` when no clear
    cue is present. Text-only, no VLM. Used by the backfill so existing
    observations populate the actions table immediately; lower confidence than a
    live crop classification, so callers should mark these rows accordingly."""
    t = (text or "").lower()
    if not t:
        return None
    for phrase, action in _CAPTION_ACTION_CUES:
        if phrase in t:
            return action
    return None


def body_box_for_face(face_bbox, person_bboxes):
    """Return the person/body bbox whose region contains the face-box centre, or
    ``None``. Lets us crop a recognised dependant's whole body for action
    classification when we only know where their face is."""
    if not face_bbox or len(face_bbox) != 4:
        return None
    fx = (face_bbox[0] + face_bbox[2]) / 2.0
    fy = (face_bbox[1] + face_bbox[3]) / 2.0
    best = None
    best_area = None
    for pb in person_bboxes or []:
        if not pb or len(pb) != 4:
            continue
        if pb[0] <= fx <= pb[2] and pb[1] <= fy <= pb[3]:
            area = max(0.0, pb[2] - pb[0]) * max(0.0, pb[3] - pb[1])
            # Prefer the tightest containing box if several overlap.
            if best_area is None or area < best_area:
                best, best_area = pb, area
    return best


def is_fall_action(action: str | None) -> bool:
    """True only for the action that should raise a fall alert. Everything in
    ``NON_FALL_ACTIONS`` (sleeping, lying_down, sitting, unknown) returns False,
    which is the sleeping-resident carve-out."""
    return _coerce_action(action) == "fallen"


def confirms_fall(parsed: dict | None) -> bool:
    """Fall-confirm decision for a single classified crop. The body geometry has
    ALREADY decided the box looks horizontal and low; this is the VLM tie-break.

    Policy is deliberately fail-open on doubt, because suppressing a real fall is
    far worse than an occasional false alert.
    - ``fallen`` -> alert.
    - ``unknown`` -> alert (the model is unsure; do not stay silent).
    - any other confident action (``sleeping``, ``lying_down``, ``sitting``, ...)
      -> suppress. This is what stops a resident asleep in bed from alerting.
    """
    action = _coerce_action((parsed or {}).get("action")) or "unknown"
    return action in ("fallen", "unknown")


def _crop(frame, bbox, *, pad: float = 0.08):
    """Crop ``frame`` to ``bbox`` with a small padding, clamped to bounds.
    Returns ``None`` when the crop would be empty. Pure given numpy-like frame."""
    if frame is None or bbox is None or len(bbox) != 4:
        return None
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = bbox
    bw = x1 - x0
    bh = y1 - y0
    x0 = int(max(0, x0 - bw * pad))
    y0 = int(max(0, y0 - bh * pad))
    x1 = int(min(w, x1 + bw * pad))
    y1 = int(min(h, y1 + bh * pad))
    if x1 <= x0 or y1 <= y0:
        return None
    return frame[y0:y1, x0:x1]


def dependant_faces(person_detections) -> list[tuple[str, str, list]]:
    """Recognised dependants in a frame as ``(person_id, person_name, face_bbox)``.
    Mirrors guardian_meal/guardian_fall face handling. Skips faces without an id,
    a name, or a usable bbox."""
    out: list[tuple[str, str, list]] = []
    if not isinstance(person_detections, dict):
        return out
    for f in person_detections.get("faces") or []:
        if not isinstance(f, dict):
            continue
        pid = f.get("person_id")
        name = f.get("person_name")
        fb = f.get("bbox")
        if pid and name and fb and len(fb) == 4:
            out.append((str(pid), str(name), fb))
    return out


async def extract_for_observation(
    vlm,
    frame,
    detections,
    provider,
    *,
    observation_id,
    camera_id,
    person_detections,
    observed_at,
) -> list[dict]:
    """Classify each recognised dependant's action in ``frame`` and persist one
    ``ObservationAction`` row per dependant. Returns the classified actions (for
    meal driving, tests, telemetry). No-op with an empty return when there are no
    dependants in frame, which is also the caller's gate, so the heavier action
    pass never runs on stranger-only frames.

    Each dependant is cropped to their own body box (the person detection whose
    region contains their face), falling back to the face box when no body box
    contains it. One person per crop keeps small local VLMs reliable."""
    deps = dependant_faces(person_detections)
    if not deps:
        return []

    person_boxes = [
        d.get("bbox")
        for d in (detections or [])
        if isinstance(d, dict) and d.get("label") == "person" and d.get("bbox")
    ]

    results: list[dict] = []
    for pid, name, face_bbox in deps:
        crop_box = body_box_for_face(face_bbox, person_boxes) or face_bbox
        parsed = await classify_crop(vlm, frame, crop_box, provider)
        results.append(
            {
                "person_id": pid,
                "person_name": name,
                "action": parsed["action"],
                "posture": parsed["posture"],
                "confidence": parsed["confidence"],
            }
        )

    # Persist. Skip "unknown" rows: they carry no signal and would bloat the
    # table on every cluttered frame.
    persist = [r for r in results if r["action"] != "unknown"]
    if persist:
        await _store_actions(observation_id, camera_id, observed_at, persist)
    return results


async def _store_actions(observation_id, camera_id, observed_at, rows: list[dict]) -> None:
    import uuid as _uuid

    from shared.database import async_session
    from shared.models import ObservationAction

    def _as_uuid(v):
        if v is None or isinstance(v, _uuid.UUID):
            return v
        try:
            return _uuid.UUID(str(v))
        except (ValueError, TypeError):
            return None

    try:
        async with async_session() as db:
            for r in rows:
                db.add(
                    ObservationAction(
                        observation_id=_as_uuid(observation_id),
                        camera_id=_as_uuid(camera_id),
                        person_id=_as_uuid(r.get("person_id")),
                        person_name=r.get("person_name"),
                        action=r["action"],
                        posture=r.get("posture"),
                        confidence=r.get("confidence"),
                        observed_at=observed_at,
                    )
                )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("observation_actions store failed", exc_info=True)


async def classify_crop(vlm, frame, bbox, provider) -> dict:
    """Crop ``frame`` to ``bbox`` and ask the VLM what the single person is
    doing. Returns a parsed ``{action, posture, confidence}``. On any error or
    empty crop, returns ``action="unknown"`` so callers can decide their own
    fail-open / fail-closed policy."""
    crop = _crop(frame, bbox)
    if crop is None or getattr(crop, "size", 0) == 0:
        return {"action": "unknown", "posture": "unknown", "confidence": None}
    try:
        raw = await vlm.classify_action(crop, provider)
    except Exception:  # noqa: BLE001
        logger.debug("action classify call failed", exc_info=True)
        return {"action": "unknown", "posture": "unknown", "confidence": None}
    return parse_action(raw)
