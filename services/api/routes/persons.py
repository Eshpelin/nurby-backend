"""People management API. CRUD for persons + face photo upload + auto-discovery suggestions + activity feed."""

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import and_, func as sa_func, or_, select


def sa_lower(col):
    return sa_func.lower(col)

from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.auth import decode_access_token, get_current_user, require_admin
from shared.database import get_db
from shared.models import Camera, Conversation, FaceCluster, FaceClusterSample, FaceEmbedding, Incident, Observation, Person, Recording, Transcript, User
from shared.schemas import PersonCreate, PersonRecapResponse, PersonResponse, PersonUpdate
from services.recap import generate_recap

router = APIRouter()

PHOTOS_DIR = os.path.join(settings.thumbnails_path, "persons")


@router.get("", response_model=list[PersonResponse])
async def list_persons(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Person).order_by(Person.created_at))
    return result.scalars().all()


# ── Face cluster suggestion endpoints ──


class NameClusterBody(PydanticBaseModel):
    display_name: str
    relationship: str | None = None


@router.get("/suggestions", response_model=list)
async def list_suggestions(
    min_sightings: int = Query(default=2, ge=1, description="Minimum sightings to show as suggestion"),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """List auto-discovered face clusters pending user naming."""
    result = await db.execute(
        select(FaceCluster)
        .where(FaceCluster.status == "pending")
        .where(FaceCluster.sighting_count >= min_sightings)
        .order_by(FaceCluster.sighting_count.desc())
    )
    clusters = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "sample_thumbnail_path": c.sample_thumbnail_path,
            "sighting_count": c.sighting_count,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "first_camera_id": str(c.first_camera_id) if c.first_camera_id else None,
            "status": c.status,
            "auto_label_number": c.auto_label_number,
            "auto_label": f"Unknown {c.auto_label_number}" if c.auto_label_number else "Unknown",
            "appearance_description": c.appearance_description,
            "appearance_description_status": c.appearance_description_status,
        }
        for c in clusters
    ]


@router.get("/suggestions/{cluster_id}/samples")
async def get_cluster_samples(
    cluster_id: uuid.UUID,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Get sample face thumbnails for a cluster."""
    result = await db.execute(
        select(FaceClusterSample)
        .where(FaceClusterSample.cluster_id == cluster_id)
        .order_by(FaceClusterSample.captured_at.desc())
        .limit(12)
    )
    samples = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "camera_id": str(s.camera_id),
            "thumbnail_path": s.thumbnail_path,
            "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        }
        for s in samples
    ]


@router.get("/suggestions/{cluster_id}/thumbnail")
async def get_cluster_thumbnail(
    cluster_id: uuid.UUID,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Thumbnail auth accepts `?token=` query param so <img> tags work."""
    if not token or not decode_access_token(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster or not cluster.sample_thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = os.path.abspath(cluster.sample_thumbnail_path)
    allowed_dir = os.path.abspath(settings.thumbnails_path)
    if not path.startswith(allowed_dir + os.sep) and not path.startswith(allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Thumbnail file not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/suggestions/{cluster_id}/samples/{sample_id}/thumbnail")
async def get_sample_thumbnail(
    cluster_id: uuid.UUID,
    sample_id: uuid.UUID,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Thumbnail auth accepts `?token=` query param so <img> tags work."""
    if not token or not decode_access_token(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    sample = await db.get(FaceClusterSample, sample_id)
    if not sample or not sample.thumbnail_path or sample.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = os.path.abspath(sample.thumbnail_path)
    allowed_dir = os.path.abspath(settings.thumbnails_path)
    if not path.startswith(allowed_dir + os.sep) and not path.startswith(allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Thumbnail file not found")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/suggestions/{cluster_id}/name")
async def name_cluster(
    cluster_id: uuid.UUID,
    body: NameClusterBody,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Name a face cluster. Creates Person and links all cluster embeddings."""
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if cluster.status != "pending":
        raise HTTPException(status_code=400, detail="Cluster already processed")

    await _ensure_unique_display_name(db, body.display_name)
    # Create Person
    person = Person(
        display_name=body.display_name,
        relationship=body.relationship,
        consent_given=True,
        photo_path=cluster.sample_thumbnail_path,
    )
    db.add(person)
    await db.flush()

    # Link all cluster sample embeddings to the new person
    samples_result = await db.execute(
        select(FaceClusterSample).where(FaceClusterSample.cluster_id == cluster_id)
    )
    samples = samples_result.scalars().all()

    for sample in samples:
        face_emb = FaceEmbedding(
            person_id=person.id,
            embedding=sample.embedding,
            source="detection",
        )
        db.add(face_emb)

    # Update cluster
    cluster.person_id = person.id
    cluster.status = "named"

    await db.commit()
    await db.refresh(person)

    return {
        "status": "ok",
        "person_id": str(person.id),
        "display_name": person.display_name,
        "embeddings_linked": len(samples),
    }


@router.post("/suggestions/{cluster_id}/ignore")
async def ignore_cluster(
    cluster_id: uuid.UUID,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Dismiss a face cluster suggestion."""
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    cluster.status = "ignored"
    await db.commit()
    return {"status": "ok"}


# ── Activity feed endpoints ──


class PersonActivity(PydanticBaseModel):
    observation_id: str
    camera_id: str
    camera_name: str | None = None
    started_at: str
    ended_at: str | None = None
    vlm_description: str | None = None
    thumbnail_path: str | None = None
    person_name: str | None = None
    match_distance: float | None = None
    object_detections: dict | None = None


class PersonSummary(PydanticBaseModel):
    person_id: str
    display_name: str
    nickname: str | None = None
    relationship: str | None = None
    photo_path: str | None = None
    total_sightings: int = 0
    sightings_1h: int = 0
    sightings_24h: int = 0
    last_seen_at: str | None = None
    last_seen_camera: str | None = None
    first_seen_at: str | None = None


@router.get("/activity/summary", response_model=list[PersonSummary])
async def person_activity_summary(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get activity summary for all named persons.

    Returns sighting counts (total, 1h, 24h), last seen time and camera.
    Scans observations where person_detections JSON contains person_id.
    """
    from datetime import timezone as tz

    result = await db.execute(select(Person).order_by(Person.created_at))
    persons = result.scalars().all()

    if not persons:
        return []

    # Load cameras for name lookup
    cam_result = await db.execute(select(Camera))
    cameras = {str(c.id): c.name for c in cam_result.scalars().all()}

    # Fetch observations with person detections from last 7 days for efficiency
    from datetime import timedelta
    cutoff_7d = datetime.now(tz.utc) - timedelta(days=7)
    obs_result = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .where(Observation.started_at >= cutoff_7d)
        .order_by(Observation.started_at.desc())
    )
    observations = obs_result.scalars().all()

    now = datetime.now(tz.utc)
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    # Build per-person stats
    person_map: dict[str, dict] = {}
    for p in persons:
        pid = str(p.id)
        person_map[pid] = {
            "person_id": pid,
            "display_name": p.display_name,
            "nickname": p.nickname,
            "relationship": p.relationship,
            "photo_path": p.photo_path,
            "total_sightings": 0,
            "sightings_1h": 0,
            "sightings_24h": 0,
            "last_seen_at": None,
            "last_seen_camera": None,
            "first_seen_at": None,
        }

    # Collect raw sighting timestamps per person, then group into sessions.
    # Two sightings less than SESSION_GAP apart count as one visit.
    SESSION_GAP = timedelta(minutes=10)

    # person_id -> list of (obs_time, camera_id)
    raw_sightings: dict[str, list[tuple[datetime, str]]] = {pid: [] for pid in person_map}

    for obs in observations:
        pd = obs.person_detections
        if not pd or not pd.get("faces"):
            continue
        for face in pd["faces"]:
            pid = face.get("person_id")
            if not pid or pid not in person_map:
                continue
            if obs.started_at:
                raw_sightings[pid].append((obs.started_at, str(obs.camera_id)))

    for pid, sightings in raw_sightings.items():
        if not sightings:
            continue
        entry = person_map[pid]

        # Sort chronologically for session grouping
        sightings.sort(key=lambda x: x[0])

        # Group into sessions
        sessions: list[tuple[datetime, str]] = []  # (session_start, camera_id)
        prev_time = None
        for obs_time, cam_id in sightings:
            if prev_time is None or (obs_time - prev_time) > SESSION_GAP:
                sessions.append((obs_time, cam_id))
            prev_time = obs_time

        entry["total_sightings"] = len(sessions)
        entry["sightings_1h"] = sum(1 for t, _ in sessions if t >= cutoff_1h)
        entry["sightings_24h"] = sum(1 for t, _ in sessions if t >= cutoff_24h)

        # Last seen = latest sighting, first seen = earliest
        last_time, last_cam = sightings[-1]
        first_time, _ = sightings[0]
        entry["last_seen_at"] = last_time.isoformat()
        entry["last_seen_camera"] = cameras.get(last_cam)
        entry["first_seen_at"] = first_time.isoformat()

    # Sort by most recently seen first
    summaries = sorted(
        person_map.values(),
        key=lambda x: x["last_seen_at"] or "",
        reverse=True,
    )
    return summaries


@router.get("/activity/{person_id}", response_model=list[PersonActivity])
async def person_activity_feed(
    person_id: uuid.UUID,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Get activity feed for a specific person.

    Returns observations where this person was detected, ordered by most recent.
    Uses JSON containment to find matching person_detections.
    """
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    # Load cameras for name lookup
    cam_result = await db.execute(select(Camera))
    cameras = {str(c.id): c.name for c in cam_result.scalars().all()}

    pid_str = str(person_id)

    # Query observations with person_detections, scan for matching person_id
    # For PostgreSQL, we could use JSON operators, but for portability we fetch and filter
    obs_result = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .order_by(Observation.started_at.desc())
        .limit(limit * 3)  # Over-fetch since we filter in Python
    )
    all_obs = obs_result.scalars().all()

    activities: list[PersonActivity] = []
    for obs in all_obs:
        if len(activities) >= limit:
            break
        pd = obs.person_detections
        if not pd or not pd.get("faces"):
            continue
        matching_face = None
        for face in pd["faces"]:
            if face.get("person_id") == pid_str:
                matching_face = face
                break
        if not matching_face:
            continue

        skip = offset > 0
        if skip:
            offset -= 1
            continue

        activities.append(PersonActivity(
            observation_id=str(obs.id),
            camera_id=str(obs.camera_id),
            camera_name=cameras.get(str(obs.camera_id)),
            started_at=obs.started_at.isoformat() if obs.started_at else "",
            ended_at=obs.ended_at.isoformat() if obs.ended_at else None,
            vlm_description=obs.vlm_description,
            thumbnail_path=obs.thumbnail_path,
            person_name=matching_face.get("person_name"),
            match_distance=matching_face.get("match_distance"),
            object_detections=obs.object_detections,
        ))

    return activities


# ── Unknown cluster activity endpoints ──


class ClusterSummary(PydanticBaseModel):
    cluster_id: str
    auto_label: str  # "Unknown 645"
    auto_label_number: int | None = None
    appearance_description: str | None = None
    appearance_description_status: str = "pending"
    sample_thumbnail_path: str | None = None
    sighting_count: int = 0
    sightings_1h: int = 0
    sightings_24h: int = 0
    last_seen_at: str | None = None
    last_seen_camera: str | None = None
    first_seen_at: str | None = None


@router.get("/clusters/activity/summary", response_model=list[ClusterSummary])
async def cluster_activity_summary(
    min_sightings: int = Query(default=2, ge=1),
    hours: int = Query(default=24, ge=1, le=168),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Activity summary for unknown (pending) clusters in a recent window.

    Counts distinct visit sessions per cluster using the same 10-minute gap
    rule as the named-person summary, so a single visit does not inflate
    numbers. Only returns clusters still pending (not named or ignored).
    """
    from datetime import timedelta, timezone as tz

    clusters_result = await db.execute(
        select(FaceCluster)
        .where(FaceCluster.status == "pending")
        .where(FaceCluster.sighting_count >= min_sightings)
        .order_by(FaceCluster.last_seen_at.desc())
    )
    clusters = clusters_result.scalars().all()
    if not clusters:
        return []

    cam_result = await db.execute(select(Camera))
    cameras = {str(c.id): c.name for c in cam_result.scalars().all()}

    cutoff = datetime.now(tz.utc) - timedelta(hours=hours)
    obs_result = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .where(Observation.started_at >= cutoff)
        .order_by(Observation.started_at.desc())
    )
    observations = obs_result.scalars().all()

    now = datetime.now(tz.utc)
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)
    SESSION_GAP = timedelta(minutes=10)

    sightings_by_cluster: dict[str, list[tuple[datetime, str]]] = {}
    for obs in observations:
        pd = obs.person_detections
        if not pd or not pd.get("faces"):
            continue
        for face in pd["faces"]:
            cid = face.get("cluster_id")
            if not cid or face.get("person_id"):
                continue
            sightings_by_cluster.setdefault(cid, []).append((obs.started_at, str(obs.camera_id)))

    summaries: list[ClusterSummary] = []
    for c in clusters:
        cid = str(c.id)
        raw = sightings_by_cluster.get(cid, [])
        raw.sort(key=lambda x: x[0])

        sessions: list[tuple[datetime, str]] = []
        prev = None
        for t, cam in raw:
            if prev is None or (t - prev) > SESSION_GAP:
                sessions.append((t, cam))
            prev = t

        total = len(sessions) or (c.sighting_count if not raw else 0)
        if not sessions:
            # No observations in window but cluster has historic sightings.
            # Still surface it with zeros rather than hide.
            last_seen = c.last_seen_at
            first_seen = c.first_seen_at
            last_cam = cameras.get(str(c.first_camera_id)) if c.first_camera_id else None
        else:
            last_seen = sessions[-1][0]
            first_seen = sessions[0][0]
            last_cam = cameras.get(sessions[-1][1])

        label_num = c.auto_label_number or 0
        summaries.append(ClusterSummary(
            cluster_id=cid,
            auto_label=f"Unknown {label_num}" if label_num else "Unknown",
            auto_label_number=c.auto_label_number,
            appearance_description=c.appearance_description,
            appearance_description_status=c.appearance_description_status or "pending",
            sample_thumbnail_path=c.sample_thumbnail_path,
            sighting_count=c.sighting_count,
            sightings_1h=sum(1 for t, _ in sessions if t >= cutoff_1h),
            sightings_24h=sum(1 for t, _ in sessions if t >= cutoff_24h),
            last_seen_at=last_seen.isoformat() if last_seen else None,
            last_seen_camera=last_cam,
            first_seen_at=first_seen.isoformat() if first_seen else None,
        ))

    return summaries


@router.get("/clusters/activity/{cluster_id}", response_model=list[PersonActivity])
async def cluster_activity_feed(
    cluster_id: uuid.UUID,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Activity feed for a single unknown cluster.

    Same shape as the named-person activity feed so the frontend can reuse
    its session grouping logic.
    """
    cluster = await db.get(FaceCluster, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    cam_result = await db.execute(select(Camera))
    cameras = {str(c.id): c.name for c in cam_result.scalars().all()}

    cid_str = str(cluster_id)
    label = f"Unknown {cluster.auto_label_number}" if cluster.auto_label_number else "Unknown"

    obs_result = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .order_by(Observation.started_at.desc())
        .limit(limit * 3)
    )
    all_obs = obs_result.scalars().all()

    activities: list[PersonActivity] = []
    for obs in all_obs:
        if len(activities) >= limit:
            break
        pd = obs.person_detections
        if not pd or not pd.get("faces"):
            continue
        matching = None
        for face in pd["faces"]:
            if face.get("cluster_id") == cid_str:
                matching = face
                break
        if not matching:
            continue

        if offset > 0:
            offset -= 1
            continue

        activities.append(PersonActivity(
            observation_id=str(obs.id),
            camera_id=str(obs.camera_id),
            camera_name=cameras.get(str(obs.camera_id)),
            started_at=obs.started_at.isoformat() if obs.started_at else "",
            ended_at=obs.ended_at.isoformat() if obs.ended_at else None,
            vlm_description=obs.vlm_description,
            thumbnail_path=obs.thumbnail_path,
            person_name=label,
            match_distance=matching.get("match_distance"),
            object_detections=obs.object_detections,
        ))

    return activities


# ── Starred persons recap ──


@router.get("/starred/status", response_model=list[PersonRecapResponse])
async def starred_status(
    force: bool = Query(default=False, description="Bypass recap cache"),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current recap for every starred person.

    Uses each person's recap_prompt to bias the VLM summary. Caches results
    on the Person row for a short TTL so the dashboard can poll cheaply.
    """
    result = await db.execute(
        select(Person).where(Person.is_starred == True).order_by(Person.display_name)  # noqa: E712
    )
    persons = list(result.scalars().all())

    # Auto-seed. if no one is starred yet, promote the top 3 most-seen persons
    # so the dashboard never looks empty. The user can unstar them later.
    if not persons:
        persons = await _auto_star_top_persons(db, limit=3)

    out: list[dict] = []
    for p in persons:
        recap = await generate_recap(db, p, force=force)
        out.append({
            "person_id": p.id,
            "display_name": p.display_name,
            "photo_path": p.photo_path,
            "status": recap["status"],
            "last_seen_at": recap["last_seen_at"],
            "last_camera_id": recap["last_camera_id"],
            "last_camera_name": recap["last_camera_name"],
            "last_thumbnail_path": recap["last_thumbnail_path"],
            "last_observation_id": recap.get("last_observation_id"),
            "sightings_24h": recap["sightings_24h"],
            "generated_at": recap["generated_at"],
            "cached": recap["cached"],
            "stale": recap["stale"],
        })
    return out


async def _auto_star_top_persons(db: AsyncSession, limit: int = 3) -> list[Person]:
    """Pick the most frequently detected persons over the last 7 days and mark
    them as starred. Returns the newly-starred persons. No-op if there are no
    persons at all in the DB."""
    from datetime import timezone as _tz, timedelta as _td

    pres = await db.execute(select(Person))
    all_persons = list(pres.scalars().all())
    if not all_persons:
        return []

    cutoff = datetime.now(_tz.utc) - _td(days=7)
    ores = await db.execute(
        select(Observation)
        .where(Observation.person_detections.isnot(None))
        .where(Observation.started_at >= cutoff)
    )
    counts: dict[str, int] = {}
    for obs in ores.scalars().all():
        pd = obs.person_detections or {}
        for face in pd.get("faces", []) or []:
            pid = face.get("person_id")
            if pid:
                counts[str(pid)] = counts.get(str(pid), 0) + 1

    # Rank. persons with sightings first (by count desc), then the rest by
    # created_at so new installs still get cards instead of an empty row.
    by_id = {str(p.id): p for p in all_persons}
    ranked_seen = sorted(
        (by_id[pid] for pid in counts if pid in by_id),
        key=lambda p: counts.get(str(p.id), 0),
        reverse=True,
    )
    ranked_ids = {str(p.id) for p in ranked_seen}
    remainder = [p for p in all_persons if str(p.id) not in ranked_ids]
    ordered = (ranked_seen + remainder)[:limit]

    for p in ordered:
        p.is_starred = True
    if ordered:
        await db.commit()
    return ordered


# ── Person CRUD endpoints ──


async def _ensure_unique_display_name(
    db: AsyncSession, name: str, exclude_id: uuid.UUID | None = None
) -> None:
    """Case-insensitive uniqueness check on persons.display_name.
    Backs the DB constraint with a friendly 409 instead of an opaque
    integrity error."""
    name_norm = (name or "").strip().lower()
    if not name_norm:
        return
    q = select(Person).where(sa_lower(Person.display_name) == name_norm)
    if exclude_id is not None:
        q = q.where(Person.id != exclude_id)
    existing = (await db.execute(q.limit(1))).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A person named '{existing.display_name}' already exists. "
            "Two people cannot share a display name; journeys would fuse them.",
        )


@router.post("", response_model=PersonResponse, status_code=201)
async def create_person(body: PersonCreate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _ensure_unique_display_name(db, body.display_name)
    person = Person(**body.model_dump())
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return person


@router.get("/{person_id}", response_model=PersonResponse)
async def get_person(person_id: uuid.UUID, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@router.patch("/{person_id}", response_model=PersonResponse)
async def update_person(
    person_id: uuid.UUID, body: PersonUpdate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    updates = body.model_dump(exclude_unset=True)
    if "display_name" in updates and updates["display_name"]:
        await _ensure_unique_display_name(
            db, updates["display_name"], exclude_id=person_id
        )
    for field, value in updates.items():
        setattr(person, field, value)

    await db.commit()
    await db.refresh(person)
    return person


@router.delete("/{person_id}", status_code=204)
async def delete_person(person_id: uuid.UUID, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    await db.delete(person)
    await db.commit()


@router.post("/{person_id}/face", response_model=dict)
async def upload_face(
    person_id: uuid.UUID,
    file: UploadFile = File(...),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Upload a face photo. Generates embedding and stores for matching."""
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # Save photo
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "jpg"
    photo_filename = f"{person_id}.{ext}"
    photo_path = os.path.join(PHOTOS_DIR, photo_filename)

    with open(photo_path, "wb") as f:
        f.write(image_bytes)

    person.photo_path = photo_path
    await db.commit()

    # Generate face embedding
    try:
        from services.perception.faces import FaceRecognizer
        embedding = FaceRecognizer.embed_from_image(image_bytes)
    except Exception:
        embedding = None

    if embedding is None:
        return {
            "status": "photo_saved",
            "message": "Photo saved but no face detected. Try a clearer photo with one visible face.",
            "photo_path": photo_path,
        }

    # Store embedding
    face_emb = FaceEmbedding(
        person_id=person_id,
        embedding=embedding,
        source="upload",
    )
    db.add(face_emb)
    await db.commit()

    return {
        "status": "ok",
        "message": "Face photo saved and embedding generated",
        "photo_path": photo_path,
        "embedding_id": str(face_emb.id),
    }


@router.get("/{person_id}/photo")
async def get_person_photo(
    person_id: uuid.UUID,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Photo auth accepts `?token=` query param so <img> tags work."""
    if not token or not decode_access_token(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person.photo_path:
        raise HTTPException(status_code=404, detail="No photo uploaded")
    path = os.path.abspath(person.photo_path)
    allowed_dir = os.path.abspath(settings.thumbnails_path)
    if not path.startswith(allowed_dir + os.sep) and not path.startswith(allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Photo file not found")
    return FileResponse(path, media_type="image/jpeg")


# ── Follow ("investigative timeline") endpoints ──────────────────────────


async def _follow_observations(
    db: AsyncSession,
    person_id: uuid.UUID | None,
    cluster_id: str | None,
    time_from: datetime | None,
    time_to: datetime | None,
    camera_ids: list[uuid.UUID] | None,
    limit: int,
) -> list[Observation]:
    """Pull observations whose ``person_detections`` reference the
    subject. Person + cluster are mutually exclusive. We over-fetch
    because the JSON match runs in Python (no GIN on the json path
    today)."""
    q = select(Observation).where(Observation.person_detections.isnot(None))
    if camera_ids:
        q = q.where(Observation.camera_id.in_(camera_ids))
    if time_from:
        q = q.where(Observation.started_at >= time_from)
    if time_to:
        q = q.where(Observation.started_at <= time_to)
    q = q.order_by(Observation.started_at.desc()).limit(limit * 6)
    rows = (await db.execute(q)).scalars().all()
    out: list[Observation] = []
    target_pid = str(person_id) if person_id else None
    target_cid = str(cluster_id) if cluster_id else None
    for o in rows:
        if len(out) >= limit:
            break
        faces = (o.person_detections or {}).get("faces") or []
        for f in faces:
            if target_pid and f.get("person_id") == target_pid:
                out.append(o)
                break
            if target_cid and f.get("cluster_id") == target_cid:
                out.append(o)
                break
    return out


def _serialize_observation(o: Observation, cam_name: str | None) -> dict:
    return {
        "kind": "observation",
        "id": str(o.id),
        "camera_id": str(o.camera_id),
        "camera_name": cam_name,
        "ts": o.started_at.isoformat(),
        "ended_at": o.ended_at.isoformat() if o.ended_at else None,
        "vlm_description": o.vlm_description,
        "thumbnail_path": o.thumbnail_path,
        "incident_id": str(o.incident_id) if o.incident_id else None,
        "refined_by_provider_name": o.refined_by_provider_name,
        "object_detections": o.object_detections,
        "person_detections": o.person_detections,
    }


def _serialize_incident(i: Incident, cam_name: str | None) -> dict:
    return {
        "kind": "incident",
        "id": str(i.id),
        "camera_id": str(i.camera_id),
        "camera_name": cam_name,
        "ts": i.started_at.isoformat(),
        "last_seen_at": i.last_seen_at.isoformat(),
        "ended_at": i.ended_at.isoformat() if i.ended_at else None,
        "finalized": i.finalized,
        "occurrence_count": i.occurrence_count,
        "summary_text": i.summary_text,
        "signature_kind": i.signature_kind,
        "signature_key": i.signature_key,
        "thumbnails": i.thumbnails,
    }


def _serialize_conversation(c: Conversation, cam_name: str | None) -> dict:
    return {
        "kind": "conversation",
        "id": str(c.id),
        "camera_id": str(c.camera_id),
        "camera_name": cam_name,
        "ts": c.started_at.isoformat(),
        "ended_at": (c.ended_at or c.ended_at_provisional).isoformat(),
        "transcript_count": c.transcript_count,
        "finalized": c.finalized,
        "summary_text": c.summary_text,
        "cleaned_text": c.cleaned_text,
        "has_clip": bool(c.clip_path),
    }


def _serialize_transcript(t: Transcript, cam_name: str | None) -> dict:
    return {
        "kind": "transcript",
        "id": str(t.id),
        "camera_id": str(t.camera_id),
        "camera_name": cam_name,
        "ts": t.started_at.isoformat(),
        "ended_at": t.ended_at.isoformat(),
        "text": t.text,
        "audio_capture_id": str(t.audio_capture_id) if t.audio_capture_id else None,
        "conversation_id": str(t.conversation_id) if t.conversation_id else None,
    }


def _serialize_recording(r: Recording, cam_name: str | None) -> dict:
    return {
        "kind": "recording",
        "id": str(r.id),
        "camera_id": str(r.camera_id),
        "camera_name": cam_name,
        "ts": r.started_at.isoformat(),
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "duration_seconds": r.duration_seconds,
        "file_path": r.file_path,
        "thumbnail_path": r.thumbnail_path,
    }


async def _follow_bundle(
    db: AsyncSession,
    *,
    subject: dict,
    person_id: uuid.UUID | None,
    cluster_id: str | None,
    display_name: str | None,
    time_from: datetime | None,
    time_to: datetime | None,
    camera_ids_param: list[uuid.UUID] | None,
    limit: int,
) -> dict:
    """Build the unified investigative bundle for one subject.

    Pulls observations (JSON scan), incidents (signature match by
    name or cluster id), conversations + transcripts (when the
    subject has a person_id and was attributed as a speaker), and
    recordings overlapping the observation timestamps. Returns a
    feed sorted newest-first plus aggregate stats for the header.
    """
    cam_rows = (await db.execute(select(Camera))).scalars().all()
    cam_name_by_id: dict[str, str] = {str(c.id): c.name for c in cam_rows}

    obs_rows = await _follow_observations(
        db,
        person_id=person_id,
        cluster_id=cluster_id,
        time_from=time_from,
        time_to=time_to,
        camera_ids=camera_ids_param,
        limit=limit,
    )

    # Cameras seen on, derived from observations.
    cameras_seen: dict[str, dict] = {}
    for o in obs_rows:
        cid = str(o.camera_id)
        bucket = cameras_seen.setdefault(
            cid,
            {
                "id": cid,
                "name": cam_name_by_id.get(cid, "Unknown"),
                "count": 0,
                "first_seen_at": None,
                "last_seen_at": None,
            },
        )
        bucket["count"] += 1
        ts = o.started_at.isoformat()
        if not bucket["first_seen_at"] or ts < bucket["first_seen_at"]:
            bucket["first_seen_at"] = ts
        if not bucket["last_seen_at"] or ts > bucket["last_seen_at"]:
            bucket["last_seen_at"] = ts
    cameras_seen_list = sorted(
        cameras_seen.values(), key=lambda b: -int(b["count"])
    )
    seen_camera_uuids = [uuid.UUID(c["id"]) for c in cameras_seen_list]

    # Incidents matching the subject signature.
    inc_q = select(Incident).order_by(Incident.last_seen_at.desc()).limit(limit)
    if cluster_id is not None:
        inc_q = inc_q.where(
            and_(
                Incident.signature_kind == "cluster",
                Incident.signature_key.ilike(f"%{cluster_id}%"),
            )
        )
    elif display_name:
        inc_q = inc_q.where(
            and_(
                Incident.signature_kind == "person",
                or_(
                    Incident.signature_key == display_name,
                    Incident.signature_key.ilike(f"%{display_name}%"),
                ),
            )
        )
    else:
        inc_q = inc_q.where(Incident.id.is_(None))  # no match
    if camera_ids_param:
        inc_q = inc_q.where(Incident.camera_id.in_(camera_ids_param))
    if time_from:
        inc_q = inc_q.where(Incident.started_at >= time_from)
    if time_to:
        inc_q = inc_q.where(Incident.started_at <= time_to)
    inc_rows = (await db.execute(inc_q)).scalars().all()

    # Conversations + transcripts. Only persons (not clusters) carry
    # speaker attribution today.
    conv_rows: list[Conversation] = []
    tx_rows: list[Transcript] = []
    if person_id is not None:
        tx_q = (
            select(Transcript)
            .where(Transcript.speaker_person_id == person_id)
            .where(Transcript.filtered.is_(False))
            .order_by(Transcript.started_at.desc())
            .limit(limit)
        )
        if camera_ids_param:
            tx_q = tx_q.where(Transcript.camera_id.in_(camera_ids_param))
        if time_from:
            tx_q = tx_q.where(Transcript.started_at >= time_from)
        if time_to:
            tx_q = tx_q.where(Transcript.started_at <= time_to)
        tx_rows = list((await db.execute(tx_q)).scalars().all())
        # Deduplicate to one entry per conversation_id when possible;
        # standalone transcripts (no conversation) ride through.
        conv_ids = sorted(
            {t.conversation_id for t in tx_rows if t.conversation_id}
        )
        if conv_ids:
            conv_rows = list(
                (
                    await db.execute(
                        select(Conversation)
                        .where(Conversation.id.in_(conv_ids))
                        .order_by(Conversation.started_at.desc())
                        .limit(limit)
                    )
                ).scalars().all()
            )

    # Recordings on cameras the subject was seen on, overlapping the
    # observation window. Pulled per-camera so we can later mark which
    # observations they overlap.
    rec_rows: list[Recording] = []
    if seen_camera_uuids:
        rec_q = (
            select(Recording)
            .where(Recording.camera_id.in_(seen_camera_uuids))
            .order_by(Recording.started_at.desc())
            .limit(limit)
        )
        if time_from:
            rec_q = rec_q.where(Recording.started_at >= time_from)
        if time_to:
            rec_q = rec_q.where(Recording.started_at <= time_to)
        rec_rows = list((await db.execute(rec_q)).scalars().all())
        # Filter to recordings that overlap any observation timestamp
        # for the subject. Cheap: O(rec * obs) with small N each.
        kept: list[Recording] = []
        for r in rec_rows:
            r_start = r.started_at
            r_end = r.ended_at or datetime.utcnow().replace(tzinfo=r_start.tzinfo)
            for o in obs_rows:
                if o.camera_id != r.camera_id:
                    continue
                if r_start <= o.started_at <= r_end:
                    kept.append(r)
                    break
        rec_rows = kept

    feed: list[dict] = []
    feed.extend(_serialize_observation(o, cam_name_by_id.get(str(o.camera_id))) for o in obs_rows)
    feed.extend(_serialize_incident(i, cam_name_by_id.get(str(i.camera_id))) for i in inc_rows)
    feed.extend(_serialize_conversation(c, cam_name_by_id.get(str(c.camera_id))) for c in conv_rows)
    # Standalone transcripts only.
    seen_conv_ids = {str(c.id) for c in conv_rows}
    for t in tx_rows:
        if t.conversation_id and str(t.conversation_id) in seen_conv_ids:
            continue
        feed.append(_serialize_transcript(t, cam_name_by_id.get(str(t.camera_id))))
    feed.extend(_serialize_recording(r, cam_name_by_id.get(str(r.camera_id))) for r in rec_rows)
    feed.sort(key=lambda x: x["ts"], reverse=True)

    # Aggregate stats for the header.
    first_ts = (obs_rows[-1].started_at.isoformat() if obs_rows else None)
    last_ts = (obs_rows[0].started_at.isoformat() if obs_rows else None)

    hour_buckets: dict[str, int] = {}
    for o in obs_rows:
        h = o.started_at.strftime("%H")
        hour_buckets[h] = hour_buckets.get(h, 0) + 1

    return {
        "subject": subject,
        "stats": {
            "total_sightings": len(obs_rows),
            "first_seen_at": first_ts,
            "last_seen_at": last_ts,
            "cameras_seen": cameras_seen_list,
            "hour_buckets": hour_buckets,
            "incidents_count": len(inc_rows),
            "conversations_count": len(conv_rows),
            "recordings_count": len(rec_rows),
        },
        "feed": feed[: limit * 4],
    }


@router.get("/{person_id}/follow")
async def follow_person(
    person_id: uuid.UUID,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    camera_ids: list[uuid.UUID] | None = Query(default=None, alias="camera_id"),
    limit: int = Query(default=80, ge=10, le=500),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Investigative timeline for a named person. Bundles observations,
    incidents matching the person's name, conversations + transcripts
    where the person was attributed as a speaker, and recordings that
    overlap the person's observation timestamps.
    """
    person = await db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="person not found")
    subject = {
        "kind": "person",
        "id": str(person.id),
        "display_name": person.display_name,
        "relationship": person.relationship,
        "photo_path": person.photo_path,
    }
    return await _follow_bundle(
        db,
        subject=subject,
        person_id=person_id,
        cluster_id=None,
        display_name=person.display_name,
        time_from=from_,
        time_to=to,
        camera_ids_param=camera_ids,
        limit=limit,
    )


@router.get("/clusters/{cluster_id}/follow")
async def follow_cluster(
    cluster_id: uuid.UUID,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    camera_ids: list[uuid.UUID] | None = Query(default=None, alias="camera_id"),
    limit: int = Query(default=80, ge=10, le=500),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Investigative timeline for a recurring unknown face cluster.
    Conversations / transcripts are skipped because cluster-level
    speaker attribution is not implemented today.
    """
    cluster = await db.get(FaceCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="cluster not found")
    subject = {
        "kind": "cluster",
        "id": str(cluster.id),
        "auto_label": getattr(cluster, "auto_label", None),
        "auto_label_number": getattr(cluster, "auto_label_number", None),
        "appearance_description": getattr(cluster, "appearance_description", None),
        "sample_thumbnail_path": getattr(cluster, "sample_thumbnail_path", None),
    }
    return await _follow_bundle(
        db,
        subject=subject,
        person_id=None,
        cluster_id=str(cluster_id),
        display_name=None,
        time_from=from_,
        time_to=to,
        camera_ids_param=camera_ids,
        limit=limit,
    )
