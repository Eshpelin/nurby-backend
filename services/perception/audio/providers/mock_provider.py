"""Mock STT provider for tests and offline development.

Returns deterministic transcript text derived from the segment duration
and a small lookup table on the camera_id. Never raises. Never blocks.
Used by the integration test suite to exercise the full router →
hallucination filter → DB write → WS broadcast flow without pulling in
faster-whisper.
"""

from __future__ import annotations

import asyncio
import hashlib

from services.perception.audio.types import SpeechSegment, TranscriptResult

_PHRASES = (
    "hello nurby",
    "the front door is open",
    "someone is at the door",
    "is the baby still asleep",
    "i need to grab the package",
    "music",  # exercises the blocklist path
    "the the the the the the",  # exercises the repetition path
)


class MockProvider:
    kind = "mock"
    name = "mock"
    model = "fixture-v1"
    is_local = True

    def __init__(self, latency_ms: int = 5) -> None:
        self._latency_s = latency_ms / 1000.0

    async def transcribe(self, segment: SpeechSegment) -> TranscriptResult:
        await asyncio.sleep(self._latency_s)
        # Deterministic phrase pick keyed by (camera, started_at). Tests
        # can map known segments to known phrases without smuggling
        # state through globals.
        h = hashlib.md5(
            f"{segment.camera_id}-{segment.started_at.isoformat()}".encode()
        ).digest()
        phrase = _PHRASES[h[0] % len(_PHRASES)]
        return TranscriptResult(
            text=phrase,
            language="en",
            provider=self.kind,
            model=self.model,
            confidence=0.95,
            no_speech_prob=0.05,
            avg_logprob=-0.3,
            duration_ms=segment.duration_ms,
        )
