"""Observation enrichment task. Phase 2.

When a transcript lands or an observation closes, we want to re-run
the VLM with ``heard_text`` so the description on the timeline
reflects what was said too. This module coordinates the scheduling.

Design decisions.

- Event-driven, not polling. ``loop.call_later`` defers each run by
  ``AUDIO_ENRICHMENT_DELAY_S`` so concurrent late transcripts collapse
  into a single re-enrichment.
- Per-observation cooldown. ``AUDIO_VLM_RERUN_COOLDOWN_S`` prevents
  thrash if a long monologue produces many transcripts.
- Pure scheduler. The actual VLM call lives in ``vlm_enrich_observation``
  which takes a DB session, fetches the obs, builds heard_text, and
  patches the row. Easy to unit-test in isolation.
- Fail open. Any error logs and bails. Enrichment is opportunistic.

The scheduler cannot be a singleton across processes. The ingestion
process spawns audio routers and writes transcripts, so enrichment
runs there. The VLM HTTP call is the work, not the bottleneck.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, select

from services.perception.audio.constants import (
    AUDIO_ENRICHMENT_DELAY_S,
    AUDIO_LATE_TRANSCRIPT_WINDOW_S,
    AUDIO_VLM_RERUN_COOLDOWN_S,
)
from shared.database import async_session
from shared.models import Observation, Transcript

logger = logging.getLogger("nurby.perception.audio.enrichment")


# Per-obs cooldown. monotonic timestamp of the last enrichment attempt.
_last_run: dict[uuid.UUID, float] = {}
# Per-obs scheduled handle so a second schedule call is a no-op while a
# run is already pending.
_pending: dict[uuid.UUID, object] = {}


def schedule_enrichment(observation_id: uuid.UUID, delay: float = AUDIO_ENRICHMENT_DELAY_S) -> None:
    """Defer a VLM re-run for this observation. Idempotent."""
    if observation_id in _pending:
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return

    def _trigger() -> None:
        _pending.pop(observation_id, None)
        # Skip if cooldown still active. Plan §7.1 caps at one run per
        # AUDIO_VLM_RERUN_COOLDOWN_S regardless of how many transcripts
        # arrive.
        last = _last_run.get(observation_id, 0.0)
        if time.monotonic() - last < AUDIO_VLM_RERUN_COOLDOWN_S:
            return
        loop.create_task(_run_enrichment(observation_id))

    handle = loop.call_later(delay, _trigger)
    _pending[observation_id] = handle


def schedule_enrichment_for_transcript(
    camera_id: uuid.UUID, started_at: datetime, ended_at: datetime
) -> None:
    """Find observations whose [started_at, ended_at + late_window] window
    overlaps the transcript and schedule each. Late-arrival policy from
    plan §7.2 lives here. transcripts that land after an obs closed but
    within AUDIO_LATE_TRANSCRIPT_WINDOW_S still trigger one re-run.
    """
    asyncio.create_task(
        _resolve_and_schedule(camera_id, started_at, ended_at)
    )


async def _resolve_and_schedule(
    camera_id: uuid.UUID, started_at: datetime, ended_at: datetime
) -> None:
    try:
        async with async_session() as db:
            late_window = timedelta(seconds=AUDIO_LATE_TRANSCRIPT_WINDOW_S)
            q = select(Observation.id).where(
                and_(
                    Observation.camera_id == camera_id,
                    Observation.started_at <= ended_at,
                    (Observation.ended_at.is_(None))
                    | (Observation.ended_at + late_window >= started_at),
                )
            )
            rows = (await db.execute(q)).scalars().all()
        for obs_id in rows:
            schedule_enrichment(obs_id)
    except Exception:
        logger.exception("late-transcript enrichment lookup failed")


async def _run_enrichment(observation_id: uuid.UUID) -> None:
    """Pull overlapping transcripts, build heard_text, run VLM, patch."""
    _last_run[observation_id] = time.monotonic()
    try:
        async with async_session() as db:
            obs = await db.get(Observation, observation_id)
            if obs is None:
                return
            heard = await _gather_heard_text(db, obs)
            if not heard:
                return

        # Defer to the VLM enrichment client. Imported lazily so this
        # module stays importable in environments without httpx/cv2.
        try:
            from services.perception.audio.vlm_enrichment import enrich_observation
        except ImportError:
            logger.warning("vlm_enrichment module unavailable")
            return

        await enrich_observation(observation_id, heard)
    except Exception:
        logger.exception("enrichment failed for obs %s", observation_id)


async def _gather_heard_text(db, obs: Observation) -> str:
    """Concat the transcript text of segments overlapping this obs's
    window. Filtered transcripts are excluded. Speaker names are
    prepended when Tier A attributed."""
    obs_end = obs.ended_at or datetime.now(timezone.utc)
    q = (
        select(Transcript)
        .where(
            and_(
                Transcript.camera_id == obs.camera_id,
                Transcript.filtered.is_(False),
                Transcript.started_at <= obs_end,
                Transcript.ended_at >= obs.started_at,
            )
        )
        .order_by(Transcript.started_at.asc())
    )
    rows = (await db.execute(q)).scalars().all()
    parts: list[str] = []
    for t in rows:
        prefix = ""
        if t.speaker_person_id and t.speaker_source in ("video", "fused", "voice"):
            # We do not have the person row in this scope. The speaker
            # name is rendered by the UI from the transcripts read API.
            # For VLM context, anonymize as 'Speaker' so the VLM treats
            # it as quoted speech without leaking names into the prompt.
            prefix = "Speaker. "
        parts.append(f"{prefix}\"{t.text.strip()}\"")
    return " ".join(parts).strip()
