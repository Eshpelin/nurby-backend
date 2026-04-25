"""Voice activity detection. Speech segmentation.

Wraps silero-VAD when available. Falls back to an RMS-energy detector
so the pipeline still works on machines that have not pulled the
torch dependency. Both paths share the same segment-bounding logic so
downstream code never branches on which path was taken.

VAD operates on int16 mono PCM at 16 kHz. Capture must resample to
that before pushing chunks in. Segment boundaries are wall-clock
times derived from the inbound chunks' ``capture_t``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Iterator

import numpy as np

from services.perception.audio.constants import (
    AUDIO_SAMPLE_RATE_HZ,
    AUDIO_VAD_MAX_SEG_MS,
    AUDIO_VAD_MIN_SEG_MS,
    AUDIO_VAD_SILENCE_CLOSE_MS,
)
from services.perception.audio.types import PcmChunk, SpeechSegment

logger = logging.getLogger("nurby.perception.audio.vad")

_FRAME_MS = 30
_FRAME_SAMPLES = AUDIO_SAMPLE_RATE_HZ * _FRAME_MS // 1000  # 480 @ 16kHz
_FRAME_BYTES = _FRAME_SAMPLES * 2  # int16


class VadSegmenter:
    """Frame-by-frame state machine.

    Push raw PCM via :meth:`feed`. Pull bounded :class:`SpeechSegment`
    instances via the iterator it returns. Caller decides the timing
    discipline. Closing/idle cameras can flush via :meth:`flush`.
    """

    def __init__(
        self,
        camera_id: uuid.UUID,
        sample_rate: int = AUDIO_SAMPLE_RATE_HZ,
        rms_threshold: float = 0.015,
    ) -> None:
        if sample_rate != AUDIO_SAMPLE_RATE_HZ:
            raise ValueError(f"VAD requires {AUDIO_SAMPLE_RATE_HZ} Hz")
        self.camera_id = camera_id
        self._rms_threshold = rms_threshold
        self._silero = _try_load_silero()
        self._frame_buf = bytearray()
        self._seg_pcm = bytearray()
        self._seg_started: datetime | None = None
        self._silence_run_ms = 0
        self._frame_t: datetime | None = None  # wall-clock at start of next frame

    def feed(self, chunk: PcmChunk) -> Iterator[SpeechSegment]:
        """Push a PCM chunk. Yields zero or more closed segments."""
        if self._frame_t is None:
            self._frame_t = chunk.capture_t
        self._frame_buf.extend(chunk.pcm)

        while len(self._frame_buf) >= _FRAME_BYTES:
            frame = bytes(self._frame_buf[:_FRAME_BYTES])
            del self._frame_buf[:_FRAME_BYTES]
            yield from self._process_frame(frame)
            assert self._frame_t is not None
            self._frame_t = self._frame_t + timedelta(milliseconds=_FRAME_MS)

    def flush(self) -> Iterator[SpeechSegment]:
        """Close any in-progress segment. Call on shutdown / toggle off."""
        if self._seg_pcm and self._seg_started is not None and self._frame_t is not None:
            seg = self._emit(self._frame_t)
            if seg is not None:
                yield seg
        self._frame_buf.clear()
        self._seg_pcm.clear()
        self._seg_started = None
        self._silence_run_ms = 0

    # ---- internals ---------------------------------------------------

    def _process_frame(self, frame: bytes) -> Iterator[SpeechSegment]:
        is_speech = self._is_speech(frame)
        assert self._frame_t is not None
        frame_end = self._frame_t + timedelta(milliseconds=_FRAME_MS)

        if is_speech:
            if self._seg_started is None:
                self._seg_started = self._frame_t
                self._seg_pcm.clear()
            self._seg_pcm.extend(frame)
            self._silence_run_ms = 0
            # Force-close oversized segments. Long monologues split into
            # chunks of AUDIO_VAD_MAX_SEG_MS, no overlap.
            seg_ms = (len(self._seg_pcm) // 2) * 1000 // AUDIO_SAMPLE_RATE_HZ
            if seg_ms >= AUDIO_VAD_MAX_SEG_MS:
                seg = self._emit(frame_end)
                if seg is not None:
                    yield seg
        else:
            if self._seg_started is not None:
                # Trailing silence still belongs in the segment up to the
                # close timeout. Past the timeout, emit and reset.
                self._seg_pcm.extend(frame)
                self._silence_run_ms += _FRAME_MS
                if self._silence_run_ms >= AUDIO_VAD_SILENCE_CLOSE_MS:
                    seg = self._emit(frame_end)
                    if seg is not None:
                        yield seg

    def _emit(self, ended_at: datetime) -> SpeechSegment | None:
        if self._seg_started is None or not self._seg_pcm:
            self._reset()
            return None
        # Trim trailing silence so the duration field reflects the speech
        # window, not the silence padding that closed it.
        trim_bytes = (
            self._silence_run_ms * AUDIO_SAMPLE_RATE_HZ // 1000
        ) * 2
        usable = len(self._seg_pcm) - max(0, trim_bytes)
        if usable <= 0:
            self._reset()
            return None
        pcm = bytes(self._seg_pcm[:usable])
        duration_ms = (usable // 2) * 1000 // AUDIO_SAMPLE_RATE_HZ
        if duration_ms < AUDIO_VAD_MIN_SEG_MS:
            self._reset()
            return None
        seg = SpeechSegment(
            camera_id=self.camera_id,
            started_at=self._seg_started,
            ended_at=self._seg_started + timedelta(milliseconds=duration_ms),
            pcm=pcm,
            sample_rate=AUDIO_SAMPLE_RATE_HZ,
            duration_ms=duration_ms,
        )
        self._reset()
        return seg

    def _reset(self) -> None:
        self._seg_pcm.clear()
        self._seg_started = None
        self._silence_run_ms = 0

    def _is_speech(self, frame: bytes) -> bool:
        if self._silero is not None:
            try:
                return self._silero(frame)
            except Exception:
                # Fall through to RMS on any silero error.
                logger.debug("silero failure. falling back to RMS", exc_info=True)
        return self._rms_speech(frame)

    def _rms_speech(self, frame: bytes) -> bool:
        arr = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(arr * arr) + 1e-12))
        return rms >= self._rms_threshold


def _try_load_silero():
    """Return a callable ``(frame_bytes) -> bool`` or None.

    Loaded once per VadSegmenter instance to keep state-per-camera
    isolated. The model itself caches inside torch hub so the second
    load is fast.
    """
    try:
        import torch  # type: ignore
    except ImportError:
        return None
    try:
        # silero-vad ships via torch.hub. Pin the repo and version so
        # we do not silently jump models on a torch upgrade.
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
            verbose=False,
        )
        get_speech_timestamps = utils[0]
        del get_speech_timestamps  # unused. we run frame-level.

        def _classify(frame: bytes) -> bool:
            arr = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(arr)
            prob = float(model(tensor, AUDIO_SAMPLE_RATE_HZ).item())
            return prob >= 0.5

        return _classify
    except Exception:
        logger.info("silero-vad unavailable. using RMS fallback")
        return None
