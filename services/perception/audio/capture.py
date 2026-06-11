"""Audio capture pump.

Pulls the audio track off a MediaMTX path with PyAV, resamples to
mono int16 16 kHz, and emits :class:`PcmChunk` instances tagged with
authoritative wall-clock at packet demux. Bounded, drop-oldest queue.

This is intentionally a separate PyAV connection from the existing
sound classifier in :mod:`services.ingestion.audio_worker`. The
MediaMTX mux is the fan-out point so two consumers do not double-pull
the camera.

The pump runs PyAV in a worker thread and pushes into an asyncio
queue. The router (or any consumer) drains the queue from the event
loop side. Backpressure is drop-oldest. video must never block on
audio.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone

import numpy as np

from services.perception.audio.constants import (
    AUDIO_CHANNELS,
    AUDIO_PCM_QUEUE_MAX,
    AUDIO_SAMPLE_RATE_HZ,
)
from services.perception.audio.types import PcmChunk

logger = logging.getLogger("nurby.perception.audio.capture")

# Push at ~30ms cadence so the VAD frame state machine is fed in
# multiples of its frame size.
_CHUNK_MS = 30
_CHUNK_SAMPLES = AUDIO_SAMPLE_RATE_HZ * _CHUNK_MS // 1000


class AudioCapture:
    """One per-camera PyAV pump.

    Lifecycle. ``start`` spawns a background thread that opens the
    stream and pushes PcmChunks. ``stop`` flips a flag and lets the
    thread exit on its next iteration. The asyncio queue is drained
    by the router.
    """

    def __init__(self, camera_id: uuid.UUID, stream_url: str) -> None:
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.queue: asyncio.Queue[PcmChunk] = asyncio.Queue(maxsize=AUDIO_PCM_QUEUE_MAX)
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.dropped_chunks = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._loop = asyncio.get_event_loop()
        self._running.set()
        self._thread = threading.Thread(
            target=self._run_pump,
            name=f"audio-capture-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()

    # ---- pump --------------------------------------------------------

    def _run_pump(self) -> None:
        try:
            import av  # type: ignore
        except ImportError:
            logger.error("PyAV unavailable. STT capture disabled for %s", self.camera_id)
            return

        backoff = 1.0
        while self._running.is_set():
            try:
                self._open_and_pump(av)
                backoff = 1.0
            except Exception:
                logger.exception("STT capture error for %s. reconnecting", self.camera_id)
                self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _open_and_pump(self, av) -> None:
        container = av.open(
            self.stream_url,
            options={"rtsp_transport": "tcp", "stimeout": "5000000"},
            timeout=10,
        )
        try:
            astreams = [s for s in container.streams if s.type == "audio"]
            if not astreams:
                logger.info("no audio track for %s", self.camera_id)
                self._sleep(30)
                return

            astream = astreams[0]
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=AUDIO_SAMPLE_RATE_HZ,
            )

            buf = bytearray()
            for packet in container.demux(astream):
                if not self._running.is_set():
                    break
                # Authoritative wall-clock at packet demux. Single
                # source of truth for everything downstream.
                capture_t = datetime.now(timezone.utc)
                for frame in packet.decode():
                    resampled = resampler.resample(frame)
                    if not isinstance(resampled, list):
                        resampled = [resampled]
                    for rf in resampled:
                        if rf is None:
                            continue
                        arr = rf.to_ndarray()
                        # AudioResampler with layout=mono yields shape (1, N).
                        # Coerce to 1-D int16 little-endian bytes.
                        arr = arr.reshape(-1).astype(np.int16, copy=False)
                        buf.extend(arr.tobytes())
                        while len(buf) >= _CHUNK_SAMPLES * 2:
                            chunk_bytes = bytes(buf[: _CHUNK_SAMPLES * 2])
                            del buf[: _CHUNK_SAMPLES * 2]
                            self._enqueue(
                                PcmChunk(
                                    camera_id=self.camera_id,
                                    capture_t=capture_t,
                                    pcm=chunk_bytes,
                                    sample_rate=AUDIO_SAMPLE_RATE_HZ,
                                    channels=AUDIO_CHANNELS,
                                )
                            )
        finally:
            try:
                container.close()
            except Exception:
                pass

    def _enqueue(self, chunk: PcmChunk) -> None:
        """Drop-oldest backpressure. The router can be slow without
        the capture thread blocking the demux loop.
        """
        if self._loop is None:
            return
        loop = self._loop

        def _put() -> None:
            try:
                self.queue.put_nowait(chunk)
            except asyncio.QueueFull:
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self.dropped_chunks += 1
                try:
                    self.queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    self.dropped_chunks += 1

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            # Loop closed during shutdown. drop silently.
            pass

    def _sleep(self, seconds: float) -> None:
        # Sleep that respects the stop flag. Avoids the 30 s reconnect
        # backoff sticking around after the user disables audio.
        end = seconds
        while end > 0 and self._running.is_set():
            chunk = min(0.5, end)
            self._running.wait(timeout=chunk)
            end -= chunk
