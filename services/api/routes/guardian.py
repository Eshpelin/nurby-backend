"""Guardian by Nurby API.

A thin permission-and-view layer. Guardian-facing endpoints are scoped to the
caller's own active links and honor entitlements (delay, image throttle, tier
gating). Facility-admin endpoints (require_admin) grant/revoke links, manage the
approved-pickup registry, and read the access log.

Nothing here forks identity or detection logic; presence comes from
services.guardian.presence over existing Observation rows.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.guardian import alerts as alerts_mod
from services.guardian import entitlements as ent
from services.guardian import presence as presence_mod
from shared.app_settings import get_setting
from shared.auth import decode_access_token, get_current_user, require_admin
from shared.config import settings
from shared.database import get_db
from shared.models import (
    ApprovedPickup,
    Camera,
    Facility,
    GuardianAccessLog,
    GuardianLink,
    Observation,
    Person,
    User,
)
from shared.schemas import (
    ApprovedPickupCreate,
    ApprovedPickupResponse,
    FacilityCreate,
    FacilityResponse,
    FacilityUpdate,
    GuardianAccessLogResponse,
    GuardianAlertPrefsUpdate,
    GuardianChannelsUpdate,
    GuardianLinkCreate,
    GuardianLinkResponse,
    GuardianLinkUpdate,
)

router = APIRouter()


class GuardianSearchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


# ── helpers ──────────────────────────────────────────────────────────


async def get_or_create_default_facility(db: AsyncSession) -> Facility:
    """One implicit facility for a single-household self-host deploy."""
    row = (
        await db.execute(select(Facility).where(Facility.is_default.is_(True)))
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = Facility(name="My Household", slug="default", is_default=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _load_link(db: AsyncSession, link_id: uuid.UUID) -> GuardianLink:
    link = await db.get(GuardianLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Guardian link not found")
    return link


def _ensure_owner_or_admin(link: GuardianLink, user: User) -> None:
    if user.role == "admin":
        return
    if link.guardian_user_id != user.id:
        # 404, not 403, so a guardian cannot probe for others' link ids.
        raise HTTPException(status_code=404, detail="Guardian link not found")


def _ensure_active(link: GuardianLink) -> None:
    if not ent.is_active(link):
        raise HTTPException(status_code=410, detail="This guardian link is no longer active")


async def _log(
    db: AsyncSession,
    link: GuardianLink,
    action: str,
    request: Request | None,
    detail: dict | None = None,
) -> None:
    ip = None
    if request is not None and request.client is not None:
        ip = request.client.host
    db.add(
        GuardianAccessLog(
            guardian_link_id=link.id,
            guardian_user_id=link.guardian_user_id,
            person_id=link.person_id,
            action=action,
            ip=ip,
            detail=detail,
        )
    )
    await db.commit()


async def _user_from_token_or_header(
    token: str | None, request: Request, db: AsyncSession
) -> User:
    """Resolve the caller from a ``?token=`` JWT (for <img> tags) or the
    Authorization: Bearer header. Raises 401 when neither resolves."""
    jwt = token
    if not jwt:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            jwt = header[7:]
    user_id = decode_access_token(jwt) if jwt else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    return user


async def _facility_floor(db: AsyncSession, link: GuardianLink) -> float | None:
    fac = await db.get(Facility, link.facility_id)
    return fac.reveal_min_confidence if fac else None


async def _free_delay(db: AsyncSession) -> int:
    return int(await get_setting("guardian_free_delay_seconds", 1800))


async def _free_image_interval(db: AsyncSession) -> int:
    return int(await get_setting("guardian_free_image_interval_seconds", 3600))


# ── guardian-facing endpoints ────────────────────────────────────────


@router.get("/me")
async def guardian_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The caller's profile + every dependant they may follow."""
    rows = (
        await db.execute(
            select(GuardianLink).where(GuardianLink.guardian_user_id == user.id)
        )
    ).scalars().all()
    dependants = []
    for link in rows:
        person = await db.get(Person, link.person_id)
        dependants.append(
            {
                "link_id": str(link.id),
                "person_id": str(link.person_id),
                "display_name": (person.nickname or person.display_name) if person else None,
                "relationship_label": link.relationship_label,
                "has_photo": bool(person and person.photo_path),
                "photo_url": (
                    f"/api/guardian/links/{link.id}/photo"
                    if person and person.photo_path
                    else None
                ),
                "active": ent.is_active(link),
                "expires_at": link.expires_at.isoformat() if link.expires_at else None,
                "alert_prefs": link.alert_prefs or ent.DEFAULT_ALERT_PREFS,
                "notify_channels": link.notify_channels or ent.DEFAULT_NOTIFY_CHANNELS,
                "entitlements": ent.entitlement_summary(link),
            }
        )
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
        },
        "dependants": dependants,
    }


@router.get("/links/{link_id}/status")
async def link_status(
    link_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not ent.can_view(link, ent.CAP_STATUS):
        raise HTTPException(status_code=403, detail="This tier cannot view presence")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    delay = await _free_delay(db)
    status = await presence_mod.dependant_status(db, link, person, free_delay_seconds=delay)
    await _log(db, link, "status", request)
    return {**status, "entitlements": ent.entitlement_summary(link)}


@router.get("/links/{link_id}/timeline")
async def link_timeline(
    link_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent sightings of the dependant, clamped to the link cutoff. The
    honest day-timeline: each entry is a real observation with a zone + time."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not ent.can_view(link, ent.CAP_TIMELINE):
        raise HTTPException(status_code=403, detail="This tier cannot view a timeline")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    delay = await _free_delay(db)
    cutoff = ent.cutoff_time(link, delay)
    from datetime import timedelta

    from sqlalchemy import String as SAString
    from sqlalchemy import cast

    needle = f'%"person_id": "{person.id}"%'
    rows = (
        await db.execute(
            select(Observation)
            .where(Observation.started_at <= cutoff)
            .where(
                Observation.started_at
                >= cutoff - timedelta(days=presence_mod.PRESENCE_LOOKBACK_DAYS)
            )
            .where(cast(Observation.person_detections, SAString).ilike(needle))
            .order_by(Observation.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    items = []
    for obs in rows:
        cam = await db.get(Camera, obs.camera_id)
        items.append(
            {
                "observation_id": str(obs.id),
                "at": obs.started_at.isoformat(),
                "zone": (cam.location_label or cam.name) if cam else None,
                "camera_name": cam.name if cam else None,
            }
        )
    await _log(db, link, "timeline", request, {"count": len(items)})
    return {
        "items": items,
        "delayed": ent.effective_delay_seconds(link, delay) > 0,
        "as_of": cutoff.isoformat(),
    }


@router.get("/links/{link_id}/image")
async def link_image(
    link_id: uuid.UUID,
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Serve the freshest blurred thumbnail containing the dependant.

    Identity is resolved from a ``?token=`` JWT (so plain <img> tags work) or
    the Authorization header. Free tier is throttled to one image per configured
    interval and is delayed. The image is only ever of an observation that
    contains the bound dependant; other people remain blurred by the existing
    privacy pipeline.
    """
    user = await _user_from_token_or_header(token, request, db)
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not ent.can_view(link, ent.CAP_IMAGE):
        raise HTTPException(status_code=403, detail="This tier cannot view images")

    interval = await _free_image_interval(db)
    if not ent.image_allowed(link, interval):
        wait = ent.seconds_until_next_image(link, interval)
        raise HTTPException(
            status_code=429,
            detail=f"Free tier is limited to one image per hour. Next image in {wait // 60} min.",
            headers={"Retry-After": str(wait)},
        )

    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    delay = await _free_delay(db)
    img = await presence_mod.latest_image(db, link, person, free_delay_seconds=delay)
    if img is None:
        raise HTTPException(status_code=404, detail="No recent image available")

    path = os.path.abspath(img["thumbnail_path"])
    allowed_dir = os.path.abspath(settings.thumbnails_path)
    if not (path.startswith(allowed_dir + os.sep) or path == allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    # Privacy spine: blur so no non-dependant face is identifiable. Fail safe.
    from fastapi import Response

    from services.guardian.imaging import blur_image_file

    radius = int(await get_setting("guardian_image_blur_radius", 12))
    try:
        blurred = blur_image_file(path, radius)
    except Exception:
        raise HTTPException(status_code=500, detail="Could not process image")

    # Stamp the throttle and log the view.
    link.last_image_served_at = datetime.now(timezone.utc)
    await db.commit()
    await _log(db, link, "image", request, {"observation_id": img["observation_id"]})
    return Response(content=blurred, media_type="image/jpeg")


@router.get("/links/{link_id}/recap")
async def link_recap(
    link_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Premium daily recap. Reuses the existing per-person recap text the
    engine already maintains. Returns a pending state when none is cached."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not ent.can_view(link, ent.CAP_RECAP):
        raise HTTPException(status_code=402, detail="Daily recap is a premium feature")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    await _log(db, link, "recap", request)
    return {
        "person_id": str(person.id),
        "display_name": person.nickname or person.display_name,
        "status": "ready" if person.recap_cached_status else "pending",
        "text": person.recap_cached_status,
        "generated_at": person.recap_cached_at.isoformat() if person.recap_cached_at else None,
        "stale": person.recap_stale,
    }


@router.get("/links/{link_id}/live")
async def link_live(
    link_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Real-time view. Requires live_presence (no-delay status) or live_video
    (a fresh blurred frame). 402 when neither is held."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not (link.live_presence or link.live_video):
        raise HTTPException(status_code=402, detail="Live access is a paid feature")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    delay = await _free_delay(db)
    status = await presence_mod.dependant_status(db, link, person, free_delay_seconds=delay)
    img = clip = None
    clips_on = bool(await get_setting("guardian_unblurred_clips_enabled", False))
    if link.live_video:
        img = await presence_mod.latest_image(db, link, person, free_delay_seconds=delay)
        if clips_on:
            clip = await presence_mod.latest_clip(db, link, person, free_delay_seconds=delay)
    await _log(db, link, "live", request)
    return {
        **status,
        "live_presence": link.live_presence,
        "live_video": link.live_video,
        "image_available": img is not None,
        "image_url": f"/api/guardian/links/{link_id}/image" if img is not None else None,
        "clip_available": clip is not None,
        "clip_url": f"/api/guardian/links/{link_id}/clip" if clip is not None else None,
    }


@router.get("/links/{link_id}/clip")
async def link_clip(
    link_id: uuid.UUID,
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Serve the dependant's most recent recording clip. Requires live_video.

    Clips are raw operator footage (faces not blurred), so they are disabled by
    default to hold the privacy promise. A facility opts in via
    ``guardian_unblurred_clips_enabled``. Per-frame video blur is the planned
    enhancement that lets this default flip on safely."""
    user = await _user_from_token_or_header(token, request, db)
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not link.live_video:
        raise HTTPException(status_code=402, detail="Live video is a paid feature")
    if not bool(await get_setting("guardian_unblurred_clips_enabled", False)):
        raise HTTPException(
            status_code=403,
            detail="Blurred live clips are coming. Raw clips are disabled for privacy.",
        )
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    delay = await _free_delay(db)
    clip = await presence_mod.latest_clip(db, link, person, free_delay_seconds=delay)
    if clip is None:
        raise HTTPException(status_code=404, detail="No recent clip available")
    path = os.path.abspath(clip["clip_path"])
    allowed_dir = os.path.abspath(settings.recordings_path)
    if not (path.startswith(allowed_dir + os.sep) or path == allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Clip file not found on disk")
    await _log(db, link, "live", request, {"clip_observation_id": clip["observation_id"]})
    return FileResponse(path, media_type="video/mp4")


@router.post("/links/{link_id}/search")
async def link_search(
    link_id: uuid.UUID,
    body: GuardianSearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Premium dependant-scoped search. Matches the query against captions of
    observations that contain the bound dependant, clamped to the link cutoff.
    Only ever searches the bound person's sightings."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not ent.can_view(link, ent.CAP_SEARCH):
        raise HTTPException(status_code=402, detail="Smart search is a premium feature")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")

    from datetime import timedelta

    from sqlalchemy import String as SAString
    from sqlalchemy import cast

    delay = await _free_delay(db)
    cutoff = ent.cutoff_time(link, delay)
    person_needle = f'%"person_id": "{person.id}"%'
    base = (
        select(Observation)
        .where(Observation.started_at <= cutoff)
        .where(
            Observation.started_at
            >= cutoff - timedelta(days=presence_mod.PRESENCE_LOOKBACK_DAYS)
        )
        .where(cast(Observation.person_detections, SAString).ilike(person_needle))
    )
    text = (body.query or "").strip()
    rows = None
    mode = "recent"

    # Prefer semantic search over the dependant's sightings. Embed the query
    # and order by pgvector cosine distance; fall back to caption ILIKE, then
    # to recent-first, so it always returns something useful.
    if text:
        embedding = None
        try:
            from services.search.embeddings import generate_embedding, get_embedding_provider

            provider = await get_embedding_provider()
            embedding = await generate_embedding(text, provider)
        except Exception:
            embedding = None
        if embedding:
            q = (
                base.where(Observation.description_embedding.isnot(None))
                .order_by(Observation.description_embedding.cosine_distance(embedding))
                .limit(body.limit)
            )
            rows = (await db.execute(q)).scalars().all()
            mode = "semantic"
        if not rows:
            q = base.where(Observation.vlm_description.ilike(f"%{text}%"))
            rows = (
                await db.execute(q.order_by(Observation.started_at.desc()).limit(body.limit))
            ).scalars().all()
            mode = "keyword"
    if rows is None:
        rows = (
            await db.execute(base.order_by(Observation.started_at.desc()).limit(body.limit))
        ).scalars().all()

    results = []
    for obs in rows:
        cam = await db.get(Camera, obs.camera_id)
        results.append(
            {
                "observation_id": str(obs.id),
                "at": obs.started_at.isoformat(),
                "zone": (cam.location_label or cam.name) if cam else None,
                "caption": obs.vlm_description,
            }
        )
    await _log(db, link, "search", request, {"query": text, "count": len(results), "mode": mode})
    return {
        "query": text,
        "mode": mode,
        "results": results,
        "delayed": ent.effective_delay_seconds(link, delay) > 0,
    }


@router.get("/links/{link_id}/events")
async def link_events(
    link_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The dependant's real day-timeline: arrival, pickup, zone events. Clamped
    to the link cutoff and filtered to the alerts the guardian opted into."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not ent.can_view(link, ent.CAP_TIMELINE):
        raise HTTPException(status_code=403, detail="This tier cannot view a timeline")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")
    from shared.models import GuardianEvent

    delay = await _free_delay(db)
    cutoff = ent.cutoff_time(link, delay)
    rows = (
        await db.execute(
            select(GuardianEvent)
            .where(GuardianEvent.person_id == person.id)
            .where(GuardianEvent.at <= cutoff)
            .order_by(GuardianEvent.at.desc())
            .limit(limit)
        )
    ).scalars().all()
    items = [
        {
            "id": str(e.id),
            "kind": e.kind,
            "message": e.message,
            "severity": e.severity,
            "zone": e.zone,
            "at": e.at.isoformat(),
            "pickup_matched": e.pickup_matched,
            "pickup_name": e.pickup_name,
        }
        for e in rows
        if ent.alert_enabled(link, e.kind)
    ]
    last_pickup = next((i for i in items if i["kind"] == "picked_up"), None)
    await _log(db, link, "timeline", request, {"events": len(items)})
    return {
        "items": items,
        "last_pickup": last_pickup,
        "delayed": ent.effective_delay_seconds(link, delay) > 0,
    }


@router.get("/links/{link_id}/photo")
async def link_photo(
    link_id: uuid.UUID,
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Serve the dependant's enrolled photo (a consented identity/recognition
    aid). Token-auth so <img> tags work. Sharp, since it is the guardian's own
    bound dependant; non-dependant live frames stay blurred elsewhere."""
    user = await _user_from_token_or_header(token, request, db)
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    person = await db.get(Person, link.person_id)
    if person is None or not person.photo_path:
        raise HTTPException(status_code=404, detail="No photo")
    path = os.path.abspath(person.photo_path)
    # Photos live under the thumbnails store in this deploy.
    allowed = os.path.abspath(settings.thumbnails_path)
    if not (path.startswith(allowed + os.sep) or path == allowed):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Photo not found on disk")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/links/{link_id}/trends")
async def link_trends(
    link_id: uuid.UUID,
    request: Request,
    days: int = Query(default=7, ge=1, le=30),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Premium weekly wellbeing trends. Gentle signals (days seen, first/last
    sighting, zone variety), framed as awareness, never judgment. Scoped to the
    bound dependant and clamped to the link cutoff."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    if not link.premium:
        raise HTTPException(status_code=402, detail="Trends are a premium feature")
    person = await db.get(Person, link.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Dependant not found")

    from datetime import timedelta

    from sqlalchemy import String as SAString
    from sqlalchemy import cast

    delay = await _free_delay(db)
    cutoff = ent.cutoff_time(link, delay)
    needle = f'%"person_id": "{person.id}"%'
    rows = (
        await db.execute(
            select(Observation)
            .where(Observation.started_at <= cutoff)
            .where(Observation.started_at >= cutoff - timedelta(days=days))
            .where(cast(Observation.person_detections, SAString).ilike(needle))
            .order_by(Observation.started_at.asc())
        )
    ).scalars().all()

    by_day: dict[str, dict] = {}
    cam_names: dict[uuid.UUID, str | None] = {}
    for o in rows:
        day = o.started_at.date().isoformat()
        d = by_day.setdefault(
            day,
            {"date": day, "sightings": 0, "first_seen": None, "last_seen": None, "zones": set()},
        )
        d["sightings"] += 1
        ts = o.started_at.isoformat()
        if d["first_seen"] is None:
            d["first_seen"] = ts
        d["last_seen"] = ts
        if o.camera_id not in cam_names:
            cam = await db.get(Camera, o.camera_id)
            cam_names[o.camera_id] = (cam.location_label or cam.name) if cam else None
        if cam_names[o.camera_id]:
            d["zones"].add(cam_names[o.camera_id])

    days_list = []
    for d in by_day.values():
        d["zones"] = sorted(d["zones"])
        days_list.append(d)
    days_list.sort(key=lambda x: x["date"])

    await _log(db, link, "search", request, {"trends_days": days})
    return {
        "display_name": person.nickname or person.display_name,
        "window_days": days,
        "days_seen": len(days_list),
        "total_sightings": sum(d["sightings"] for d in days_list),
        "days": days_list,
        "delayed": ent.effective_delay_seconds(link, delay) > 0,
    }


@router.patch("/links/{link_id}/alerts")
async def link_alerts(
    link_id: uuid.UUID,
    body: GuardianAlertPrefsUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Guardian toggles which supported alerts they receive for this dependant."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    link.alert_prefs = ent.sanitize_alert_prefs(body.alert_prefs)
    await db.commit()
    await _log(db, link, "alerts_change", request, {"alert_prefs": link.alert_prefs})
    return {"alert_prefs": link.alert_prefs}


@router.patch("/links/{link_id}/channels")
async def link_channels(
    link_id: uuid.UUID,
    body: GuardianChannelsUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Guardian chooses which channels they receive alerts on (telegram, email,
    in_app). in_app is the shared household notification and is always recorded;
    telegram and email are the per-guardian transports this gates."""
    link = await _load_link(db, link_id)
    _ensure_owner_or_admin(link, user)
    _ensure_active(link)
    link.notify_channels = ent.sanitize_notify_channels(body.notify_channels)
    await db.commit()
    await _log(db, link, "channels_change", request, {"notify_channels": link.notify_channels})
    return {"notify_channels": link.notify_channels}


# ── facility-admin endpoints ─────────────────────────────────────────


@router.get("/facilities", response_model=list[FacilityResponse])
async def list_facilities(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await get_or_create_default_facility(db)
    rows = (await db.execute(select(Facility).order_by(Facility.created_at))).scalars().all()
    return rows


@router.post("/facilities", response_model=FacilityResponse, status_code=201)
async def create_facility(
    body: FacilityCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(select(Facility).where(Facility.slug == body.slug))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="A facility with that slug already exists")
    fac = Facility(
        name=body.name,
        slug=body.slug,
        timezone=body.timezone,
        reveal_min_confidence=body.reveal_min_confidence,
        max_cameras_per_person=body.max_cameras_per_person,
    )
    db.add(fac)
    await db.commit()
    await db.refresh(fac)
    return fac


@router.patch("/facilities/{facility_id}", response_model=FacilityResponse)
async def update_facility(
    facility_id: uuid.UUID,
    body: FacilityUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    fac = await db.get(Facility, facility_id)
    if fac is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(fac, field, value)
    await db.commit()
    await db.refresh(fac)
    return fac


@router.get("/links", response_model=list[GuardianLinkResponse])
async def list_links(
    person_id: uuid.UUID | None = Query(default=None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(GuardianLink).order_by(GuardianLink.granted_at.desc())
    if person_id is not None:
        q = q.where(GuardianLink.person_id == person_id)
    return (await db.execute(q)).scalars().all()


@router.post("/links", status_code=201)
async def create_link(
    body: GuardianLinkCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Bind a guardian user to an existing person. The facility grants; the
    guardian never self-grants.

    Invite flow: if only ``guardian_email`` is given and no account exists yet,
    a guardian account is created with a one-time temporary password returned
    once to the admin to hand to the parent (who then sets their own password).
    """
    import secrets as _secrets

    from shared.auth import hash_password

    person = await db.get(Person, body.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    guardian: User | None = None
    temp_password: str | None = None
    guardian_created = False
    if body.guardian_user_id is not None:
        guardian = await db.get(User, body.guardian_user_id)
    elif body.guardian_email is not None:
        email = body.guardian_email.lower().strip()
        guardian = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if guardian is None:
            # Invite: create the guardian account with a temp password.
            temp_password = _secrets.token_urlsafe(9)
            guardian = User(
                email=email,
                display_name=None,
                password_hash=hash_password(temp_password),
                role="guardian",
                is_active=True,
            )
            db.add(guardian)
            await db.flush()
            guardian_created = True
    if guardian is None:
        raise HTTPException(
            status_code=400,
            detail="Provide guardian_user_id or guardian_email.",
        )

    facility = (
        await db.get(Facility, body.facility_id)
        if body.facility_id is not None
        else await get_or_create_default_facility(db)
    )
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")

    dup = (
        await db.execute(
            select(GuardianLink).where(
                GuardianLink.guardian_user_id == guardian.id,
                GuardianLink.person_id == person.id,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=409, detail="This guardian is already linked to this person"
        )

    link = GuardianLink(
        facility_id=facility.id,
        person_id=person.id,
        guardian_user_id=guardian.id,
        relationship_label=body.relationship_label,
        tier=body.tier,
        alert_prefs=ent.sanitize_alert_prefs(body.alert_prefs),
        premium=body.premium,
        live_presence=body.live_presence,
        live_video=body.live_video,
        audio=body.audio,
        is_primary_parent=body.is_primary_parent,
        reveal_min_confidence=body.reveal_min_confidence,
        granted_by_user_id=admin.id,
        expires_at=body.expires_at,
    )
    db.add(link)
    # Promote a plain viewer to guardian so role-aware nav routes them right.
    if guardian.role == "viewer":
        guardian.role = "guardian"
    await db.commit()
    await db.refresh(link)
    return {
        "id": str(link.id),
        "facility_id": str(link.facility_id),
        "person_id": str(link.person_id),
        "guardian_user_id": str(link.guardian_user_id),
        "relationship_label": link.relationship_label,
        "tier": link.tier,
        "alert_prefs": link.alert_prefs,
        "notify_channels": link.notify_channels,
        "premium": link.premium,
        "live_presence": link.live_presence,
        "live_video": link.live_video,
        "audio": link.audio,
        "is_primary_parent": link.is_primary_parent,
        "reveal_min_confidence": link.reveal_min_confidence,
        "granted_at": link.granted_at.isoformat() if link.granted_at else None,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "revoked_at": None,
        "guardian_created": guardian_created,
        "guardian_email": guardian.email,
        "temp_password": temp_password,
    }


@router.patch("/links/{link_id}", response_model=GuardianLinkResponse)
async def update_link(
    link_id: uuid.UUID,
    body: GuardianLinkUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    link = await _load_link(db, link_id)
    data = body.model_dump(exclude_unset=True)
    if "alert_prefs" in data and data["alert_prefs"] is not None:
        data["alert_prefs"] = ent.sanitize_alert_prefs(data["alert_prefs"])
    for field, value in data.items():
        setattr(link, field, value)
    await db.commit()
    await db.refresh(link)
    return link


@router.delete("/links/{link_id}", status_code=200)
async def revoke_link(
    link_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Instant revoke. Sets revoked_at; access ends immediately. The row is
    kept for the audit trail rather than hard-deleted."""
    link = await _load_link(db, link_id)
    if link.revoked_at is None:
        link.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    return {"revoked": True, "link_id": str(link.id), "revoked_at": link.revoked_at.isoformat()}


# ── approved-pickup registry ─────────────────────────────────────────


@router.get("/persons/{person_id}/pickups", response_model=list[ApprovedPickupResponse])
async def list_pickups(
    person_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return (
        await db.execute(
            select(ApprovedPickup)
            .where(ApprovedPickup.person_id == person_id)
            .order_by(ApprovedPickup.created_at.desc())
        )
    ).scalars().all()


@router.post("/persons/{person_id}/pickups", response_model=ApprovedPickupResponse, status_code=201)
async def add_pickup(
    person_id: uuid.UUID,
    body: ApprovedPickupCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    person = await db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    pickup = ApprovedPickup(
        person_id=person_id,
        name=body.name,
        kind=body.kind,
        linked_person_id=body.linked_person_id,
        vehicle_plate=body.vehicle_plate,
        created_by_user_id=admin.id,
    )
    db.add(pickup)
    await db.commit()
    await db.refresh(pickup)
    return pickup


@router.delete("/pickups/{pickup_id}", status_code=200)
async def delete_pickup(
    pickup_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    pickup = await db.get(ApprovedPickup, pickup_id)
    if pickup is None:
        raise HTTPException(status_code=404, detail="Approved pickup not found")
    await db.delete(pickup)
    await db.commit()
    return {"deleted": True}


@router.get("/access-log", response_model=list[GuardianAccessLogResponse])
async def access_log(
    person_id: uuid.UUID | None = Query(default=None),
    guardian_user_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Facility-visible audit of every guardian view. Transparency is a feature."""
    q = select(GuardianAccessLog).order_by(GuardianAccessLog.at.desc()).limit(limit)
    if person_id is not None:
        q = q.where(GuardianAccessLog.person_id == person_id)
    if guardian_user_id is not None:
        q = q.where(GuardianAccessLog.guardian_user_id == guardian_user_id)
    return (await db.execute(q)).scalars().all()


# ── internal alert emit (perception/integration seam) ────────────────


class GuardianEventEmit(BaseModel):
    """Body for the internal alert seam. The perception layer (or any admin
    integration) posts a person-scoped event; the fan-out decides who to tell,
    verifies pickups, and records the notification."""

    person_id: uuid.UUID
    kind: str = Field(pattern=r"^(arrived|departed|picked_up|entered_zone|left_zone|not_seen)$")
    zone: str | None = None
    camera_id: uuid.UUID | None = None
    observation_id: uuid.UUID | None = None
    minutes: int | None = None
    # Optional pickup escort detection for kind=picked_up.
    detected_person_id: uuid.UUID | None = None
    detected_plate: str | None = None


@router.post("/internal/alerts")
async def emit_alert(
    body: GuardianEventEmit,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Fan a guardian alert out to the opted-in guardians of a person.

    Admin/API-key gated. This is the integration seam the perception pipeline
    calls on arrival/departure/pickup. Pure decision + verification logic lives
    in services.guardian.alerts and is unit-tested.
    """
    person = await db.get(Person, body.person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    links = (
        await db.execute(
            select(GuardianLink).where(GuardianLink.person_id == body.person_id)
        )
    ).scalars().all()

    pickup = None
    if body.kind == "picked_up":
        pickups = (
            await db.execute(
                select(ApprovedPickup).where(ApprovedPickup.person_id == body.person_id)
            )
        ).scalars().all()
        pickup = alerts_mod.verify_pickup(
            pickups,
            detected_person_id=body.detected_person_id,
            detected_plate=body.detected_plate,
        )

    result = await alerts_mod.emit(
        db,
        person,
        body.kind,
        links,
        zone=body.zone,
        camera_id=body.camera_id,
        observation_id=body.observation_id,
        pickup=pickup,
    )
    return {"kind": body.kind, "pickup": pickup, **result}
