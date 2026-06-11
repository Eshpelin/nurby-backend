"""faster-whisper local STT provider.

Lazy-loads the model on first transcribe call so import-time stays
cheap and processes that never touch audio do not pay for ctranslate2.

The loaded model is shared process-wide via :mod:`shared_resources`,
keyed by ``(model, device)``. The router builds one provider instance
per camera, so without this every camera would load its own multi-
hundred-MB copy of the same weights. The per-camera bit (language) is a
transcribe-time argument, not part of the model, so one model serves all
cameras. faster-whisper releases the GIL during inference, so several
worker threads can transcribe concurrent segments against the one model.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.perception.audio.types import SpeechSegment, TranscriptResult

logger = logging.getLogger("nurby.perception.audio.faster_whisper")


class FasterWhisperProvider:
    kind = "faster_whisper"
    is_local = True

    def __init__(
        self,
        model: str = "small.en",
        device: str = "cpu",
        language: str | None = "en",
        beam_size: int = 1,
        condition_on_previous_text: bool = False,
        no_speech_threshold: float = 0.6,
    ) -> None:
        self.model = model
        self.name = f"faster-whisper {model}"
        self._device = device
        self._beam_size = beam_size
        self._condition_on_previous_text = condition_on_previous_text
        self._no_speech_threshold = no_speech_threshold
        # Whisper accepts None to auto-detect. Some users have multi-
        # language households or non-English cameras (intercom in front
        # door area, kitchen with shared family). Per-camera setting
        # flows in via cam.audio_language.
        self._language: str | None = language or None
        self._whisper: Any | None = None

    async def _ensure_model(self) -> Any:
        if self._whisper is not None:
            return self._whisper

        async def _load() -> Any:
            # Heavy import kept inside the factory so processes that never
            # transcribe do not pay for ctranslate2.
            from faster_whisper import WhisperModel

            logger.info(
                "loading faster-whisper model %s on %s", self.model, self._device
            )
            return await asyncio.to_thread(
                WhisperModel, self.model, device=self._device, compute_type="int8"
            )

        # Shared process-wide. Every camera with the same (model, device)
        # reuses one loaded model instead of loading its own copy.
        from services.perception.audio import shared_resources

        self._whisper = await shared_resources.get_or_create(
            ("faster_whisper", self.model, self._device), _load
        )
        return self._whisper

    async def transcribe(self, segment: SpeechSegment) -> TranscriptResult:
        whisper = await self._ensure_model()

        def _run() -> TranscriptResult:
            import numpy as np

            audio = np.frombuffer(segment.pcm, dtype=np.int16).astype("float32") / 32768.0
            # language=None lets Whisper auto-detect. Pinning the
            # language is faster and more accurate when known.
            segments, info = whisper.transcribe(
                audio,
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=False,
                condition_on_previous_text=self._condition_on_previous_text,
                no_speech_threshold=self._no_speech_threshold,
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
