"""STT provider protocol and registry.

Providers are async callables that take raw PCM (mono int16 LE) plus
segment metadata and return a :class:`TranscriptResult`. The router in
``router.py`` owns retries, cooldowns, and worker concurrency.

The registry is module-level so the API and routes can list providers
without importing every backend on cold start. Backends are imported
lazily inside their factory function.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Protocol

from services.perception.audio.types import SpeechSegment, TranscriptResult

logger = logging.getLogger("nurby.perception.audio.stt")


class STTProvider(Protocol):
    kind: str  # 'faster_whisper' | 'mock' | 'openai_whisper' | ...
    name: str  # human-readable, "faster-whisper small.en"
    model: str
    is_local: bool

    async def transcribe(self, segment: SpeechSegment) -> TranscriptResult: ...


_FACTORIES: dict[str, Callable[..., Awaitable[STTProvider]]] = {}


def register_factory(kind: str, factory: Callable[..., Awaitable[STTProvider]]) -> None:
    """Register a provider factory under its ``kind`` string."""
    _FACTORIES[kind] = factory


async def build_provider(kind: str, **kwargs) -> STTProvider:
    """Resolve a registered provider by kind. Raises KeyError if missing."""
    if kind not in _FACTORIES:
        raise KeyError(f"unknown STT provider kind. {kind}")
    return await _FACTORIES[kind](**kwargs)


def known_kinds() -> list[str]:
    return sorted(_FACTORIES.keys())


# Eager-register the always-available providers. Heavy imports stay
# inside the factory bodies.

async def _faster_whisper_factory(model: str = "small.en", device: str = "cpu") -> STTProvider:
    from services.perception.audio.providers.faster_whisper_provider import (
        FasterWhisperProvider,
    )

    return FasterWhisperProvider(model=model, device=device)


async def _mock_factory(**_: object) -> STTProvider:
    from services.perception.audio.providers.mock_provider import MockProvider

    return MockProvider()


register_factory("faster_whisper", _faster_whisper_factory)
register_factory("mock", _mock_factory)
