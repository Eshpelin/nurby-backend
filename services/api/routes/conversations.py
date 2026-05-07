"""Read API for grouped audio conversations.

A conversation is a rolling group of consecutive transcripts on a
camera, bounded by a per-camera gap heuristic. Lists collapse the
N-card-per-VAD-segment view into one card per conversation. The
detail endpoint returns the full transcript rows so a card can
expand inline.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user
from shared.database import get_db
from shared.models import Conversation, Transcript, User

router = APIRouter()


def _serialize(c: Conversation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "camera_id": str(c.camera_id),
        "started_at": c.started_at.isoformat(),
        "ended_at_provisional": c.ended_at_provisional.isoformat(),
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "transcript_count": c.transcript_count,
        "finalized": c.finalized,
        "summary_text": c.summary_text,
        "cleaned_text": c.cleaned_text,
        "summary_provider_name": c.summary_provider_name,
        "speakers_seen": c.speakers_seen,
        "created_at": c.created_at.isoformat(),
    }


def _serialize_tx(t: Transcript) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "started_at": t.started_at.isoformat(),
        "ended_at": t.ended_at.isoformat(),
        "text": t.text,
        "language": t.language,
        "provider": t.provider,
        "audio_capture_id": str(t.audio_capture_id) if t.audio_capture_id else None,
        "speaker_person_id": str(t.speaker_person_id) if t.speaker_person_id else None,
        "speaker_source": t.speaker_source,
    }


@router.get("")
async def list_conversations(
    camera_id: uuid.UUID | None = Query(default=None),
    finalized: bool | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Conversation).order_by(Conversation.started_at.desc())
    if camera_id:
        q = q.where(Conversation.camera_id == camera_id)
    if finalized is not None:
        q = q.where(Conversation.finalized.is_(finalized))
    if from_:
        q = q.where(Conversation.started_at >= from_)
    if to:
        q = q.where(Conversation.started_at <= to)
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("/{conversation_id}/resummarize")
async def resummarize_conversation(
    conversation_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run the conversation summarizer over an existing finalized
    conversation. Useful when the cleaned text or summary looks wrong
    and the user wants another pass (e.g. after switching the camera's
    summary provider to a stronger model)."""
    row = await db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    # Lazy import to avoid pulling perception deps at API import time.
    from shared.models import Camera, Provider
    from services.perception.conversation_finalizer import ConversationFinalizer
    from services.perception.text_llm import call_text
    from services.perception.token_budget import (
        resolve_output_cap,
    )
    from services.perception.vlm import get_active_provider
    from services.search.embeddings import (
        generate_embedding,
        get_embedding_provider,
    )

    cam = await db.get(Camera, row.camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")

    tx_rows = (
        await db.execute(
            select(Transcript)
            .where(Transcript.conversation_id == conversation_id)
            .where(Transcript.filtered.is_(False))
            .order_by(Transcript.started_at.asc())
        )
    ).scalars().all()
    if not tx_rows:
        raise HTTPException(status_code=400, detail="no transcripts to summarize")

    # Resolve provider with the same precedence the finalizer uses.
    provider: Provider | None = None
    for pid in (cam.summary_provider_id, cam.vlm_provider_id):
        if pid:
            provider = await db.get(Provider, pid)
            if provider:
                db.expunge(provider)
                break
    if provider is None:
        provider = await get_active_provider()
    if provider is None:
        raise HTTPException(status_code=500, detail="no provider configured")

    finalizer = ConversationFinalizer()
    raw = await finalizer._call_summary(provider, tx_rows)  # noqa: SLF001
    summary_text, cleaned_text = ConversationFinalizer._parse_summary_response(raw)
    if not summary_text:
        raise HTTPException(status_code=502, detail="summary call returned empty")

    embedding = None
    try:
        ep = await get_embedding_provider()
        embedding = await generate_embedding(summary_text, ep)
    except Exception:
        embedding = None

    row.summary_text = summary_text
    row.cleaned_text = cleaned_text
    row.summary_provider_name = provider.name
    if embedding is not None:
        row.embedding = embedding
    await db.commit()
    await db.refresh(row)
    return _serialize(row)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    tx_rows = (
        await db.execute(
            select(Transcript)
            .where(Transcript.conversation_id == conversation_id)
            .where(Transcript.filtered.is_(False))
            .order_by(Transcript.started_at.asc())
        )
    ).scalars().all()
    payload = _serialize(row)
    payload["transcripts"] = [_serialize_tx(t) for t in tx_rows]
    return payload
