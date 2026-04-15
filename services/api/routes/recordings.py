import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models import Recording
from shared.schemas import RecordingResponse

router = APIRouter()


@router.get("", response_model=list[RecordingResponse])
async def list_recordings(
    camera_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Recording).order_by(Recording.started_at.desc()).limit(limit).offset(offset)
    if camera_id:
        query = query.where(Recording.camera_id == camera_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{recording_id}", response_model=RecordingResponse)
async def get_recording(recording_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


@router.delete("/{recording_id}", status_code=204)
async def delete_recording(recording_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    await db.delete(recording)
    await db.commit()
