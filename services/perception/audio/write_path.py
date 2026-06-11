"""Write path for STT results.

Sequence on each TranscriptResult.

1. Apply hallucination filter. Filtered rows still get inserted with
   ``filtered=true`` for audit visibility.
2. If ``audio_store_raw`` is on, encode and store opus, capture row.
3. Insert transcripts row (unless transcript_store == 'off').
4. (Phase 2) query overlapping observations and schedule enrichment.
   Phase 1 just emits the WS event.
5. WS broadcast ``transcript_created``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from services.api.ws import broadcast as ws_broadcast
from services.perception.audio import metrics
from services.perception.audio.enrichment import (
    schedule_enrichment_for_transcript,
)
from services.perception.audio.hallucination_filter import filter_hallucination
from services.perception.audio.speaker_video import attribute_by_video
from services.perception.audio.storage import write_opus
from services.perception.audio.types import SpeechSegment, TranscriptResult
from shared.database import async_session
from shared.models import AudioCapture, Camera, Conversation, Person, Transcript

logger = logging.getLogger("nurby.perception.audio.write")


async def write_transcript(
    camera_id: uuid.UUID, segment: SpeechSegment, result: TranscriptResult
) -> None:
    """Bound to a CameraAudioRouter via write_callback. Never raises."""
    try:
        await _write(camera_id, segment, result)
    except Exception:
        logger.exception("transcript write failed camera=%s", camera_id)


async def _write(
    camera_id: uuid.UUID, segment: SpeechSegment, result: TranscriptResult
) -> None:
    keep, reason = filter_hallucination(result)
    if not keep:
        metrics.incr(
            "stt_hallucinations_filtered_total",
            {"reason": reason},
        )

    # Re-fetch the latest camera row. Toggles may have flipped between
    # capture start and STT completion. The router catches the next
    # cycle, but for in-flight segments we honor the current setting.
    async with async_session() as db:
        cam = await db.get(Camera, camera_id)
        if cam is None:
            return

        store_text = (cam.transcript_store or "full") != "off"
        store_raw = bool(cam.audio_store_raw)

        capture_id: uuid.UUID | None = None
        if store_raw:
            new_capture_id = uuid.uuid4()
            stored = await write_opus(segment, new_capture_id)
            if stored is not None:
                file_path, size_bytes = stored
                cap = AudioCapture(
                    id=new_capture_id,
                    camera_id=camera_id,
                    started_at=segment.started_at,
                    ended_at=segment.ended_at,
                    duration_ms=segment.duration_ms,
                    file_path=file_path,
                    codec="opus",
                    sample_rate=segment.sample_rate,
                    size_bytes=size_bytes,
                )
                db.add(cap)
                capture_id = new_capture_id

        # Filtered + transcript_store off = nothing to insert. Skip the
        # row entirely so the table is not polluted with empty stubs.
        if not keep and not store_text:
            await db.commit()
            return

        if not store_text and keep:
            # Operator opted out of full-text storage. Skip insert. We
            # still surfaced the WS event below for live captions.
            await db.commit()
            await _broadcast(camera_id, None, segment, result)
            return

        # Tier A speaker attribution. Cheap. one indexed SELECT against
        # observations. Fail open. an attribution miss never blocks the
        # transcript write.
        attribution = None
        try:
            attribution = await attribute_by_video(
                db, camera_id, segment.started_at, segment.ended_at
            )
        except Exception:
            logger.exception("speaker attribution failed camera=%s", camera_id)

        # Conversation assignment. Group consecutive transcripts on
        # this camera into a rolling artifact so the timeline can show
        # one card per actual conversation instead of N cards per VAD
        # segment. Boundary is the camera's gap_seconds. Filtered rows
        # do not open or extend a conversation.
        conversation_id: uuid.UUID | None = None
        if keep:
            conversation_id = await _assign_conversation(
                db, cam, segment.started_at, segment.ended_at
            )

        transcript = Transcript(
            camera_id=camera_id,
            audio_capture_id=capture_id,
            started_at=segment.started_at,
            ended_at=segment.ended_at,
            text=result.text,
            original_text=result.text,
            text_edited=False,
            language=result.language,
            provider=result.provider,
            model=result.model,
            confidence=result.confidence,
            no_speech_prob=result.no_speech_prob,
            words=result.words,
            embedding=None,  # Phase 2 backfill fills this asynchronously
            filtered=not keep,
            speaker_person_id=attribution.person_id if attribution else None,
            speaker_confidence=attribution.confidence if attribution else None,
            speaker_source=attribution.source if attribution else None,
            conversation_id=conversation_id,
        )
        db.add(transcript)
        await db.commit()
        await db.refresh(transcript)

    speaker_name: str | None = None
    if keep and attribution and attribution.person_id:
        try:
            async with async_session() as db:
                p = await db.get(Person, attribution.person_id)
                if p:
                    speaker_name = p.display_name
        except Exception:
            logger.debug("speaker name lookup failed", exc_info=True)

    if keep:
        await _broadcast(
            camera_id, transcript.id, segment, result, conversation_id, speaker_name
        )
        # Fire rule engine for speech_phrase triggers. Done outside
        # the DB session so a slow rule action does not stall the
        # transcript commit.
        try:
            from services.events.engine import RuleEngine

            engine = RuleEngine()
            await engine.evaluate(
                {
                    "observation_id": None,
                    "camera_id": str(camera_id),
                    "timestamp": segment.started_at.isoformat(),
                    "transcript": {
                        "id": str(transcript.id),
                        "text": result.text,
                        "language": result.language,
                        "speaker_name": speaker_name,
                    },
                    "confidence": result.confidence or 0.0,
                }
            )
        except Exception:
            logger.exception("rule engine failed for transcript")
        # Schedule VLM re-enrichment for any observations this transcript
        # overlaps. Debounced inside the enrichment module so multiple
        # transcripts on the same observation do not amplify VLM load.
        try:
            schedule_enrichment_for_transcript(
                camera_id, segment.started_at, segment.ended_at
            )
        except Exception:
            logger.exception("enrichment scheduling failed transcript=%s", transcript.id)


async def _broadcast(
    camera_id: uuid.UUID,
    transcript_id: uuid.UUID | None,
    segment: SpeechSegment,
    result: TranscriptResult,
    conversation_id: uuid.UUID | None = None,
    speaker_name: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "transcript_created",
        "camera_id": str(camera_id),
        "id": str(transcript_id) if transcript_id else None,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "started_at": segment.started_at.isoformat(),
        "ended_at": segment.ended_at.isoformat(),
        "text": result.text,
        "provider": result.provider,
        "speaker_name": speaker_name,
    }
    try:
        await ws_broadcast(payload)
    except Exception:
        logger.exception("WS broadcast failed for transcript %s", transcript_id)
    if conversation_id is not None:
        # Separate event so the UI can collapse + update a single
        # conversation card without re-fetching the whole timeline.
        try:
            await ws_broadcast(
                {
                    "type": "conversation_updated",
                    "camera_id": str(camera_id),
                    "conversation_id": str(conversation_id),
                    "transcript_id": str(transcript_id) if transcript_id else None,
                    "started_at": segment.started_at.isoformat(),
                    "ended_at": segment.ended_at.isoformat(),
                    "text": result.text,
                    "speaker_name": speaker_name,
                }
            )
        except Exception:
            logger.exception("conversation_updated WS failed")


async def _assign_conversation(
    db,
    cam: Camera,
    seg_start,
    seg_end,
) -> uuid.UUID | None:
    """Find an open conversation on this camera within the gap window
    and extend it. Otherwise open a new one.

    The query targets the partial index on (camera_id, finalized,
    ended_at_provisional), so this is one indexed lookup per
    transcript.
    """
    gap = max(5, int(getattr(cam, "conversation_gap_seconds", 30) or 30))
    cutoff = seg_start - timedelta(seconds=gap)
    try:
        existing = (
            await db.execute(
                select(Conversation)
                .where(Conversation.camera_id == cam.id)
                .where(Conversation.finalized.is_(False))
                .where(Conversation.ended_at_provisional >= cutoff)
                .order_by(Conversation.ended_at_provisional.desc())
                .limit(1)
            )
        ).scalars().first()
        if existing is not None:
            existing.ended_at_provisional = seg_end
            existing.transcript_count = (existing.transcript_count or 0) + 1
            return existing.id

        new_conv = Conversation(
            camera_id=cam.id,
            started_at=seg_start,
            ended_at_provisional=seg_end,
            transcript_count=1,
            finalized=False,
        )
        db.add(new_conv)
        await db.flush()
        return new_conv.id
    except Exception:
        logger.exception("conversation assignment failed camera=%s", cam.id)
        return None
