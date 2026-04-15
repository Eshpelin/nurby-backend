import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models import Camera
from shared.schemas import SystemStatus

router = APIRouter()


@router.get("/status", response_model=SystemStatus)
async def get_system_status(db: AsyncSession = Depends(get_db)):
    from services.api.main import START_TIME

    total = await db.scalar(select(func.count()).select_from(Camera))
    online = await db.scalar(
        select(func.count()).select_from(Camera).where(Camera.status != "offline")
    )
    recording = await db.scalar(
        select(func.count()).select_from(Camera).where(Camera.status == "recording")
    )

    return SystemStatus(
        version="0.1.0",
        cameras_total=total or 0,
        cameras_online=online or 0,
        cameras_recording=recording or 0,
        uptime_seconds=time.time() - START_TIME,
    )
