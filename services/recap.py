"""Recap service. Combines recent VLM observations for a starred person into a
short natural-language status using the person's custom recap prompt."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Camera, Observation, Person
from services.events.actions import _call_vlm, _get_provider_by_kind

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Summarize how this person is doing right now in one short sentence. "
    "Focus on recency, current camera, and notable activity. "
    "Stay calm and factual. No alarmist language."
)

DEFAULT_SYSTEM = (
    "You write a one-sentence status update about a specific person based on "
    "recent camera observations. Write in plain English. Stay under 24 words. "
    "Never invent details. If there are no recent sightings, say so."
)

SIGHTING_LIMIT = 12
RECAP_TTL = timedelta(minutes=5)
PROVIDER_FALLBACK_ORDER = ("openai", "anthropic", "google", "ollama")


def _format_ago(when: datetime, now: datetime) -> str:
    delta = now - when
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


async def _collect_sightings(
    db: AsyncSession, person_id: str, cameras: dict[str, str]
) -> tuple[list[dict], Observation | None]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .where(Observation.started_at >= cutoff)
        .order_by(Observation.started_at.desc())
        .limit(400)
    )
    obs = result.scalars().all()
    out: list[dict] = []
    latest: Observation | None = None
    for o in obs:
        pd = o.person_detections or {}
        faces = pd.get("faces") or []
        if not any(f.get("person_id") == person_id for f in faces):
            continue
        if latest is None:
            latest = o
        out.append({
            "at": o.started_at,
            "camera": cameras.get(str(o.camera_id), "unknown camera"),
            "description": (o.vlm_description or "").strip()[:220],
        })
        if len(out) >= SIGHTING_LIMIT:
            break
    return out, latest


def _build_user_prompt(person: Person, sightings: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    lines = [f"Person. {person.display_name}"]
    if person.relationship:
        lines.append(f"Relationship. {person.relationship}")
    lines.append("")
    lines.append(
        "User preference for what matters. "
        + (person.recap_prompt or DEFAULT_PROMPT)
    )
    lines.append("")
    if not sightings:
        lines.append("No sightings in the last 24 hours.")
    else:
        lines.append(f"Recent sightings (newest first, up to {SIGHTING_LIMIT}).")
        for s in sightings:
            stamp = _format_ago(s["at"], now)
            desc = s["description"] or "No description"
            lines.append(f"- {stamp}, {s['camera']}. {desc}")
    lines.append("")
    lines.append("Write the one-sentence status now.")
    return "\n".join(lines)


async def _pick_provider():
    for kind in PROVIDER_FALLBACK_ORDER:
        provider = await _get_provider_by_kind(kind)
        if provider:
            return kind, provider
    return None, None


async def generate_recap(
    db: AsyncSession, person: Person, force: bool = False
) -> dict:
    """Return {status, last_seen_at, last_camera_id, last_thumbnail_path,
    sightings_24h, generated_at, cached}. Caches on the Person row."""
    now = datetime.now(timezone.utc)
    if (
        not force
        and person.recap_cached_status
        and person.recap_cached_at
        and (now - person.recap_cached_at) < RECAP_TTL
    ):
        last = await _latest_sighting_meta(db, str(person.id))
        return {
            "status": person.recap_cached_status,
            "last_seen_at": last["at"],
            "last_camera_id": last["camera_id"],
            "last_thumbnail_path": last["thumbnail_path"],
            "sightings_24h": last["count_24h"],
            "generated_at": person.recap_cached_at,
            "cached": True,
        }

    cam_result = await db.execute(select(Camera))
    cameras = {str(c.id): c.name for c in cam_result.scalars().all()}

    sightings, latest = await _collect_sightings(db, str(person.id), cameras)
    count_24h = len(sightings)

    status = await _run_vlm_status(person, sightings)

    person.recap_cached_status = status
    person.recap_cached_at = now
    await db.commit()

    return {
        "status": status,
        "last_seen_at": latest.started_at if latest else None,
        "last_camera_id": latest.camera_id if latest else None,
        "last_thumbnail_path": latest.thumbnail_path if latest else None,
        "sightings_24h": count_24h,
        "generated_at": now,
        "cached": False,
    }


async def _latest_sighting_meta(db: AsyncSession, person_id: str) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .where(Observation.started_at >= cutoff)
        .order_by(Observation.started_at.desc())
        .limit(400)
    )
    count = 0
    latest: Observation | None = None
    for o in result.scalars().all():
        pd = o.person_detections or {}
        faces = pd.get("faces") or []
        if any(f.get("person_id") == person_id for f in faces):
            if latest is None:
                latest = o
            count += 1
    return {
        "at": latest.started_at if latest else None,
        "camera_id": latest.camera_id if latest else None,
        "thumbnail_path": latest.thumbnail_path if latest else None,
        "count_24h": count,
    }


def _fallback_status(person: Person, sightings: list[dict]) -> str:
    if not sightings:
        return f"No sightings of {person.display_name} in the last 24 hours."
    s = sightings[0]
    stamp = _format_ago(s["at"], datetime.now(timezone.utc))
    return f"{person.display_name} last seen on {s['camera']} {stamp}."


async def _run_vlm_status(person: Person, sightings: list[dict]) -> str:
    kind, provider = await _pick_provider()
    if not provider:
        return _fallback_status(person, sightings)
    prompt = _build_user_prompt(person, sightings)
    try:
        raw = await _call_vlm(
            kind,
            provider,
            provider.default_model or "",
            DEFAULT_SYSTEM,
            prompt,
            None,
            None,
            20.0,
        )
    except Exception:
        logger.exception("Recap VLM call failed for %s", person.id)
        return _fallback_status(person, sightings)
    cleaned = (raw or "").strip()
    if not cleaned:
        return _fallback_status(person, sightings)
    # Collapse newlines, keep it one sentence-ish.
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > 240:
        cleaned = cleaned[:237].rstrip() + "."
    return cleaned
