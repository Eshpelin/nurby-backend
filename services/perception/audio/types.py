"""Shared dataclasses for the audio pipeline.

Keep these dependency-free. types.py is imported by every audio module
and by tests, so it must not pull in faster-whisper or torch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class PcmChunk:
    """Raw PCM frames as they leave the ffmpeg pipe.

    ``capture_t`` is the authoritative wall-clock when the bytes were
    demuxed. Always use this. Do NOT recompute from queue arrival time
    further downstream.
    """

    camera_id: uuid.UUID
    capture_t: datetime
    pcm: bytes
    sample_rate: int
    channels: int = 1


@dataclass(slots=True)
class SpeechSegment:
    """A VAD-bounded slice of speech.

    ``pcm`` is mono int16 little-endian at ``sample_rate``.
    ``started_at`` and ``ended_at`` come from PcmChunk capture_t so the
    segment carries true wall-clock, not queue-arrival time.
    """

    camera_id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    pcm: bytes
    sample_rate: int
    duration_ms: int


@dataclass(slots=True)
class TranscriptResult:
    """Provider output before the hallucination filter and DB write.

    The filter consumes this directly. The write path turns it into a
    Transcript row. Keep field names aligned with the DB columns to
    minimize translation noise.
    """

    text: str
    language: str | None
    provider: str
    model: str
    confidence: float | None = None
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    words: list[dict] | None = None  # per-word timing if provider supplies it
    duration_ms: int = 0
    error: str | None = None  # set when transcribe failed terminally
    extra: dict = field(default_factory=dict)
