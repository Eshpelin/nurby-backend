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
from shared.models import AudioCapture, Camera, Transcript

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
        )
        db.add(transcript)
        await db.commit()
        await db.refresh(transcript)

    if keep:
        await _broadcast(camera_id, transcript.id, segment, result)
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
) -> None:
    payload: dict[str, Any] = {
        "type": "transcript_created",
        "camera_id": str(camera_id),
        "id": str(transcript_id) if transcript_id else None,
        "started_at": segment.started_at.isoformat(),
        "ended_at": segment.ended_at.isoformat(),
        "text": result.text,
        "provider": result.provider,
    }
    try:
        await ws_broadcast(payload)
    except Exception:
        logger.exception("WS broadcast failed for transcript %s", transcript_id)
