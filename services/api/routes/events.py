import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models import Event
from shared.schemas import EventResponse

router = APIRouter()


@router.get("", response_model=list[EventResponse])
async def list_events(
    rule_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Event).order_by(Event.fired_at.desc()).limit(limit).offset(offset)
    if rule_id:
        query = query.where(Event.rule_id == rule_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/{event_id}/acknowledge", response_model=EventResponse)
async def acknowledge_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    event.acknowledged_at = datetime.now()
    await db.commit()
    await db.refresh(event)
    return event
