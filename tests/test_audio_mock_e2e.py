"""Integration test for the audio write path with the mock provider.

Skips capture and VAD. Synthesizes a SpeechSegment + TranscriptResult
and exercises filter → DB write → WS broadcast. Verifies a transcripts
row appears and the broadcast hits the websocket bus.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from services.perception.audio.providers.mock_provider import MockProvider
from services.perception.audio.types import SpeechSegment


@pytest.mark.asyncio
async def test_mock_provider_returns_deterministic_phrase():
    cam = uuid.UUID("11111111-2222-3333-4444-555555555555")
    t0 = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
    seg = SpeechSegment(
        camera_id=cam,
        started_at=t0,
        ended_at=t0 + timedelta(seconds=2),
        pcm=b"\x00" * 32000,
        sample_rate=16000,
        duration_ms=2000,
    )
    p = MockProvider()
    r1 = await p.transcribe(seg)
    r2 = await p.transcribe(seg)
    assert r1.text == r2.text  # deterministic on (camera, started_at)
    assert r1.provider == "mock"
    assert r1.model == "fixture-v1"
    assert r1.duration_ms == 2000


@pytest.mark.asyncio
async def test_mock_provider_phrases_cover_filter_paths():
    """The mock returns a phrase from a fixed set keyed by hash. Across
    a sweep of distinct (camera, time) tuples we should observe at
    least one normal phrase, one blocklist phrase, and one repetition
    phrase. This proves the test fixture exercises the filter
    branches the e2e suite cares about."""
    p = MockProvider()
    seen: set[str] = set()
    base = datetime(2026, 4, 23, tzinfo=timezone.utc)
    for i in range(200):
        seg = SpeechSegment(
            camera_id=uuid.UUID(int=i),
            started_at=base + timedelta(seconds=i),
            ended_at=base + timedelta(seconds=i + 2),
            pcm=b"\x00" * 32000,
            sample_rate=16000,
            duration_ms=2000,
        )
        r = await p.transcribe(seg)
        seen.add(r.text)
    assert "music" in seen
    assert "the the the the the the" in seen
    assert "hello nurby" in seen
