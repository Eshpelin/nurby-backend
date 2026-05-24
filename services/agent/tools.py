"""Tool registry for the agent loop.

Five Phase 1 tools, all read-only. Each tool is an async callable that
takes a ``ctx`` dict + keyword params validated against the tool's
JSON schema, and returns a JSON-serializable dict.

``ctx`` shape (Wave 2 driver populates this).

    {
        "user": shared.models.User,          # current authenticated user
        "run_id": uuid.UUID | None,          # the AgentRun row id, may be None
        "db": sqlalchemy AsyncSession,       # request-scoped DB session
    }

Every tool funnels its result set through ``accessible_camera_ids`` so
users never see data outside their access scope. Window arguments are
clamped (1..720 hours) and result counts are bounded.

Wave 1A owns the AgentRun + budget + AgentVlmCall persistence. Wave 1C
owns the actual VLM analyzer that ``analyze_clip`` and ``analyze_frame``
delegate to. Both are imported lazily so this module loads even when
the sibling waves have not landed yet. The analyzer tools return a
clean ``analyzer_not_ready`` payload in that case.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy import String as SAString

from services.agent.access import accessible_camera_ids
from shared.models import Camera, Journey, Observation, Person

logger = logging.getLogger("nurby.agent.tools")


# ── Helpers ───────────────────────────────────────────────────────────


_MAX_WINDOW_HOURS = 720  # 30 days, matches docs/agent-design.md
_MIN_WINDOW_HOURS = 1


def _clamp_hours(hours: int | None) -> int:
    if hours is None:
        return 24
    try:
        h = int(hours)
    except (TypeError, ValueError):
        raise ValueError("hours must be an integer")
    if h < _MIN_WINDOW_HOURS:
        raise ValueError(f"hours must be >= {_MIN_WINDOW_HOURS}")
    if h > _MAX_WINDOW_HOURS:
        return _MAX_WINDOW_HOURS
    return h


def _clamp_limit(limit: int | None, default: int = 20, max_: int = 100) -> int:
    if limit is None:
        return default
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    if n < 1:
        return 1
    return min(n, max_)


def _to_uuid_set(values: list[str] | None) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for v in values or []:
        try:
            out.add(uuid.UUID(str(v)))
        except (TypeError, ValueError):
            continue
    return out


def _infer_role(name: str | None, location: str | None) -> str:
    """Keyword classifier for camera roles. Matches the buckets in
    docs/agent-design.md section 9.2."""
    haystack = f"{(name or '').lower()} {(location or '').lower()}"
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("entry", ("door", "entry", "front door", "porch", "gate")),
        ("kitchen", ("kitchen",)),
        ("garage", ("garage",)),
        ("outdoor", ("yard", "outdoor", "backyard", "driveway")),
        ("nursery", ("baby", "nursery")),
        ("living", ("living", "family")),
        ("bedroom", ("bedroom", "bed room")),
        ("bathroom", ("bathroom", "bath")),
        ("office", ("office",)),
    ]
    for role, needles in rules:
        for n in needles:
            if n in haystack:
                return role
    return "other"


async def _embed_query(text: str) -> list[float] | None:
    """Best-effort embedding generation. None when unavailable so the
    caller falls back to keyword-only search."""
    try:
        from services.search.embeddings import generate_embedding, get_embedding_provider

        provider = await get_embedding_provider()
        embedding = await generate_embedding(text, provider)
        if any(v != 0.0 for v in embedding):
            return embedding
    except Exception:
        logger.debug("embedding generation failed", exc_info=True)
    return None


def _thumbnail_url(thumbnail_path: str | None) -> str | None:
    if not thumbnail_path:
        return None
    # The frontend resolves these via /api/thumbnails/{path}. We return
    # the bare relative path so the agent driver / UI can mount it
    # under whatever base url the deployment uses.
    return thumbnail_path


# ── Tool 1. query_observations ────────────────────────────────────────


_QUERY_OBSERVATIONS_SCHEMA = {
    "type": "object",
    "required": ["query"],
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
        "hours": {"type": "integer", "minimum": 1, "maximum": _MAX_WINDOW_HOURS, "default": 24},
        "camera_ids": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
        },
        "person_ids": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
        },
        "labels": {
            "type": "array",
            "items": {"type": "string"},
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    },
}


async def query_observations(
    ctx: dict,
    *,
    query: str,
    hours: int = 24,
    camera_ids: list[str] | None = None,
    person_ids: list[str] | None = None,
    labels: list[str] | None = None,
    limit: int = 20,
) -> dict:
    """Search past camera observations.

    Combines pgvector cosine similarity over Observation.description_embedding
    with structured filters. The result list is bounded by ``limit``
    (clamped 1..100) and the time window is bounded by ``hours``
    (clamped 1..720). Every result is filtered through
    accessible_camera_ids before returning.
    """
    user = ctx["user"]
    db = ctx["db"]

    hours = _clamp_hours(hours)
    limit = _clamp_limit(limit, default=20, max_=100)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    allowed = await accessible_camera_ids(user, db)
    if not allowed:
        return {"count": 0, "observations": []}

    requested_cam_ids = _to_uuid_set(camera_ids)
    if requested_cam_ids:
        effective_cams = requested_cam_ids & allowed
        if not effective_cams:
            return {"count": 0, "observations": []}
    else:
        effective_cams = allowed

    filters = [
        Observation.started_at >= cutoff,
        Observation.camera_id.in_(effective_cams),
    ]

    requested_persons = _to_uuid_set(person_ids)
    if requested_persons:
        # Person UUIDs are embedded in the person_detections JSON blob
        # as the ``person_id`` field on each detected face. We match via
        # a substring scan since the column is JSON, not JSONB indexed.
        person_conditions = [
            cast(Observation.person_detections, SAString).ilike(f"%{str(pid)}%")
            for pid in requested_persons
        ]
        filters.append(or_(*person_conditions))

    if labels:
        label_conditions = []
        for lbl in labels:
            if not lbl:
                continue
            label_conditions.append(
                cast(Observation.object_detections, SAString).ilike(f"%\"{lbl}\"%")
            )
        if label_conditions:
            filters.append(or_(*label_conditions))

    rows: list[tuple[Observation, float | None]] = []
    query_embedding = await _embed_query(query) if query else None

    if query_embedding is not None:
        vec_filters = list(filters) + [Observation.description_embedding.isnot(None)]
        cosine = Observation.description_embedding.cosine_distance(query_embedding)
        stmt = (
            select(Observation, cosine.label("distance"))
            .where(and_(*vec_filters))
            .order_by(cosine.asc())
            .limit(limit * 2)
        )
        result = await db.execute(stmt)
        for obs, dist in result.all():
            rows.append((obs, float(dist) if dist is not None else None))
        # threshold low-similarity matches
        rows = [(o, d) for (o, d) in rows if d is None or d <= 0.85][:limit]

    if not rows:
        # Keyword fallback. ILIKE the vlm_description so even a
        # missing-embedding deployment returns sensible results.
        kw = query.strip()
        kw_filter = (
            Observation.vlm_description.ilike(f"%{kw}%") if kw else None
        )
        stmt = (
            select(Observation)
            .where(and_(*filters, kw_filter) if kw_filter is not None else and_(*filters))
            .order_by(Observation.started_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        rows = [(o, None) for o in result.scalars().all()]

    observations: list[Observation] = [o for o, _ in rows]

    # Resolve camera names in one shot.
    camera_map: dict[uuid.UUID, str] = {}
    if observations:
        cam_rows = await db.execute(
            select(Camera.id, Camera.name).where(
                Camera.id.in_({o.camera_id for o in observations})
            )
        )
        camera_map = {cid: cname for cid, cname in cam_rows.all()}

    out_rows: list[dict[str, Any]] = []
    for (obs, dist) in rows:
        if obs.camera_id not in allowed:
            # Belt-and-braces. effective_cams should already enforce
            # this but a stale cache or race could slip a row through.
            continue
        person_names: list[str] = []
        pd = obs.person_detections or {}
        for face in pd.get("faces", []) or []:
            name = face.get("person_name")
            if name:
                person_names.append(name)
        out_rows.append(
            {
                "id": str(obs.id),
                "camera_id": str(obs.camera_id),
                "camera_name": camera_map.get(obs.camera_id, "Unknown"),
                "timestamp": obs.started_at.isoformat(),
                "description": obs.vlm_description,
                "thumbnail_url": _thumbnail_url(obs.thumbnail_path),
                "detections": obs.object_detections,
                "person_names": person_names,
                "similarity_score": (1.0 - dist) if dist is not None else None,
            }
        )

    return {"count": len(out_rows), "observations": out_rows}


# ── Tool 2. get_journeys ──────────────────────────────────────────────


_GET_JOURNEYS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "person_id": {"type": "string", "format": "uuid"},
        "person_name": {"type": "string", "minLength": 1, "maxLength": 255},
        "hours": {"type": "integer", "minimum": 1, "maximum": _MAX_WINDOW_HOURS, "default": 24},
        "camera_ids": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    },
}


async def get_journeys(
    ctx: dict,
    *,
    person_id: str | None = None,
    person_name: str | None = None,
    hours: int = 24,
    camera_ids: list[str] | None = None,
    limit: int = 20,
) -> dict:
    """Cross-camera Person sighting sessions.

    A Journey row represents a contiguous period a Person was visible
    across one or more cameras. When ``person_name`` is provided and
    resolves to more than one Person, the response sets
    ``disambiguation`` and returns an empty ``journeys`` list. The
    Wave 2 driver convention is to surface that disambiguation back to
    the user (or pick the highest-sighting candidate if the context
    makes it obvious).
    """
    user = ctx["user"]
    db = ctx["db"]

    hours = _clamp_hours(hours)
    limit = _clamp_limit(limit, default=20, max_=100)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    allowed = await accessible_camera_ids(user, db)
    if not allowed:
        return {"journeys": []}

    requested_cams = _to_uuid_set(camera_ids)
    effective_cams = (requested_cams & allowed) if requested_cams else allowed
    if not effective_cams:
        return {"journeys": []}

    resolved_person_id: uuid.UUID | None = None
    if person_id:
        try:
            resolved_person_id = uuid.UUID(person_id)
        except (TypeError, ValueError):
            return {"journeys": [], "error": "invalid person_id"}
    elif person_name:
        like = f"%{person_name.strip()}%"
        person_rows = (
            await db.execute(
                select(Person.id, Person.display_name).where(
                    Person.display_name.ilike(like)
                )
            )
        ).all()
        if not person_rows:
            return {"journeys": [], "disambiguation": []}
        if len(person_rows) > 1:
            return {
                "journeys": [],
                "disambiguation": [
                    {"person_id": str(pid), "display_name": dname}
                    for pid, dname in person_rows
                ],
            }
        resolved_person_id = person_rows[0][0]

    filters = [
        Journey.subject_kind == "person",
        Journey.last_seen_at >= cutoff,
    ]
    if resolved_person_id is not None:
        filters.append(Journey.subject_key == str(resolved_person_id))

    stmt = (
        select(Journey)
        .where(and_(*filters))
        .order_by(Journey.last_seen_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()

    # Resolve display names for the subject persons.
    subj_ids: set[uuid.UUID] = set()
    for j in rows:
        try:
            subj_ids.add(uuid.UUID(j.subject_key))
        except (TypeError, ValueError):
            continue
    person_name_map: dict[uuid.UUID, str] = {}
    if subj_ids:
        for pid, dname in (
            await db.execute(
                select(Person.id, Person.display_name).where(Person.id.in_(subj_ids))
            )
        ).all():
            person_name_map[pid] = dname

    # Resolve camera names referenced by segments.
    seg_cam_ids: set[uuid.UUID] = set()
    for j in rows:
        for seg in j.segments or []:
            cid = seg.get("camera_id") if isinstance(seg, dict) else None
            if cid:
                try:
                    seg_cam_ids.add(uuid.UUID(cid))
                except (TypeError, ValueError):
                    continue
    cam_name_map: dict[uuid.UUID, str] = {}
    if seg_cam_ids:
        for cid, cname in (
            await db.execute(
                select(Camera.id, Camera.name).where(Camera.id.in_(seg_cam_ids))
            )
        ).all():
            cam_name_map[cid] = cname

    journeys_out: list[dict[str, Any]] = []
    for j in rows:
        # Filter segments to accessible cameras and skip the journey if
        # nothing remains.
        seg_cams: list[dict[str, str]] = []
        seen: set[uuid.UUID] = set()
        observation_count = 0
        for seg in j.segments or []:
            if not isinstance(seg, dict):
                continue
            cid_str = seg.get("camera_id")
            try:
                cid = uuid.UUID(cid_str) if cid_str else None
            except (TypeError, ValueError):
                cid = None
            if cid is None or cid not in effective_cams:
                continue
            if cid not in seen:
                seg_cams.append(
                    {"id": str(cid), "name": cam_name_map.get(cid, "Unknown")}
                )
                seen.add(cid)
            observation_count += int(seg.get("observation_count") or 0)
        if not seg_cams:
            continue

        try:
            subj_uuid = uuid.UUID(j.subject_key)
        except (TypeError, ValueError):
            subj_uuid = None

        started = j.started_at
        ended = j.ended_at or j.last_seen_at
        duration_s = int((ended - started).total_seconds()) if started and ended else None

        # Use the first segment's thumbnail if present.
        thumb = None
        for seg in j.segments or []:
            if isinstance(seg, dict) and seg.get("thumbnail_path"):
                thumb = seg.get("thumbnail_path")
                break

        journeys_out.append(
            {
                "id": str(j.id),
                "person_id": str(subj_uuid) if subj_uuid else None,
                "person_name": person_name_map.get(subj_uuid) if subj_uuid else None,
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "duration_seconds": duration_s,
                "cameras": seg_cams,
                "observation_count": observation_count,
                "thumbnail_url": _thumbnail_url(thumb),
            }
        )

    return {"journeys": journeys_out}


# ── Tool 3. get_camera_layout ─────────────────────────────────────────


_GET_CAMERA_LAYOUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}


async def get_camera_layout(ctx: dict) -> dict:
    """Bootstrap camera context. Returns every accessible camera plus
    an inferred role and current status. Wave 2 driver should call
    this first when answering location questions."""
    user = ctx["user"]
    db = ctx["db"]

    allowed = await accessible_camera_ids(user, db)
    if not allowed:
        return {"cameras": []}

    rows = (
        await db.execute(
            select(Camera)
            .where(Camera.id.in_(allowed))
            .order_by(Camera.display_order, Camera.created_at)
        )
    ).scalars().all()

    cameras = []
    for c in rows:
        cameras.append(
            {
                "id": str(c.id),
                "name": c.name,
                "location_label": c.location_label,
                "role": _infer_role(c.name, c.location_label),
                "scene_mode": c.scene_mode,
                "status": c.status,
                "timezone": c.timezone,
            }
        )
    return {"cameras": cameras}


# ── Tool 3b. get_last_sightings ───────────────────────────────────────


# Common labels we surface a baseline for even when not asked. Picks
# the labels users most often ask "where is X?" questions about and
# the existing perception pipeline tags reliably.
_BASELINE_LABELS = ("person", "cat", "dog", "package", "car", "bird")


_GET_LAST_SIGHTINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "person_name": {
            "type": "string",
            "description": (
                "Optional. Restrict to a single Person by display_name "
                "(case-insensitive substring). Returns disambiguation "
                "when more than one match."
            ),
            "minLength": 1,
            "maxLength": 255,
        },
        "labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
            "maxItems": 12,
            "description": (
                "Optional. Restrict to these YOLO labels (e.g. ['cat']). "
                "Default queries a curated baseline (person, cat, dog, "
                "package, car, bird)."
            ),
        },
        "since_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": 365,
            "default": 30,
            "description": "Search window in days. Defaults to 30.",
        },
    },
}


async def get_last_sightings(
    ctx: dict,
    person_name: str | None = None,
    labels: list[str] | None = None,
    since_days: int = 30,
) -> dict:
    """Return last-seen-at timestamps per named Person and per common
    YOLO label across the full retention window (default 30 days).

    Use this when a question is about where/when an entity was last
    visible and the default 24h `query_observations` window came back
    empty — this is the cheap baseline that avoids blind window
    widening.
    """
    user = ctx["user"]
    db = ctx["db"]

    since_days = max(1, min(365, int(since_days if since_days is not None else 30)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    allowed = await accessible_camera_ids(user, db)
    if not allowed:
        return {"persons": [], "labels": [], "since_days": since_days}

    persons_block: list[dict] = []
    disambiguation: list[dict] | None = None

    # ── Person-side lookup ──────────────────────────────────────────
    person_rows: list[Person] = []
    if person_name:
        needle = f"%{person_name.strip().lower()}%"
        rs = await db.execute(
            select(Person).where(func.lower(Person.display_name).like(needle))
        )
        person_rows = list(rs.scalars().all())
        if len(person_rows) > 1:
            disambiguation = [
                {"person_id": str(p.id), "display_name": p.display_name}
                for p in person_rows
            ]
    else:
        rs = await db.execute(select(Person).order_by(Person.display_name))
        person_rows = list(rs.scalars().all())

    if not disambiguation:
        for p in person_rows:
            # Most recent Journey that touches at least one accessible
            # camera. Journey is the right grain because the pipeline
            # writes a Journey row whenever a Person shows across one
            # or more cameras.
            j = (
                await db.execute(
                    select(Journey)
                    .where(Journey.person_id == p.id)
                    .where(Journey.last_seen_at >= cutoff)
                    .order_by(Journey.last_seen_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if j is None:
                persons_block.append(
                    {
                        "person_id": str(p.id),
                        "display_name": p.display_name,
                        "last_seen_at": None,
                        "last_camera_id": None,
                        "last_journey_id": None,
                        "days_since_seen": None,
                    }
                )
                continue
            cams = j.cameras or []
            # Filter Journey cameras down to those the user can see.
            visible = [c for c in cams if uuid.UUID(c.get("id")) in allowed] if cams else []
            persons_block.append(
                {
                    "person_id": str(p.id),
                    "display_name": p.display_name,
                    "last_seen_at": j.last_seen_at.isoformat() if j.last_seen_at else None,
                    "last_camera_id": str(visible[0]["id"]) if visible else None,
                    "last_camera_name": visible[0].get("name") if visible else None,
                    "last_journey_id": str(j.id),
                    "days_since_seen": (datetime.now(timezone.utc) - j.last_seen_at).days
                    if j.last_seen_at
                    else None,
                }
            )

    # ── Label-side lookup. uses Observation.detections JSON ──────────
    target_labels = list(labels) if labels else list(_BASELINE_LABELS)
    label_block: list[dict] = []
    for lab in target_labels:
        # Pull the latest Observation whose detections contains this
        # label. We cannot rely on a single index here so we cast the
        # JSON column to text and use ILIKE as a cheap filter. For a
        # household of typical size + 30d window this is fast enough.
        needle = f'%"label": "{lab}"%'
        row = (
            await db.execute(
                select(Observation)
                .where(Observation.camera_id.in_(allowed))
                .where(Observation.timestamp >= cutoff)
                .where(cast(Observation.detections, SAString).ilike(needle))
                .order_by(Observation.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            label_block.append(
                {
                    "label": lab,
                    "last_seen_at": None,
                    "last_camera_id": None,
                    "last_observation_id": None,
                    "days_since_seen": None,
                }
            )
            continue
        cam = (
            await db.execute(select(Camera).where(Camera.id == row.camera_id))
        ).scalar_one_or_none()
        label_block.append(
            {
                "label": lab,
                "last_seen_at": row.timestamp.isoformat(),
                "last_camera_id": str(row.camera_id),
                "last_camera_name": cam.name if cam else None,
                "last_observation_id": str(row.id),
                "thumbnail_url": _thumbnail_url(row.thumbnail_path),
                "days_since_seen": (datetime.now(timezone.utc) - row.timestamp).days,
            }
        )

    out: dict = {
        "since_days": since_days,
        "persons": persons_block,
        "labels": label_block,
    }
    if disambiguation:
        out["disambiguation"] = disambiguation
        out["persons"] = []
    return out


# ── Tool 4. analyze_clip ──────────────────────────────────────────────


_ANALYZE_CLIP_SCHEMA = {
    "type": "object",
    "required": ["camera_id", "time_from", "time_to", "question"],
    "additionalProperties": False,
    "properties": {
        "camera_id": {"type": "string", "format": "uuid"},
        "time_from": {"type": "string", "format": "date-time"},
        "time_to": {"type": "string", "format": "date-time"},
        "question": {"type": "string", "minLength": 1, "maxLength": 500},
        "provider_id": {"type": "string", "format": "uuid"},
    },
}


def _analyzer_not_ready(message: str | None = None) -> dict:
    return {
        "answer": None,
        "confidence": 0.0,
        "frames_analyzed": 0,
        "cached": False,
        "cost_cents": 0,
        "thumbnails_url_base": None,
        "vlm_call_id": None,
        "error": "analyzer_not_ready",
        "message": message
        or "VLM analyzer module not yet deployed; this is expected during build wave 1",
    }


async def _check_user_budget(ctx: dict) -> tuple[bool, str | None]:
    """Hook into Wave 1A's budget enforcement. Returns (ok, reason).

    Wave 1A provides ``services.agent.budget.check_user_budget``. If the
    module isn't present we fail open since the analyzer module also
    isn't present and will short-circuit before any real spend.
    """
    try:
        from services.agent.budget import check_user_budget  # type: ignore
    except Exception:
        return True, None
    try:
        return await check_user_budget(ctx)
    except Exception:
        logger.debug("budget check raised; allowing", exc_info=True)
        return True, None


async def analyze_clip(
    ctx: dict,
    *,
    camera_id: str,
    time_from: str,
    time_to: str,
    question: str,
    provider_id: str | None = None,
) -> dict:
    """Run a VLM against a video clip over a time window.

    Expensive. Wave 2 prompt instructs the LLM to call query_observations
    first. This call validates inputs (camera access, window length),
    checks the per-user budget, and delegates to the Wave 1C analyzer.
    """
    user = ctx["user"]
    db = ctx["db"]

    try:
        cam_uuid = uuid.UUID(camera_id)
    except (TypeError, ValueError):
        return {"error": "invalid_camera_id", "message": "camera_id must be a UUID"}

    try:
        t_from = datetime.fromisoformat(time_from.replace("Z", "+00:00"))
        t_to = datetime.fromisoformat(time_to.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return {"error": "invalid_time", "message": "time_from / time_to must be ISO datetimes"}
    if t_to <= t_from:
        return {"error": "invalid_window", "message": "time_to must be after time_from"}

    # Clip-length ceiling per AppSetting. Defaults to 5 minutes when
    # the key is missing; documented at agent_max_clip_minutes.
    try:
        from shared.app_settings import get_setting

        max_minutes = int(await get_setting("agent_max_clip_minutes", 5))
    except Exception:
        max_minutes = 5
    if (t_to - t_from).total_seconds() > max_minutes * 60:
        return {
            "error": "window_too_large",
            "message": f"clip window exceeds agent_max_clip_minutes={max_minutes}",
        }

    allowed = await accessible_camera_ids(user, db)
    if cam_uuid not in allowed:
        return {"error": "camera_access_denied", "message": "no access to this camera"}

    ok, reason = await _check_user_budget(ctx)
    if not ok:
        return {"error": "budget_exceeded", "message": reason or "user budget exhausted"}

    try:
        from services.agent.analyzer import analyze_clip_target  # type: ignore
    except Exception:
        return _analyzer_not_ready()

    try:
        provider_uuid = uuid.UUID(provider_id) if provider_id else None
    except (TypeError, ValueError):
        provider_uuid = None

    try:
        return await analyze_clip_target(
            ctx,
            camera_id=cam_uuid,
            time_from=t_from,
            time_to=t_to,
            question=question,
            provider_id=provider_uuid,
        )
    except Exception as exc:
        logger.exception("analyze_clip_target raised")
        return {
            "error": "analyzer_failed",
            "message": f"{type(exc).__name__}: {exc}",
            "answer": None,
            "confidence": 0.0,
            "frames_analyzed": 0,
            "cached": False,
            "cost_cents": 0,
        }


# ── Tool 5. analyze_frame ─────────────────────────────────────────────


_ANALYZE_FRAME_SCHEMA = {
    "type": "object",
    "required": ["observation_id", "question"],
    "additionalProperties": False,
    "properties": {
        "observation_id": {"type": "string", "format": "uuid"},
        "question": {"type": "string", "minLength": 1, "maxLength": 500},
        "provider_id": {"type": "string", "format": "uuid"},
    },
}


async def analyze_frame(
    ctx: dict,
    *,
    observation_id: str,
    question: str,
    provider_id: str | None = None,
) -> dict:
    """Run a VLM against a single Observation thumbnail. Cheapest VLM
    path. Use after query_observations returned a candidate row that
    needs a closer look."""
    user = ctx["user"]
    db = ctx["db"]

    try:
        obs_uuid = uuid.UUID(observation_id)
    except (TypeError, ValueError):
        return {"error": "invalid_observation_id", "message": "observation_id must be a UUID"}

    obs = await db.get(Observation, obs_uuid)
    if obs is None:
        return {"error": "observation_not_found", "message": "no such observation"}

    allowed = await accessible_camera_ids(user, db)
    if obs.camera_id not in allowed:
        return {"error": "camera_access_denied", "message": "no access to this camera"}

    ok, reason = await _check_user_budget(ctx)
    if not ok:
        return {"error": "budget_exceeded", "message": reason or "user budget exhausted"}

    try:
        from services.agent.analyzer import analyze_frame_target  # type: ignore
    except Exception:
        ready = _analyzer_not_ready()
        ready["frames_analyzed"] = 1 if obs.thumbnail_path else 0
        return ready

    try:
        provider_uuid = uuid.UUID(provider_id) if provider_id else None
    except (TypeError, ValueError):
        provider_uuid = None

    try:
        return await analyze_frame_target(
            ctx,
            observation_id=obs_uuid,
            question=question,
            provider_id=provider_uuid,
        )
    except Exception as exc:
        logger.exception("analyze_frame_target raised")
        return {
            "error": "analyzer_failed",
            "message": f"{type(exc).__name__}: {exc}",
            "answer": None,
            "confidence": 0.0,
            "frames_analyzed": 0,
            "cached": False,
            "cost_cents": 0,
        }


# ── Registry ─────────────────────────────────────────────────────────


ToolFn = Callable[..., Awaitable[dict]]


TOOL_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "query_observations",
        "description": (
            "Search past camera observations by text query, time window, person, "
            "and label. Returns observation rows with thumbnails, descriptions, "
            "and detected objects. Use this BEFORE running the expensive analyzer; "
            "if the user's question can be answered from indexed data, this is "
            "the cheap path."
        ),
        "input_schema": _QUERY_OBSERVATIONS_SCHEMA,
        "fn": query_observations,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "get_journeys",
        "description": (
            "List cross-camera Person sightings sessions. A Journey is a "
            "contiguous period when a Person was visible across one or more "
            "cameras. Use this to answer 'when was X around today?' or "
            "'where did the dog go?'."
        ),
        "input_schema": _GET_JOURNEYS_SCHEMA,
        "fn": get_journeys,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "get_camera_layout",
        "description": (
            "Get the list of cameras in this household, their roles "
            "(front_door, kitchen, etc.), and current online status. Call this "
            "FIRST when answering location questions so you know which cameras "
            "exist."
        ),
        "input_schema": _GET_CAMERA_LAYOUT_SCHEMA,
        "fn": get_camera_layout,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "get_last_sightings",
        "description": (
            "Return last-seen-at timestamps per named Person AND per "
            "common label (person, cat, dog, package, car, bird) across "
            "the last 30 days. Cheap. Use this when the default 24h "
            "query_observations window came back empty so you can "
            "honestly say 'I haven't seen the cat today, but I last saw "
            "it 19h ago at the back door' instead of just 'no data'. "
            "Optional person_name narrows to one Person; optional "
            "labels overrides the curated baseline set."
        ),
        "input_schema": _GET_LAST_SIGHTINGS_SCHEMA,
        "fn": get_last_sightings,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "analyze_clip",
        "description": (
            "Run a VLM against a video clip from a specific camera over a time "
            "window. Use this when the user's question requires looking at "
            "footage that wasn't pre-indexed, e.g. 'was Daddy eating?', 'is the "
            "package still there?', 'did anyone come in through the gate at "
            "2pm?'. EXPENSIVE. only call after confirming query_observations "
            "doesn't already answer the question."
        ),
        "input_schema": _ANALYZE_CLIP_SCHEMA,
        "fn": analyze_clip,
        "side_effect": "read",
        "cost_class": "expensive",
    },
    {
        "name": "analyze_frame",
        "description": (
            "Run a VLM against a single Observation's thumbnail. Cheapest VLM "
            "call. Use when you have a specific observation_id from "
            "query_observations and want to verify or extract a detail."
        ),
        "input_schema": _ANALYZE_FRAME_SCHEMA,
        "fn": analyze_frame,
        "side_effect": "read",
        "cost_class": "medium",
    },
]


_REGISTRY_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOL_REGISTRY}


def get_tool(name: str) -> dict[str, Any] | None:
    """Lookup a tool entry by name."""
    return _REGISTRY_BY_NAME.get(name)


# ── Provider dialect adapter ─────────────────────────────────────────


def _to_anthropic(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["input_schema"],
    }


def _to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


def _to_gemini(tool: dict[str, Any]) -> dict[str, Any]:
    # Gemini's functionDeclarations entry. Field naming differs from
    # OpenAI but the schema body is a JSON Schema subset, same content.
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["input_schema"],
    }


_DIALECT_ADAPTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "anthropic": _to_anthropic,
    "claude": _to_anthropic,
    "openai": _to_openai,
    "gpt": _to_openai,
    "ollama": _to_openai,  # Ollama mimics the OpenAI tool-use schema
    "google": _to_gemini,
    "gemini": _to_gemini,
}


def all_tools_for_provider(provider_kind: str) -> list[dict[str, Any]]:
    """Return the tool registry serialized to the given provider's
    tool-use schema dialect. Wave 2 driver feeds the output straight
    into the provider's request body."""
    kind = (provider_kind or "").lower()
    adapter = _DIALECT_ADAPTERS.get(kind)
    if adapter is None:
        raise ValueError(f"unsupported provider_kind: {provider_kind!r}")
    return [adapter(t) for t in TOOL_REGISTRY]
