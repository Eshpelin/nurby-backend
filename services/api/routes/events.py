import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user, require_admin
from shared.database import get_db
from shared.models import Event, EventNote, Observation, Person, User
from shared.schemas import EventNoteCreate, EventNoteResponse, EventResponse

router = APIRouter()


async def _serialize_note(db: AsyncSession, note: EventNote) -> dict:
    """Resolve the author's display name for the EventNote response."""
    display_name: str | None = None
    if note.author_user_id is not None:
        author = await db.get(User, note.author_user_id)
        if author is not None:
            display_name = author.display_name or author.email
    return {
        "id": note.id,
        "event_id": note.event_id,
        "author_user_id": note.author_user_id,
        "author_display_name": display_name,
        "source": note.source,
        "text": note.text,
        "telegram_message_id": note.telegram_message_id,
        "created_at": note.created_at,
    }


@router.get("", response_model=list[EventResponse])
async def list_events(
    rule_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    query = select(Event).order_by(Event.fired_at.desc()).limit(limit).offset(offset)
    if rule_id:
        query = query.where(Event.rule_id == rule_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/history", response_model=list[EventResponse])
async def event_history(
    rule_id: uuid.UUID | None = Query(default=None),
    camera_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from", description="Inclusive start (ISO 8601)"),
    to: datetime | None = Query(default=None, description="Inclusive end (ISO 8601)"),
    person_id: uuid.UUID | None = Query(default=None, description="Filter to events whose observation names this person"),
    label: str | None = Query(default=None, description="Filter to events whose observation carries this label"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """List events with optional filters for rule, camera, action status,
    time range, person, and label."""
    query = select(Event).order_by(Event.fired_at.desc())
    if rule_id:
        query = query.where(Event.rule_id == rule_id)
    if status:
        query = query.where(Event.action_status == status)
    if from_:
        query = query.where(Event.fired_at >= from_)
    if to:
        query = query.where(Event.fired_at <= to)

    # camera/person/label filters all reach through the linked Observation.
    needs_obs = bool(camera_id or person_id or label)
    if needs_obs:
        query = query.join(Observation, Event.observation_id == Observation.id)
    if camera_id:
        query = query.where(Observation.camera_id == camera_id)
    if person_id:
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


@router.get("/{event_id}")
async def get_event(
    event_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single event with its annotation notes.

    Phase 4. The response now embeds an array of ``notes`` (web,
    telegram, api). Pre-Phase-4 callers that only read top-level
    Event fields keep working because the new key is additive.
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    notes_result = await db.execute(
        select(EventNote)
        .where(EventNote.event_id == event_id)
        .order_by(EventNote.created_at.asc())
    )
    notes_rows = list(notes_result.scalars().all())
    notes_out = [await _serialize_note(db, n) for n in notes_rows]
    base = EventResponse.model_validate(event).model_dump()
    base["notes"] = notes_out
    return base


# ── Phase 4. Event notes (annotations) ──

@router.get("/{event_id}/notes", response_model=list[EventNoteResponse])
async def list_event_notes(
    event_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    result = await db.execute(
        select(EventNote)
        .where(EventNote.event_id == event_id)
        .order_by(EventNote.created_at.asc())
    )
    return [await _serialize_note(db, n) for n in result.scalars().all()]


@router.post("/{event_id}/notes", response_model=EventNoteResponse, status_code=201)
async def create_event_note(
    event_id: uuid.UUID,
    body: EventNoteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Attach a free-text annotation to an event. Source defaults to
    ``web`` since this endpoint backs the timeline's "+ Add note" UI.
    Telegram replies create their own rows via the poller path."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text cannot be empty")
    note = EventNote(
        event_id=event_id,
        author_user_id=current_user.id,
        source="web",
        text=text[:4096],
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return await _serialize_note(db, note)


@router.delete("/{event_id}/notes/{note_id}", status_code=204)
async def delete_event_note(
    event_id: uuid.UUID,
    note_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete an event note. Author or admin only.

    We picked hard delete over soft delete because notes are cheap and
    a half-empty row in the timeline would just confuse users. Audit
    history lives on the Event itself.
    """
    note = await db.get(EventNote, note_id)
    if note is None or note.event_id != event_id:
        raise HTTPException(status_code=404, detail="Note not found")
    is_admin = (getattr(current_user, "role", "") or "").lower() == "admin"
    if note.author_user_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Only the author or an admin can delete this note")
    await db.delete(note)
    await db.commit()


@router.post("/{event_id}/acknowledge", response_model=EventResponse)
async def acknowledge_event(event_id: uuid.UUID, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    event.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(event)
    return event


@router.post("/{event_id}/ack", response_model=EventResponse)
async def ack_event(
    event_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Phase 2 ack endpoint. Symmetric counterpart to the Telegram
    inline-button ack. Any authenticated user can ack their own
    household's events; the ack records the acting user so the
    timeline can show "Acknowledged by Aisha (web)" regardless of
    whether the ack arrived via the Telegram button or the web UI.

    Idempotent. a second ack on an already-acknowledged event is a
    no-op that returns the existing record (the first acker is
    preserved). Mirrors the prior ``acknowledged_at`` column so old
    dashboards keep working.
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.acked_at is None:
        now = datetime.now(timezone.utc)
        event.acked_at = now
        event.acked_by_user_id = current_user.id
        event.acked_via = "web"
        # Mirror to the legacy column so callers reading either field
        # see the ack. Phase 1 dashboards only read acknowledged_at.
        if event.acknowledged_at is None:
            event.acknowledged_at = now
        await db.commit()
        await db.refresh(event)
    return event
