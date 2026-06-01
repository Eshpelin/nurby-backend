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
from shared.models import Camera, Event, Journey, Observation, Person, Rule

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

    # ── Subject resolution ──────────────────────────────────────────
    # Person journeys key ``subject_key`` by a comma-joined,
    # alphabetically-sorted set of display names (see
    # incident_tracker.compute_signature), NOT by person_id. So a
    # person filter resolves to a display name and matches the
    # name-signature, not a UUID.
    resolved_name: str | None = None
    if person_id:
        try:
            pid = uuid.UUID(person_id)
        except (TypeError, ValueError):
            return {"journeys": [], "error": "invalid person_id"}
        resolved_name = (
            await db.execute(select(Person.display_name).where(Person.id == pid))
        ).scalars().first()
        if not resolved_name:
            return {"journeys": [], "error": "person not found"}
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
        resolved_name = person_rows[0][1]

    filters = [
        Journey.subject_kind == "person",
        Journey.last_seen_at >= cutoff,
    ]
    # Coarse SQL prefilter on the name-signature. The precise
    # exact-token check happens in Python below so "Ann" does not match
    # a journey for "Anna".
    if resolved_name is not None:
        filters.append(Journey.subject_key.ilike(f"%{resolved_name}%"))

    # When name-filtering, overfetch so the Python token filter still
    # has enough candidates after dropping coincidental substring hits.
    fetch_limit = max(limit, 200) if resolved_name is not None else limit
    stmt = (
        select(Journey)
        .where(and_(*filters))
        .order_by(Journey.last_seen_at.desc())
        .limit(fetch_limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())

    def _names(subject_key: str | None) -> list[str]:
        return [n.strip() for n in (subject_key or "").split(",") if n.strip()]

    # Precise exact-token filter. subject_key is comma-joined names; the
    # person matches only if their exact display name is one of them.
    if resolved_name is not None:
        rows = [j for j in rows if resolved_name in _names(j.subject_key)][:limit]
    else:
        rows = rows[:limit]

    # Resolve person_id for single-name journeys so the agent can cite
    # one. Multi-person journeys keep person_id null but list the names.
    single_names: set[str] = set()
    for j in rows:
        ns = _names(j.subject_key)
        if len(ns) == 1:
            single_names.add(ns[0])
    name_to_id: dict[str, str] = {}
    if single_names:
        for pid, dname in (
            await db.execute(
                select(Person.id, Person.display_name).where(
                    Person.display_name.in_(single_names)
                )
            )
        ).all():
            name_to_id[dname] = str(pid)

    # Resolve thumbnails from each journey's peak observation.
    peak_ids: set[uuid.UUID] = set()
    for j in rows:
        for seg in j.segments or []:
            if isinstance(seg, dict):
                poid = seg.get("peak_observation_id")
                if poid:
                    try:
                        peak_ids.add(uuid.UUID(poid))
                    except (TypeError, ValueError):
                        continue
    thumb_by_obs: dict[uuid.UUID, str] = {}
    if peak_ids:
        for oid, tpath in (
            await db.execute(
                select(Observation.id, Observation.thumbnail_path).where(
                    Observation.id.in_(peak_ids)
                )
            )
        ).all():
            if tpath:
                thumb_by_obs[oid] = tpath

    journeys_out: list[dict[str, Any]] = []
    for j in rows:
        # Filter segments to accessible cameras and skip the journey if
        # nothing remains. Segments already carry camera_name +
        # occurrence_count (see journey_tracker._segment).
        seg_cams: list[dict[str, str]] = []
        seen: set[uuid.UUID] = set()
        observation_count = 0
        first_thumb: str | None = None
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
                    {"id": str(cid), "name": seg.get("camera_name") or "Unknown"}
                )
                seen.add(cid)
            observation_count += int(seg.get("occurrence_count") or 0)
            if first_thumb is None:
                poid = seg.get("peak_observation_id")
                if poid:
                    try:
                        first_thumb = thumb_by_obs.get(uuid.UUID(poid))
                    except (TypeError, ValueError):
                        first_thumb = None
        if not seg_cams:
            continue

        ns = _names(j.subject_key)
        person_id_out = name_to_id.get(ns[0]) if len(ns) == 1 else None

        started = j.started_at
        ended = j.ended_at or j.last_seen_at
        duration_s = int((ended - started).total_seconds()) if started and ended else None

        journeys_out.append(
            {
                "id": str(j.id),
                "person_id": person_id_out,
                "person_name": j.subject_key,
                "person_names": ns,
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "duration_seconds": duration_s,
                "cameras": seg_cams,
                "observation_count": observation_count,
                "thumbnail_url": _thumbnail_url(first_thumb),
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


# ── Tool 3a. get_household_snapshot ───────────────────────────────────


_GET_HOUSEHOLD_SNAPSHOT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}


async def get_household_snapshot(ctx: dict) -> dict:
    """Cheap one-call orientation primer. Returns:
       - cameras with last-observation timestamp,
       - named Persons with their last sighting,
       - currently open Journeys (still in progress).

    Designed to be the LLM's FIRST call on most questions so it has
    enough state to ask a sensible follow-up tool call instead of
    blind window-widening.
    """
    user = ctx["user"]
    db = ctx["db"]

    allowed = await accessible_camera_ids(user, db)
    if not allowed:
        return {
            "cameras": [],
            "persons": [],
            "active_journeys": [],
            "now_iso": datetime.now(timezone.utc).isoformat(),
        }

    now = datetime.now(timezone.utc)

    # Cameras + their most-recent observation.
    cam_rows = (
        await db.execute(
            select(Camera)
            .where(Camera.id.in_(allowed))
            .order_by(Camera.display_order, Camera.created_at)
        )
    ).scalars().all()
    cameras: list[dict] = []
    for c in cam_rows:
        last_obs = (
            await db.execute(
                select(Observation.started_at, Observation.id)
                .where(Observation.camera_id == c.id)
                .order_by(Observation.started_at.desc())
                .limit(1)
            )
        ).first()
        last_ts = last_obs[0] if last_obs else None
        cameras.append(
            {
                "id": str(c.id),
                "name": c.name,
                "role": _infer_role(c.name, c.location_label),
                "status": c.status,
                "last_observation_at": last_ts.isoformat() if last_ts else None,
                "last_observation_id": str(last_obs[1]) if last_obs else None,
                "minutes_since_last": int((now - last_ts).total_seconds() // 60)
                if last_ts
                else None,
            }
        )

    # Named Persons + their most-recent Journey.
    person_rows = (await db.execute(select(Person).order_by(Person.display_name))).scalars().all()
    persons: list[dict] = []
    for p in person_rows:
        j = (
            await db.execute(
                select(Journey)
                .where(Journey.person_id == p.id)
                .order_by(Journey.last_seen_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        persons.append(
            {
                "person_id": str(p.id),
                "display_name": p.display_name,
                "relationship": p.relationship,
                "last_seen_at": j.last_seen_at.isoformat() if j and j.last_seen_at else None,
                "last_journey_id": str(j.id) if j else None,
                "hours_since_seen": int((now - j.last_seen_at).total_seconds() // 3600)
                if j and j.last_seen_at
                else None,
            }
        )

    # Currently open Journeys (ended_at IS NULL OR last_seen_at within 5m).
    open_window = now - timedelta(minutes=5)
    active = (
        await db.execute(
            select(Journey)
            .where(Journey.last_seen_at >= open_window)
            .order_by(Journey.last_seen_at.desc())
            .limit(20)
        )
    ).scalars().all()
    active_journeys: list[dict] = []
    for j in active:
        # Only surface if it touches an accessible camera.
        segs = j.cameras or []
        visible = [s for s in segs if uuid.UUID(s.get("id")) in allowed] if segs else []
        if not visible and segs:
            continue
        active_journeys.append(
            {
                "journey_id": str(j.id),
                "person_id": str(j.person_id) if j.person_id else None,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "last_seen_at": j.last_seen_at.isoformat() if j.last_seen_at else None,
                "cameras": [{"id": s.get("id"), "name": s.get("name")} for s in (visible or segs)],
            }
        )

    return {
        "now_iso": now.isoformat(),
        "cameras": cameras,
        "persons": persons,
        "active_journeys": active_journeys,
    }


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

    # ── Label-side lookup. uses Observation.object_detections JSON ──────────
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
                .where(Observation.started_at >= cutoff)
                .where(cast(Observation.object_detections, SAString).ilike(needle))
                .order_by(Observation.started_at.desc())
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


# ── Tool 3c. get_events ───────────────────────────────────────────────


_GET_EVENTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hours": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_WINDOW_HOURS,
            "default": 24,
            "description": "Look-back window. Defaults 24h.",
        },
        "rule_ids": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
            "maxItems": 50,
        },
        "rule_name_contains": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": "Case-insensitive substring match on rule name.",
        },
        "action_status": {
            "type": "string",
            "enum": ["pending", "success", "failed", "skipped"],
        },
        "include_payload": {
            "type": "boolean",
            "default": False,
            "description": "Include the per-event payload dict. Off by default to keep responses small; turn on when you need camera_id or detection labels.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "default": 100,
        },
    },
}


async def get_events(
    ctx: dict,
    hours: int = 24,
    rule_ids: list[str] | None = None,
    rule_name_contains: str | None = None,
    action_status: str | None = None,
    include_payload: bool = False,
    limit: int = 100,
) -> dict:
    """List rule firings (events) over a time window.

    A rule firing is the strongest evidence Nurby has that something
    happened. If a rule "cat eating" fired 7 times today, that's 7
    confirmed feedings; you do NOT need to re-analyze frames to count
    them. Use this BEFORE analyze_clip for any question shaped like
    "how many times did X happen", "when did rule Y fire", or "did
    rule Z fire today".
    """
    db = ctx["db"]
    user = ctx["user"]

    hours = _clamp_hours(hours)
    limit = _clamp_limit(limit, default=100, max_=200)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    allowed = await accessible_camera_ids(user, db)

    # Resolve rule_ids from name substring if needed.
    target_rule_ids: set[uuid.UUID] = set()
    if rule_ids:
        target_rule_ids = _to_uuid_set(rule_ids)
    if rule_name_contains:
        needle = f"%{rule_name_contains.strip().lower()}%"
        named = (
            await db.execute(
                select(Rule.id).where(func.lower(Rule.name).like(needle))
            )
        ).scalars().all()
        target_rule_ids.update(named)
        if not named and not rule_ids:
            # User asked by name and we found no match. Return empty
            # without scanning events.
            return {
                "count": 0,
                "events": [],
                "hours": hours,
                "filter": {"rule_name_contains": rule_name_contains},
            }

    stmt = (
        select(Event, Rule)
        .join(Rule, Rule.id == Event.rule_id, isouter=True)
        .where(Event.fired_at >= cutoff)
    )
    if target_rule_ids:
        stmt = stmt.where(Event.rule_id.in_(target_rule_ids))
    if action_status:
        stmt = stmt.where(Event.action_status == action_status)
    stmt = stmt.order_by(Event.fired_at.desc()).limit(limit)

    rows = (await db.execute(stmt)).all()
    out: list[dict] = []
    for ev, rule in rows:
        # Respect camera ACL. If the event's payload references a
        # camera_id the user can't see, hide it. Events without a
        # camera_id in payload (rare) pass through.
        payload = ev.payload or {}
        cam_id_raw = payload.get("camera_id")
        if cam_id_raw:
            try:
                if uuid.UUID(str(cam_id_raw)) not in allowed:
                    continue
            except (ValueError, TypeError):
                pass
        item = {
            "event_id": str(ev.id),
            "rule_id": str(ev.rule_id) if ev.rule_id else None,
            "rule_name": rule.name if rule else None,
            "fired_at": ev.fired_at.isoformat() if ev.fired_at else None,
            "action_type": ev.action_type,
            "action_status": ev.action_status,
            "acked_at": ev.acked_at.isoformat() if ev.acked_at else None,
            "observation_id": str(ev.observation_id) if ev.observation_id else None,
        }
        if include_payload:
            item["payload"] = payload
        out.append(item)

    return {
        "count": len(out),
        "events": out,
        "hours": hours,
        "filter": {
            "rule_ids": [str(r) for r in target_rule_ids] if target_rule_ids else None,
            "rule_name_contains": rule_name_contains,
            "action_status": action_status,
        },
    }


# ── Tool 3d. summarize_activity ───────────────────────────────────────


_SUMMARIZE_ACTIVITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hours": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_WINDOW_HOURS,
            "default": 24,
            "description": "Look-back window for the rollup. Defaults 24h.",
        },
    },
}


async def summarize_activity(ctx: dict, hours: int = 24) -> dict:
    """Pre-aggregated 'what happened today?' rollup. One call returns.

    - per-Person sightings counts + first/last seen + camera path
      (from Journeys, the cross-camera identity story already curated
      by the perception pipeline);
    - per-rule firing counts (from Events, semantic facts the rule
      engine already confirmed);
    - per-label observation counts and totals (cat, dog, person, etc);
    - camera activity ranking (most active first).

    Designed for narrative questions. 'what happened in the house
    today?', 'give me a 24-hour recap'. The LLM should call this first
    for any such question before issuing per-entity queries; the rollup
    is one DB round-trip and answers most of the question on its own.
    """
    db = ctx["db"]
    user = ctx["user"]

    hours = _clamp_hours(hours)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    now = datetime.now(timezone.utc)
    allowed = await accessible_camera_ids(user, db)

    # ── per-Person Journey rollup ───────────────────────────────────
    persons_block: list[dict] = []
    if allowed:
        rs = await db.execute(select(Person).order_by(Person.display_name))
        for p in rs.scalars().all():
            j_rows = (
                await db.execute(
                    select(Journey)
                    .where(Journey.person_id == p.id)
                    .where(Journey.last_seen_at >= cutoff)
                    .order_by(Journey.started_at.asc())
                )
            ).scalars().all()
            if not j_rows:
                continue
            # Coalesce camera names visible to this user.
            cams_seen: list[str] = []
            seg_count = 0
            for j in j_rows:
                for seg in j.cameras or []:
                    try:
                        cid = uuid.UUID(seg.get("id"))
                    except (ValueError, TypeError):
                        continue
                    if cid in allowed and seg.get("name") not in cams_seen:
                        cams_seen.append(seg.get("name"))
                seg_count += int(getattr(j, "observation_count", 0) or 0)
            persons_block.append(
                {
                    "person_id": str(p.id),
                    "display_name": p.display_name,
                    "sighting_count": len(j_rows),
                    "observation_count": seg_count,
                    "first_seen_at": j_rows[0].started_at.isoformat()
                    if j_rows[0].started_at
                    else None,
                    "last_seen_at": j_rows[-1].last_seen_at.isoformat()
                    if j_rows[-1].last_seen_at
                    else None,
                    "cameras": cams_seen,
                }
            )

    # ── per-rule Event rollup ───────────────────────────────────────
    rules_block: list[dict] = []
    ev_rows = (
        await db.execute(
            select(Event, Rule)
            .join(Rule, Rule.id == Event.rule_id, isouter=True)
            .where(Event.fired_at >= cutoff)
        )
    ).all()
    by_rule: dict[uuid.UUID, dict] = {}
    for ev, rule in ev_rows:
        # Skip events tied to cameras the user can't see.
        payload = ev.payload or {}
        cam_raw = payload.get("camera_id")
        if cam_raw:
            try:
                if uuid.UUID(str(cam_raw)) not in allowed:
                    continue
            except (ValueError, TypeError):
                pass
        rid = ev.rule_id
        if rid is None:
            continue
        bucket = by_rule.setdefault(
            rid,
            {
                "rule_id": str(rid),
                "rule_name": rule.name if rule else None,
                "firing_count": 0,
                "first_fired_at": None,
                "last_fired_at": None,
                "success_count": 0,
                "failed_count": 0,
            },
        )
        bucket["firing_count"] += 1
        ts = ev.fired_at
        if ts:
            ts_iso = ts.isoformat()
            if not bucket["first_fired_at"] or ts_iso < bucket["first_fired_at"]:
                bucket["first_fired_at"] = ts_iso
            if not bucket["last_fired_at"] or ts_iso > bucket["last_fired_at"]:
                bucket["last_fired_at"] = ts_iso
        if ev.action_status == "success":
            bucket["success_count"] += 1
        elif ev.action_status == "failed":
            bucket["failed_count"] += 1
    rules_block = sorted(by_rule.values(), key=lambda r: -r["firing_count"])

    # ── per-label + per-camera observation rollup ──────────────────
    labels_block: dict[str, dict] = {}
    cameras_block: dict[str, dict] = {}
    cam_rows = (
        await db.execute(
            select(Camera).where(Camera.id.in_(allowed)) if allowed else select(Camera).where(False)
        )
    ).scalars().all()
    cam_name_by_id = {c.id: c.name for c in cam_rows}

    obs_rows = (
        await db.execute(
            select(
                Observation.id,
                Observation.camera_id,
                Observation.started_at,
                Observation.object_detections,
                Observation.vlm_description,
                Observation.vlm_late,
            )
            .where(Observation.started_at >= cutoff)
            .where(Observation.camera_id.in_(allowed) if allowed else False)
        )
    ).all() if allowed else []

    # VLM backlog tally. observations missing a vlm_description are
    # still pending on the worker. Observations that were patched late
    # are counted so the answer can honestly say how behind we are.
    vlm_pending = 0
    vlm_late_count = 0

    for obs_id, cam_id, ts, det, vlm_desc, _vlm_late in obs_rows:
        if not vlm_desc:
            vlm_pending += 1
        if _vlm_late:
            vlm_late_count += 1
        cb = cameras_block.setdefault(
            str(cam_id),
            {
                "camera_id": str(cam_id),
                "camera_name": cam_name_by_id.get(cam_id),
                "observation_count": 0,
                "first_observation_at": None,
                "last_observation_at": None,
            },
        )
        cb["observation_count"] += 1
        ts_iso = ts.isoformat() if ts else None
        if ts_iso:
            if not cb["first_observation_at"] or ts_iso < cb["first_observation_at"]:
                cb["first_observation_at"] = ts_iso
            if not cb["last_observation_at"] or ts_iso > cb["last_observation_at"]:
                cb["last_observation_at"] = ts_iso

        # Aggregate labels found in detections JSON. Detection format
        # varies a bit across the pipeline (legacy dict vs list of
        # objects); be defensive about parsing.
        items = det if isinstance(det, list) else (det or {}).get("objects") if isinstance(det, dict) else None
        if isinstance(items, list):
            seen_in_obs: set[str] = set()
            for obj in items:
                if not isinstance(obj, dict):
                    continue
                lab = obj.get("label")
                if not lab or lab in seen_in_obs:
                    continue
                seen_in_obs.add(lab)
                lb = labels_block.setdefault(
                    lab,
                    {"label": lab, "observation_count": 0, "last_seen_at": None},
                )
                lb["observation_count"] += 1
                if ts_iso and (not lb["last_seen_at"] or ts_iso > lb["last_seen_at"]):
                    lb["last_seen_at"] = ts_iso

    return {
        "hours": hours,
        "now_iso": now.isoformat(),
        "persons": persons_block,
        "rules_fired": rules_block,
        "labels": sorted(labels_block.values(), key=lambda x: -x["observation_count"]),
        "cameras": sorted(cameras_block.values(), key=lambda x: -x["observation_count"]),
        "totals": {
            "observations": sum(c["observation_count"] for c in cameras_block.values()),
            "persons_seen": len(persons_block),
            "rules_fired": sum(r["firing_count"] for r in rules_block),
            "unique_labels": len(labels_block),
            "vlm_pending": vlm_pending,
            "vlm_late": vlm_late_count,
        },
    }


# ── Tool 3e. query_relationships ──────────────────────────────────────


# Labels we accept as object-style subjects/objects. Matches the YOLO
# taxonomy the incident tracker writes into Journey.subject_key when
# subject_kind == "object" (see services/perception/incident_tracker.py
# compute_signature) and the labels get_last_sightings baselines on.
_KNOWN_LABELS = (
    "person",
    "cat",
    "dog",
    "car",
    "truck",
    "bus",
    "bicycle",
    "motorcycle",
    "package",
    "bird",
    "backpack",
    "handbag",
    "suitcase",
    "umbrella",
)

_REVISIT_GAP_SECONDS = 30 * 60  # 30 min between journeys counts as a return

_RELATIONS = (
    "co_present_with",
    "revisited",
    "path",
    "seen_with_label",
    "transitions",
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


_QUERY_RELATIONSHIPS_SCHEMA = {
    "type": "object",
    "required": ["subject", "relation"],
    "additionalProperties": False,
    "properties": {
        "subject": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": (
                "Person display_name (case-insensitive substring), a "
                "person_id UUID, or a label like 'cat' / 'car' / "
                "'package'."
            ),
        },
        "relation": {
            "type": "string",
            "enum": list(_RELATIONS),
            "description": (
                "co_present_with (who/what overlapped subject on the same "
                "camera), revisited (subject came back after a >30min "
                "gap), path (ordered camera transitions of the subject's "
                "most recent journey), seen_with_label (a label detected "
                "during the subject's journey windows), transitions (all "
                "camera-to-camera movement gaps in the window)."
            ),
        },
        "object": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": (
                "Optional second party. For co_present_with, restrict to "
                "this person/label. For seen_with_label, the label to look "
                "for (e.g. 'dog'). Ignored by other relations."
            ),
        },
        "hours": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_WINDOW_HOURS,
            "default": 168,
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
    },
}


def _looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value.strip()))


def _seg_cam_ids(journey: Any, allowed: set[uuid.UUID]) -> set[uuid.UUID]:
    """Accessible camera UUIDs touched by a journey's segments."""
    out: set[uuid.UUID] = set()
    for seg in journey.segments or []:
        if not isinstance(seg, dict):
            continue
        cid = seg.get("camera_id")
        if not cid:
            continue
        try:
            cu = uuid.UUID(cid)
        except (TypeError, ValueError):
            continue
        if cu in allowed:
            out.add(cu)
    return out


def _journey_window(journey: Any) -> tuple[datetime | None, datetime | None]:
    start = journey.started_at
    end = journey.ended_at or journey.last_seen_at
    return start, end


def _windows_overlap(
    a_start: datetime | None,
    a_end: datetime | None,
    b_start: datetime | None,
    b_end: datetime | None,
) -> bool:
    if not (a_start and a_end and b_start and b_end):
        return False
    return a_start < b_end and a_end > b_start


async def _resolve_subject(
    subject: str, db: Any
) -> dict[str, Any]:
    """Resolve a subject string to a (kind, key-set) descriptor.

    Returns one of.
      {"type": "person", "person_id": uuid, "display_name": str}
      {"type": "label", "label": str}
      {"type": "disambiguation", "candidates": [...]}
      {"type": "unresolved"}

    Person journeys are keyed by display_name(s) in Journey.subject_key
    (the incident tracker joins person_name values, NOT the person_id),
    so we resolve a Person row to get its display_name and match journeys
    on subject_kind == 'person' + name-in-subject_key downstream.
    """
    subj = subject.strip()
    if not subj:
        return {"type": "unresolved"}

    # UUID -> a Person row by id.
    if _looks_like_uuid(subj):
        try:
            p = await db.get(Person, uuid.UUID(subj))
        except Exception:
            p = None
        if p is not None:
            return {
                "type": "person",
                "person_id": p.id,
                "display_name": p.display_name,
            }
        # An unknown UUID. nothing to resolve.
        return {"type": "unresolved"}

    # Person display_name substring match.
    like = f"%{subj.lower()}%"
    person_rows = (
        await db.execute(
            select(Person.id, Person.display_name).where(
                func.lower(Person.display_name).like(like)
            )
        )
    ).all()
    if len(person_rows) > 1:
        return {
            "type": "disambiguation",
            "candidates": [
                {"person_id": str(pid), "display_name": dname}
                for pid, dname in person_rows
            ],
        }
    if len(person_rows) == 1:
        return {
            "type": "person",
            "person_id": person_rows[0][0],
            "display_name": person_rows[0][1],
        }

    # No Person matched. Treat a known label word as an object subject.
    if subj.lower() in _KNOWN_LABELS:
        return {"type": "label", "label": subj.lower()}

    return {"type": "unresolved"}


def _subject_journey_filter(subject: dict[str, Any]) -> list[Any]:
    """SQLAlchemy filters selecting the journeys that belong to a
    resolved subject. Person journeys match by display_name inside the
    comma-joined subject_key; label journeys match subject_kind=='object'
    with the label inside subject_key."""
    if subject["type"] == "person":
        return [
            Journey.subject_kind == "person",
            cast(Journey.subject_key, SAString).ilike(
                f"%{subject['display_name']}%"
            ),
        ]
    if subject["type"] == "label":
        return [
            Journey.subject_kind == "object",
            cast(Journey.subject_key, SAString).ilike(f"%{subject['label']}%"),
        ]
    return [select(Journey.id).where(False)]  # never matches


def _subject_echo(subject: dict[str, Any]) -> dict[str, Any]:
    if subject["type"] == "person":
        return {
            "type": "person",
            "person_id": str(subject["person_id"]),
            "display_name": subject["display_name"],
        }
    if subject["type"] == "label":
        return {"type": "label", "label": subject["label"]}
    return {"type": "unresolved"}


async def query_relationships(
    ctx: dict,
    *,
    subject: str,
    relation: str,
    object: str | None = None,
    hours: int = 168,
    limit: int = 50,
) -> dict:
    """Walk relationships between people, animals, vehicles, cameras, and
    time over the existing Journey graph (foreign keys + segments /
    transitions JSON). One DB pass, no new tables.

    All results are filtered through accessible_camera_ids. When the
    subject name resolves to more than one Person the response carries a
    ``disambiguation`` block and an empty ``results`` list, matching the
    get_journeys convention.
    """
    user = ctx["user"]
    db = ctx["db"]

    hours = _clamp_hours(hours)
    limit = _clamp_limit(limit, default=50, max_=100)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if relation not in _RELATIONS:
        return {
            "relation": relation,
            "subject": {"type": "unresolved"},
            "hours": hours,
            "results": [],
            "error": "unknown_relation",
        }

    allowed = await accessible_camera_ids(user, db)
    base = {"relation": relation, "hours": hours}
    if not allowed:
        return {**base, "subject": {"type": "unresolved"}, "results": []}

    subject_desc = await _resolve_subject(subject, db)
    if subject_desc["type"] == "disambiguation":
        return {
            **base,
            "subject": {"type": "person_ambiguous"},
            "results": [],
            "disambiguation": subject_desc["candidates"],
        }
    if subject_desc["type"] == "unresolved":
        return {**base, "subject": {"type": "unresolved"}, "results": []}

    subj_echo = _subject_echo(subject_desc)

    # Load the subject's journeys in the window once. Every relation but
    # `transitions` is grounded on this set.
    subj_filters = _subject_journey_filter(subject_desc) + [
        Journey.last_seen_at >= cutoff
    ]
    subj_journeys = (
        await db.execute(
            select(Journey)
            .where(and_(*subj_filters))
            .order_by(Journey.last_seen_at.desc())
            .limit(200)
        )
    ).scalars().all()
    # Keep only journeys that touch at least one accessible camera.
    subj_journeys = [j for j in subj_journeys if _seg_cam_ids(j, allowed)]

    # Exact-token guard. ``subject_key`` is a comma-joined set of names
    # (persons) or labels (objects). The SQL ilike above is a coarse
    # prefilter; require an exact member match here so "Ann" does not
    # match a journey for "Anna", and "car" does not match "carriage".
    if subject_desc["type"] == "person":
        token = subject_desc["display_name"]
    elif subject_desc["type"] == "label":
        token = subject_desc["label"]
    else:
        token = None
    if token is not None:
        subj_journeys = [
            j
            for j in subj_journeys
            if token in [n.strip() for n in (j.subject_key or "").split(",")]
        ]

    if relation == "co_present_with":
        results = await _rel_co_present(
            db, subject_desc, subj_journeys, allowed, cutoff, object, limit
        )
    elif relation == "revisited":
        results = _rel_revisited(subj_journeys, allowed, limit)
    elif relation == "path":
        results = _rel_path(subj_journeys, allowed, limit)
    elif relation == "seen_with_label":
        results = await _rel_seen_with_label(
            db, subj_journeys, allowed, object, limit
        )
    else:  # transitions
        results = await _rel_transitions(db, allowed, cutoff, limit)

    return {**base, "subject": subj_echo, "results": results}


async def _rel_co_present(
    db: Any,
    subject_desc: dict[str, Any],
    subj_journeys: list[Any],
    allowed: set[uuid.UUID],
    cutoff: datetime,
    object_filter: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Other subjects whose journey windows overlap the subject's
    journeys on the SAME camera."""
    if not subj_journeys:
        return []

    # Precompute subject (window, camera-set) tuples.
    subj_spans: list[tuple[datetime, datetime, set[uuid.UUID]]] = []
    subj_keys: set[str] = set()
    for j in subj_journeys:
        s, e = _journey_window(j)
        if s and e:
            subj_spans.append((s, e, _seg_cam_ids(j, allowed)))
        subj_keys.add(j.subject_key)

    # Candidate journeys. everything else in the window. We over-fetch
    # then filter in Python for the overlap + shared-camera test.
    others = (
        await db.execute(
            select(Journey)
            .where(Journey.last_seen_at >= cutoff)
            .order_by(Journey.last_seen_at.desc())
            .limit(500)
        )
    ).scalars().all()

    obj_needle = object_filter.strip().lower() if object_filter else None

    out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for o in others:
        if o.subject_key in subj_keys:
            continue  # the subject themselves
        if obj_needle and obj_needle not in (o.subject_key or "").lower():
            continue
        o_cams = _seg_cam_ids(o, allowed)
        if not o_cams:
            continue
        o_start, o_end = _journey_window(o)
        shared: set[uuid.UUID] | None = None
        for (s, e, cams) in subj_spans:
            if _windows_overlap(s, e, o_start, o_end) and (cams & o_cams):
                shared = cams & o_cams
                break
        if shared is None:
            continue
        if o.subject_key in seen_keys:
            continue
        seen_keys.add(o.subject_key)
        out.append(
            {
                "journey_id": str(o.id),
                "subject_kind": o.subject_kind,
                "subject_key": o.subject_key,
                "person_id": str(o.person_id)
                if getattr(o, "person_id", None)
                else None,
                "overlap_cameras": [
                    {"id": str(cid), "name": _seg_cam_name(o, cid)}
                    for cid in shared
                ],
                "started_at": o.started_at.isoformat() if o.started_at else None,
                "last_seen_at": o.last_seen_at.isoformat()
                if o.last_seen_at
                else None,
            }
        )
        if len(out) >= limit:
            break
    return out


def _seg_cam_name(journey: Any, cam_id: uuid.UUID) -> str | None:
    for seg in journey.segments or []:
        if isinstance(seg, dict) and seg.get("camera_id") == str(cam_id):
            return seg.get("camera_name")
    return None


def _rel_revisited(
    subj_journeys: list[Any], allowed: set[uuid.UUID], limit: int
) -> list[dict[str, Any]]:
    """Same subject_key appearing in 2+ journeys separated by a >30min
    gap. Works on body-cluster / object subjects without a face."""
    by_key: dict[str, list[Any]] = {}
    for j in subj_journeys:
        by_key.setdefault(j.subject_key, []).append(j)

    out: list[dict[str, Any]] = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda j: j.started_at or datetime.min.replace(tzinfo=timezone.utc))
        for i in range(1, len(ordered)):
            prev = ordered[i - 1]
            cur = ordered[i]
            prev_end = prev.ended_at or prev.last_seen_at
            cur_start = cur.started_at
            if not (prev_end and cur_start):
                continue
            gap = int((cur_start - prev_end).total_seconds())
            if gap < _REVISIT_GAP_SECONDS:
                continue
            out.append(
                {
                    "subject_key": key,
                    "subject_kind": cur.subject_kind,
                    "gap_seconds": gap,
                    "gap_minutes": round(gap / 60),
                    "first_journey_id": str(prev.id),
                    "first_left_at": prev_end.isoformat(),
                    "return_journey_id": str(cur.id),
                    "returned_at": cur_start.isoformat(),
                    "return_cameras": [
                        {"id": str(cid), "name": _seg_cam_name(cur, cid)}
                        for cid in _seg_cam_ids(cur, allowed)
                    ],
                }
            )
            if len(out) >= limit:
                return out
    return out


def _rel_path(
    subj_journeys: list[Any], allowed: set[uuid.UUID], limit: int
) -> list[dict[str, Any]]:
    """Ordered camera transitions for the subject's most recent journey,
    read from Journey.transitions, falling back to segment order."""
    if not subj_journeys:
        return []
    j = subj_journeys[0]  # already ordered last_seen_at desc

    out: list[dict[str, Any]] = []
    transitions = j.transitions or []
    for t in transitions:
        if not isinstance(t, dict):
            continue
        from_cid = t.get("from_camera_id")
        to_cid = t.get("to_camera_id")
        # ACL. only surface a hop when both endpoints are accessible.
        try:
            if from_cid and uuid.UUID(from_cid) not in allowed:
                continue
            if to_cid and uuid.UUID(to_cid) not in allowed:
                continue
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "journey_id": str(j.id),
                "from_camera_id": from_cid,
                "from_camera_name": t.get("from_camera_name"),
                "to_camera_id": to_cid,
                "to_camera_name": t.get("to_camera_name"),
                "gap_seconds": t.get("gap_seconds"),
                "at": t.get("ts"),
            }
        )
        if len(out) >= limit:
            break

    if not out:
        # No transition rows (single-camera journey). Surface the ordered
        # segment cameras so the agent still gets the path shape.
        for seg in j.segments or []:
            if not isinstance(seg, dict):
                continue
            cid = seg.get("camera_id")
            try:
                if cid and uuid.UUID(cid) not in allowed:
                    continue
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "journey_id": str(j.id),
                    "camera_id": cid,
                    "camera_name": seg.get("camera_name"),
                    "started_at": seg.get("started_at"),
                    "last_seen_at": seg.get("last_seen_at"),
                }
            )
            if len(out) >= limit:
                break
    return out


async def _rel_seen_with_label(
    db: Any,
    subj_journeys: list[Any],
    allowed: set[uuid.UUID],
    label: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Observations carrying ``label`` in object_detections that fall
    inside one of the subject's journey windows on a shared camera."""
    if not label or not subj_journeys:
        return []
    needle = label.strip()
    if not needle:
        return []

    out: list[dict[str, Any]] = []
    seen_obs: set[str] = set()
    for j in subj_journeys:
        j_start, j_end = _journey_window(j)
        if not (j_start and j_end):
            continue
        cams = _seg_cam_ids(j, allowed)
        if not cams:
            continue
        # Same JSON label-in-detections filter get_last_sightings uses,
        # over Observation.object_detections (the real column name).
        rows = (
            await db.execute(
                select(Observation)
                .where(Observation.camera_id.in_(cams))
                .where(Observation.started_at >= j_start)
                .where(Observation.started_at <= j_end)
                .where(
                    cast(Observation.object_detections, SAString).ilike(
                        f'%"label": "{needle}"%'
                    )
                )
                .order_by(Observation.started_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        for obs in rows:
            if str(obs.id) in seen_obs:
                continue
            if obs.camera_id not in allowed:
                continue
            seen_obs.add(str(obs.id))
            out.append(
                {
                    "observation_id": str(obs.id),
                    "journey_id": str(j.id),
                    "label": needle,
                    "camera_id": str(obs.camera_id),
                    "camera_name": _seg_cam_name(j, obs.camera_id),
                    "timestamp": obs.started_at.isoformat()
                    if obs.started_at
                    else None,
                    "description": obs.vlm_description,
                    "thumbnail_url": _thumbnail_url(obs.thumbnail_path),
                }
            )
            if len(out) >= limit:
                return out
    return out


async def _rel_transitions(
    db: Any, allowed: set[uuid.UUID], cutoff: datetime, limit: int
) -> list[dict[str, Any]]:
    """Aggregate of all camera-to-camera movement gaps across journeys
    in the window. Answers 'what's the usual path through the house?'."""
    journeys = (
        await db.execute(
            select(Journey)
            .where(Journey.last_seen_at >= cutoff)
            .order_by(Journey.last_seen_at.desc())
            .limit(1000)
        )
    ).scalars().all()

    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for j in journeys:
        for t in j.transitions or []:
            if not isinstance(t, dict):
                continue
            from_cid = t.get("from_camera_id")
            to_cid = t.get("to_camera_id")
            if not (from_cid and to_cid):
                continue
            try:
                if uuid.UUID(from_cid) not in allowed:
                    continue
                if uuid.UUID(to_cid) not in allowed:
                    continue
            except (TypeError, ValueError):
                continue
            k = (from_cid, to_cid)
            bucket = agg.setdefault(
                k,
                {
                    "from_camera_id": from_cid,
                    "from_camera_name": t.get("from_camera_name"),
                    "to_camera_id": to_cid,
                    "to_camera_name": t.get("to_camera_name"),
                    "count": 0,
                    "_gap_total": 0,
                },
            )
            bucket["count"] += 1
            bucket["_gap_total"] += int(t.get("gap_seconds") or 0)

    out: list[dict[str, Any]] = []
    for bucket in sorted(agg.values(), key=lambda b: -b["count"]):
        cnt = bucket["count"]
        out.append(
            {
                "from_camera_id": bucket["from_camera_id"],
                "from_camera_name": bucket["from_camera_name"],
                "to_camera_id": bucket["to_camera_id"],
                "to_camera_name": bucket["to_camera_name"],
                "count": cnt,
                "avg_gap_seconds": round(bucket["_gap_total"] / cnt) if cnt else 0,
            }
        )
        if len(out) >= limit:
            break
    return out


# ── Tool 3f. summarize_window (map-reduce long-window summary) ────────


_SUMMARIZE_WINDOW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hours": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_WINDOW_HOURS,
            "default": 168,
            "description": (
                "Look-back window in hours. Default 168 (7 days), max 720 "
                "(30 days)."
            ),
        },
        "focus": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": (
                "Optional free-text topic the final narrative weights "
                "toward, e.g. 'front door' or 'the dog'."
            ),
        },
        "chunk_by": {
            "type": "string",
            "enum": ["auto", "hour", "day", "incident", "journey"],
            "default": "auto",
            "description": (
                "Chunk boundary strategy. auto picks hourly buckets for "
                "windows <=48h and daily buckets for longer windows."
            ),
        },
        "provider_id": {"type": "string", "format": "uuid"},
    },
}


async def summarize_window(
    ctx: dict,
    *,
    hours: int = 168,
    focus: str | None = None,
    chunk_by: str = "auto",
    provider_id: str | None = None,
) -> dict:
    """Map-reduce summary of a LONG window (multiple days / weeks).

    Thin wrapper over services.agent.summarizer.summarize_window. The
    summarizer chunks the window, builds a deterministic zero-LLM
    mini-summary per chunk, then folds them into one narrative with a
    single budget-gated LLM reduce step. Use for 'summarize the last
    week/month'. For a single day use summarize_activity (cheaper).
    """
    from services.agent.summarizer import summarize_window as _summarize_window

    return await _summarize_window(
        ctx,
        hours=hours,
        focus=focus,
        chunk_by=chunk_by,
        provider_id=provider_id,
    )


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
            "Semantic + filter search over indexed camera observations. "
            "Cheap. Each result row carries an observation_id, a "
            "timestamp, a thumbnail_url, the VLM description, and the "
            "raw detections. WINDOW. Defaults to the last 24h. If a "
            "question implies older footage, set hours explicitly (168 "
            "= 7 days, 720 = 30 days). If your default-window query "
            "returns zero rows for an entity that probably exists, RE-"
            "QUERY with hours=168 then 720 before concluding it wasn't "
            "seen. Use get_last_sightings as a cheaper alternative when "
            "you only need the most recent timestamp per entity."
        ),
        "input_schema": _QUERY_OBSERVATIONS_SCHEMA,
        "fn": query_observations,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "get_journeys",
        "description": (
            "Cross-camera Person sighting sessions. A Journey ties one "
            "Person's appearances across one or more cameras into a "
            "single timeline row with started_at + last_seen_at + the "
            "camera path. Best tool for 'when was Aisha here?', 'where "
            "did Dad go after the kitchen?', 'is anyone still around?'. "
            "WINDOW. Defaults to last 24h. Widen via hours=168 or 720 "
            "for older context. Returns disambiguation when person_name "
            "matches more than one Person. Cheap."
        ),
        "input_schema": _GET_JOURNEYS_SCHEMA,
        "fn": get_journeys,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "get_camera_layout",
        "description": (
            "Static camera inventory. Returns id, name, inferred role "
            "(entry, kitchen, garage, outdoor, nursery, living, other), "
            "scene_mode (indoor/outdoor), online status, and timezone. "
            "Use when you need to know which camera covers which area. "
            "Prefer get_household_snapshot when you also want last-"
            "observation freshness per camera. Cheap."
        ),
        "input_schema": _GET_CAMERA_LAYOUT_SCHEMA,
        "fn": get_camera_layout,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "get_household_snapshot",
        "description": (
            "Bootstrap orientation. Returns every accessible camera with "
            "its last-observation timestamp, every named Person with "
            "their last-seen-at, and any Journey still active right now. "
            "Cheap; safe to call on the first turn of most questions so "
            "you have grounding before deciding which tool to call next."
        ),
        "input_schema": _GET_HOUSEHOLD_SNAPSHOT_SCHEMA,
        "fn": get_household_snapshot,
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
        "name": "get_events",
        "description": (
            "List rule firings (Events) over a time window. Each Event "
            "is a high-confidence semantic fact about something that "
            "happened. the rule's trigger pattern already confirmed it. "
            "Filter by rule_ids, rule_name_contains, action_status. "
            "Use BEFORE analyze_clip for 'how many times did X happen?' "
            "or 'when did rule Y fire?' questions. Cheap; one DB scan. "
            "Default 24h window, max 30d, max 200 rows. Set "
            "include_payload=true when you also need the per-event "
            "camera_id or detection labels."
        ),
        "input_schema": _GET_EVENTS_SCHEMA,
        "fn": get_events,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "summarize_activity",
        "description": (
            "Pre-aggregated rollup designed for 'what happened today?' "
            "questions. ONE call returns. per-Person sighting counts + "
            "camera path (from Journeys), per-rule firing counts with "
            "first/last fired times (from Events), per-label observation "
            "counts (cat, dog, person, etc), and per-camera activity "
            "ranking. Use this FIRST for any narrative or summary "
            "question before issuing per-entity queries. Cheap; one "
            "DB pass. Default 24h, max 30d."
        ),
        "input_schema": _SUMMARIZE_ACTIVITY_SCHEMA,
        "fn": summarize_activity,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "summarize_window",
        "description": (
            "Summarize a LONG time window (multiple days/weeks) by "
            "chunking it, summarizing each chunk, then folding into one "
            "narrative. Use for 'summarize the last week/month' or 'what "
            "happened at <camera> over <long period>'. For a single day "
            "use summarize_activity (cheaper). Bounded token cost via "
            "map-reduce. the per-chunk step is deterministic (zero LLM), "
            "only the final reduce calls the model and it is budget-gated."
        ),
        "input_schema": _SUMMARIZE_WINDOW_SCHEMA,
        "fn": summarize_window,
        "side_effect": "read",
        "cost_class": "medium",
    },
    {
        "name": "query_relationships",
        "description": (
            "Walk relationships between people, animals, vehicles, "
            "cameras, and time. Answers 'who was with X', 'did X come "
            "back later', 'where did X go', 'was X seen with a <label>', "
            "'what's the usual path'. Cheap; one DB pass over the Journey "
            "graph (segments + transitions JSON). Use this BEFORE "
            "stitching multiple get_journeys calls. Relations. "
            "co_present_with, revisited, path, seen_with_label, "
            "transitions. Subject is a Person name/id or a label like "
            "'cat'/'car'. Returns disambiguation when a name matches more "
            "than one Person."
        ),
        "input_schema": _QUERY_RELATIONSHIPS_SCHEMA,
        "fn": query_relationships,
        "side_effect": "read",
        "cost_class": "cheap",
    },
    {
        "name": "analyze_clip",
        "description": (
            "EXPENSIVE. Runs a VLM against actual video frames over a "
            "[time_from, time_to] window on one camera. Use ONLY when "
            "query_observations + get_last_sightings + get_journeys "
            "cannot answer the question because the concept isn't in "
            "our indexed taxonomy ('was he eating?', 'is the package "
            "still there?', 'did anyone come through the gate at 2 "
            "PM?'). Cached per (recording, question, model) forever — "
            "asking the same question about the same window again is "
            "free. Returns a structured answer with verdict, confidence, "
            "evidence frames, and a vlm_call_id you must cite."
        ),
        "input_schema": _ANALYZE_CLIP_SCHEMA,
        "fn": analyze_clip,
        "side_effect": "read",
        "cost_class": "expensive",
    },
    {
        "name": "analyze_frame",
        "description": (
            "MEDIUM cost. Runs a VLM against ONE Observation's "
            "thumbnail. Use when you already have a specific "
            "observation_id (from query_observations or "
            "get_last_sightings) and need to verify or extract a "
            "single detail ('is the dog in this frame holding "
            "anything?', 'what color is the visitor's jacket?'). "
            "Cached per (observation, question, model) forever. "
            "Returns the same structured answer schema as analyze_clip."
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
