"""faster-whisper local STT provider.

Lazy-loads the model on first transcribe call so import-time stays
cheap and processes that never touch audio do not pay for ctranslate2.
The model object is shared across all cameras within this provider
instance. faster-whisper releases the GIL during inference, so a
single asyncio.to_thread is enough to keep the event loop responsive
even on CPU.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncio
import io

from services.perception.audio.types import SpeechSegment, TranscriptResult

logger = logging.getLogger("nurby.perception.audio.faster_whisper")


class FasterWhisperProvider:
    kind = "faster_whisper"
    is_local = True

    def __init__(self, model: str = "small.en", device: str = "cpu") -> None:
        self.model = model
        self.name = f"faster-whisper {model}"
        self._device = device
        self._whisper: Any | None = None
        self._lock = asyncio.Lock()

    async def _ensure_model(self) -> Any:
        if self._whisper is not None:
            return self._whisper
        async with self._lock:
            if self._whisper is None:
                # Heavy import. Keep inside the lock so concurrent
                # callers do not double-load.
                from faster_whisper import WhisperModel

                logger.info("loading faster-whisper model %s on %s", self.model, self._device)
                self._whisper = await asyncio.to_thread(
                    WhisperModel, self.model, device=self._device, compute_type="int8"
                )
        return self._whisper

    async def transcribe(self, segment: SpeechSegment) -> TranscriptResult:
        whisper = await self._ensure_model()

        def _run() -> TranscriptResult:
            import numpy as np

            audio = np.frombuffer(segment.pcm, dtype=np.int16).astype("float32") / 32768.0
            segments, info = whisper.transcribe(
                audio,
                language="en",
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )
            seg_list = list(segments)
            text = " ".join(s.text.strip() for s in seg_list).strip()
            avg_logprob = (
                sum(s.avg_logprob for s in seg_list) / len(seg_list) if seg_list else None
            )
            no_speech = (
                sum(s.no_speech_prob for s in seg_list) / len(seg_list) if seg_list else None
            )
            words: list[dict] | None = None
            return TranscriptResult(
                text=text,
                language=info.language if info else None,
                provider=self.kind,
                model=self.model,
                confidence=None,
                no_speech_prob=no_speech,
                avg_logprob=avg_logprob,
                words=words,
                duration_ms=segment.duration_ms,
            )

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:
            logger.exception("faster-whisper transcribe failed")
            return TranscriptResult(
                text="",
                language=None,
                provider=self.kind,
                model=self.model,
                duration_ms=segment.duration_ms,
                error=str(exc),
            )
