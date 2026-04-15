import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models import Observation
from shared.schemas import ObservationResponse

router = APIRouter()


@router.get("", response_model=list[ObservationResponse])
async def list_observations(
    camera_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Observation).order_by(Observation.started_at.desc()).limit(limit).offset(offset)
    )
    if camera_id:
        query = query.where(Observation.camera_id == camera_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{observation_id}", response_model=ObservationResponse)
async def get_observation(observation_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    observation = await db.get(Observation, observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation
