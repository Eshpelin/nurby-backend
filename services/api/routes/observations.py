import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import decode_access_token, get_current_user, require_admin
from shared.config import settings
from shared.database import get_db
from shared.models import Observation, Person, User
from shared.schemas import ObservationResponse

router = APIRouter()


@router.get("", response_model=list[ObservationResponse])
async def list_observations(
    camera_id: uuid.UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from", description="Inclusive start (ISO 8601)"),
    to: datetime | None = Query(default=None, description="Inclusive end (ISO 8601)"),
    person_id: uuid.UUID | None = Query(default=None, description="Filter to observations naming this person"),
    label: str | None = Query(default=None, description="Filter to observations with this YOLO label"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    query = select(Observation).order_by(Observation.started_at.desc())
    if camera_id:
        query = query.where(Observation.camera_id == camera_id)
    if from_:
        query = query.where(Observation.started_at >= from_)
    if to:
        query = query.where(Observation.started_at <= to)
    if person_id:
        # person_detections stores the canonical display_name. Resolve
        # the id to a name and match the JSON text.
        name = (
            await db.execute(select(Person.display_name).where(Person.id == person_id))
        ).scalars().first()
        if not name:
            return []
        query = query.where(
            cast(Observation.person_detections, String).ilike(f'%"person_name": "{name}"%')
        )
    if label:
        query = query.where(
            cast(Observation.object_detections, String).ilike(f'%"label": "{label}"%')
        )
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{observation_id}", response_model=ObservationResponse)
async def get_observation(observation_id: uuid.UUID, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    observation = await db.get(Observation, observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation


@router.get("/{observation_id}/thumbnail")
async def get_observation_thumbnail(
    observation_id: uuid.UUID,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Thumbnail auth accepts either Bearer header or a `?token=` query
    param so <img> tags can load without JS."""
    if not token or not decode_access_token(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    observation = await db.get(Observation, observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    if not observation.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = os.path.abspath(observation.thumbnail_path)
    allowed_dir = os.path.abspath(settings.thumbnails_path)
    if not path.startswith(allowed_dir + os.sep) and not path.startswith(allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Thumbnail file not found on disk")
    return FileResponse(path, media_type="image/jpeg")
