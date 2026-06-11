"""Unit tests for VadSegmenter boundary behavior.

Synthetic int16 PCM. 16 kHz mono. We bypass silero by exploiting the
RMS fallback path. Loud frames are speech, silent frames are not.
The state machine should bound segments at MIN/MAX_SEG_MS and close
on SILENCE_CLOSE_MS of trailing silence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import numpy as np

from services.perception.audio.constants import (
    AUDIO_SAMPLE_RATE_HZ,
    AUDIO_VAD_MAX_SEG_MS,
    AUDIO_VAD_MIN_SEG_MS,
)
from services.perception.audio.types import PcmChunk
from services.perception.audio.vad import VadSegmenter

CAM = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _frame(loud: bool, ms: int = 30) -> bytes:
    n = AUDIO_SAMPLE_RATE_HZ * ms // 1000
    if loud:
        # 1 kHz tone at half scale. Easily above the RMS threshold.
        t = np.arange(n) / AUDIO_SAMPLE_RATE_HZ
        wave = (np.sin(2 * np.pi * 1000 * t) * 8000).astype(np.int16)
    else:
        wave = np.zeros(n, dtype=np.int16)
    return wave.tobytes()


def _chunk(payload: bytes, t0: datetime) -> PcmChunk:
    return PcmChunk(
        camera_id=CAM,
        capture_t=t0,
        pcm=payload,
        sample_rate=AUDIO_SAMPLE_RATE_HZ,
        channels=1,
    )


def _make_seg(seg: VadSegmenter):
    # Force RMS path. silero is not loaded in this test environment.
    seg._silero = None  # type: ignore[attr-defined]
    return seg


def test_emits_one_segment_after_speech_then_silence():
    seg = _make_seg(VadSegmenter(CAM))
    t0 = datetime(2026, 4, 23, tzinfo=timezone.utc)
    # 1.5 s speech + 1.0 s silence.
    speech = b"".join(_frame(True) for _ in range(50))
    silence = b"".join(_frame(False) for _ in range(34))
    out = list(seg.feed(_chunk(speech + silence, t0)))
    assert len(out) == 1
    assert out[0].duration_ms >= AUDIO_VAD_MIN_SEG_MS
    assert out[0].duration_ms <= 2000  # speech window only


def test_drops_too_short_speech():
    seg = _make_seg(VadSegmenter(CAM))
    t0 = datetime(2026, 4, 23, tzinfo=timezone.utc)
    # 200 ms speech then long silence. Below MIN_SEG_MS = 500.
    speech = b"".join(_frame(True) for _ in range(7))
    silence = b"".join(_frame(False) for _ in range(40))
    out = list(seg.feed(_chunk(speech + silence, t0)))
    assert out == []


def test_force_closes_at_max_seg_ms():
    seg = _make_seg(VadSegmenter(CAM))
    t0 = datetime(2026, 4, 23, tzinfo=timezone.utc)
    # 18 s of continuous speech. Should split into >= 1 segment that hit
    # the max bound, leaving the tail in progress.
    long_speech = b"".join(_frame(True) for _ in range(600))
    out = list(seg.feed(_chunk(long_speech, t0)))
    assert len(out) >= 1
    assert out[0].duration_ms <= AUDIO_VAD_MAX_SEG_MS + 30
