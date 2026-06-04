"""Vehicle identities API. the vehicle analogue of persons.py.

Vehicles are auto-created by the perception pipeline (keyed by license
plate). Sightings are not a separate table. they are read out of
``Observation.vehicle_detections`` exactly like People reads
person_detections, so a vehicle's "where + when" comes straight from the
timeline data already being stored.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import decode_access_token, get_current_user
from shared.database import get_db
from shared.models import Camera, Observation, User, Vehicle

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VehicleResponse(BaseModel):
    id: uuid.UUID
    identity_key: str
    display_name: str
    nickname: str | None = None
    license_plate: str | None = None
    vehicle_type: str | None = None
    make: str | None = None
    model: str | None = None
    color: str | None = None
    description: str | None = None
    description_status: str = "pending"
    is_starred: bool = False
    is_provisional: bool = True
    sighting_count: int = 0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    first_camera_id: uuid.UUID | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class VehicleUpdate(BaseModel):
    display_name: str | None = None
    nickname: str | None = None
    license_plate: str | None = None
    vehicle_type: str | None = None
    make: str | None = None
    model: str | None = None
    color: str | None = None
    is_starred: bool | None = None


def _vehicle_ids_in(obs: Observation) -> set[str]:
    vd = obs.vehicle_detections or {}
    out: set[str] = set()
    for v in vd.get("vehicles", []) or []:
        vid = v.get("vehicle_id")
        if vid:
            out.add(str(vid))
    return out


@router.get("", response_model=list[VehicleResponse])
async def list_vehicles(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All known vehicles, most recently seen first."""
    rows = (
        await db.execute(select(Vehicle).order_by(Vehicle.last_seen_at.desc()))
    ).scalars().all()
    return rows


@router.get("/activity/summary")
async def vehicles_activity_summary(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-vehicle sighting counts (1h / 24h / total) + last camera.

    Scans observations from the last 7 days and tallies by vehicle_id,
    mirroring the People activity summary.
    """
    vehicles = (await db.execute(select(Vehicle))).scalars().all()
    if not vehicles:
        return []

    cam_rows = (await db.execute(select(Camera.id, Camera.name))).all()
    cam_names = {str(cid): name for cid, name in cam_rows}

    now = _now()
    cutoff_7d = now - timedelta(days=7)
    obs = (
        await db.execute(
            select(Observation)
            .where(
                Observation.vehicle_detections.is_not(None),
                Observation.started_at >= cutoff_7d,
            )
            .order_by(Observation.started_at.desc())
        )
    ).scalars().all()

    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    agg: dict[str, dict] = {}
    for o in obs:
        ts = o.started_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        for vid in _vehicle_ids_in(o):
            a = agg.setdefault(vid, {"total": 0, "h1": 0, "h24": 0, "last": None, "last_cam": None})
            a["total"] += 1
            if ts >= cutoff_1h:
                a["h1"] += 1
            if ts >= cutoff_24h:
                a["h24"] += 1
            if a["last"] is None or ts > a["last"]:
                a["last"] = ts
                a["last_cam"] = cam_names.get(str(o.camera_id))

    out = []
    for v in vehicles:
        a = agg.get(str(v.id), {})
        out.append({
            "vehicle_id": str(v.id),
            "display_name": v.display_name,
            "license_plate": v.license_plate,
            "vehicle_type": v.vehicle_type,
            "color": v.color,
            "make": v.make,
            "model": v.model,
            "description": v.description,
            "is_starred": v.is_starred,
            "total_sightings": a.get("total", v.sighting_count or 0),
            "sightings_1h": a.get("h1", 0),
            "sightings_24h": a.get("h24", 0),
            "last_seen_at": (a.get("last") or v.last_seen_at),
            "last_seen_camera": a.get("last_cam"),
            "first_seen_at": v.first_seen_at,
        })
    return out


@router.get("/activity/{vehicle_id}")
async def vehicle_activity_feed(
    vehicle_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sightings of one vehicle. observations whose vehicle_detections
    reference this vehicle, newest first, with camera name + thumbnail."""
    cam_rows = (await db.execute(select(Camera.id, Camera.name))).all()
    cam_names = {str(cid): name for cid, name in cam_rows}

    obs = (
        await db.execute(
            select(Observation)
            .where(Observation.vehicle_detections.is_not(None))
            .order_by(Observation.started_at.desc())
            .limit(limit * 6)
        )
    ).scalars().all()

    target = str(vehicle_id)
    feed = []
    for o in obs:
        if target not in _vehicle_ids_in(o):
            continue
        plate = None
        for v in (o.vehicle_detections or {}).get("vehicles", []) or []:
            if str(v.get("vehicle_id")) == target:
                plate = v.get("plate_text")
                break
        feed.append({
            "observation_id": str(o.id),
            "camera_id": str(o.camera_id),
            "camera_name": cam_names.get(str(o.camera_id)),
            "started_at": o.started_at,
            "vlm_description": o.vlm_description,
            "thumbnail_path": o.thumbnail_path,
            "plate_text": plate,
        })
        if len(feed) >= limit:
            break
    return feed


@router.get("/{vehicle_id}", response_model=VehicleResponse)
async def get_vehicle(
    vehicle_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await db.get(Vehicle, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return v


@router.patch("/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleUpdate,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await db.get(Vehicle, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(v, field, value)
    # A human edited it. no longer a provisional auto-guess.
    if any(k in data for k in ("display_name", "make", "model", "license_plate")):
        v.is_provisional = False
    await db.commit()
    await db.refresh(v)
    return v


@router.delete("/{vehicle_id}", status_code=204)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await db.get(Vehicle, vehicle_id)
    if v is not None:
        await db.delete(v)
        await db.commit()
    return None


@router.get("/{vehicle_id}/photo")
async def vehicle_photo(
    vehicle_id: uuid.UUID,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Best available photo. the vehicle's stored photo, else the most
    recent sighting thumbnail. Accepts a token query param so <img> tags
    work without a header."""
    if token:
        try:
            decode_access_token(token)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")

    v = await db.get(Vehicle, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    path = v.photo_path
    if not path or not os.path.exists(path):
        # Fall back to the latest sighting thumbnail.
        obs = (
            await db.execute(
                select(Observation)
                .where(Observation.vehicle_detections.is_not(None))
                .order_by(Observation.started_at.desc())
                .limit(200)
            )
        ).scalars().all()
        target = str(vehicle_id)
        for o in obs:
            if target in _vehicle_ids_in(o) and o.thumbnail_path and os.path.exists(o.thumbnail_path):
                path = o.thumbnail_path
                break
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No photo available")
    return FileResponse(path, media_type="image/jpeg")
