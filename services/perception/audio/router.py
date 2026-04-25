"""Per-camera STT router.

Owns the lifecycle. capture → VAD → bounded segment queue → STT
worker pool → hallucination filter → DB write → WS broadcast. One
:class:`CameraAudioRouter` per camera. Cheap to start and stop. Toggle
flips on the camera row are reconciled by :class:`AudioPipelineManager`
in this module on every poll.

The router never imports the heavy STT backend at import time. It
resolves a provider via :mod:`stt` only when the camera enables
transcription.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Iterable

from services.perception.audio import metrics
from services.perception.audio.capture import AudioCapture
from services.perception.audio.constants import (
    AUDIO_SEGMENT_QUEUE_MAX,
    AUDIO_STT_COOLDOWN_S,
    AUDIO_STT_RETRIES,
    AUDIO_STT_WORKERS_LOCAL,
)
from services.perception.audio.stt import STTProvider, build_provider
from services.perception.audio.types import SpeechSegment
from services.perception.audio.vad import VadSegmenter

logger = logging.getLogger("nurby.perception.audio.router")


SegmentSink = "asyncio.Queue[SpeechSegment]"


class CameraAudioRouter:
    """Wires capture → VAD → STT for one camera.

    The write path (filter, DB insert, WS broadcast) is injected via
    ``write_callback`` so this module stays decoupled from the API and
    DB layer. write_callback signature.

        async def write(camera_id, segment, result) -> None
    """

    def __init__(
        self,
        camera_id: uuid.UUID,
        stream_url: str,
        provider_kind: str,
        provider_kwargs: dict,
        write_callback,
    ) -> None:
        self.camera_id = camera_id
        self.stream_url = stream_url
        self._provider_kind = provider_kind
        self._provider_kwargs = provider_kwargs
        self._write = write_callback

        self._capture = AudioCapture(camera_id, stream_url)
        self._vad = VadSegmenter(camera_id)
        self._segments: asyncio.Queue[SpeechSegment] = asyncio.Queue(
            maxsize=AUDIO_SEGMENT_QUEUE_MAX
        )
        self._provider: STTProvider | None = None
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()
        self._cooldown_until = 0.0

    # ---- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        self._provider = await build_provider(
            self._provider_kind, **self._provider_kwargs
        )
        self._capture.start()
        self._tasks.append(asyncio.create_task(self._segmenter_loop()))
        for i in range(AUDIO_STT_WORKERS_LOCAL):
            self._tasks.append(asyncio.create_task(self._stt_worker(i)))
        logger.info(
            "audio router started camera=%s provider=%s", self.camera_id, self._provider_kind
        )

    async def stop(self) -> None:
        self._stopping.set()
        self._capture.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Drain the segment queue. Any pending work is dropped on toggle-off.
        while not self._segments.empty():
            try:
                self._segments.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._tasks.clear()
        logger.info("audio router stopped camera=%s", self.camera_id)

    # ---- segmenter loop ---------------------------------------------

    async def _segmenter_loop(self) -> None:
        try:
            while not self._stopping.is_set():
                chunk = await self._capture.queue.get()
                for seg in self._vad.feed(chunk):
                    self._enqueue_segment(seg)
                    asyncio.create_task(self._broadcast_vad_pulse(seg))
        except asyncio.CancelledError:
            for seg in self._vad.flush():
                self._enqueue_segment(seg)
            raise

    async def _broadcast_vad_pulse(self, seg: SpeechSegment) -> None:
        # Lightweight UI signal. fires the moment a speech segment closes,
        # before STT runs. Good enough to drive a tile "audio active" dot
        # without waiting for transcription.
        try:
            from services.api.ws import broadcast as ws_broadcast

            await ws_broadcast(
                {
                    "type": "vad_pulse",
                    "camera_id": str(self.camera_id),
                    "started_at": seg.started_at.isoformat(),
                    "ended_at": seg.ended_at.isoformat(),
                    "duration_ms": seg.duration_ms,
                }
            )
        except Exception:
            logger.debug("vad_pulse broadcast failed", exc_info=True)

    def _enqueue_segment(self, seg: SpeechSegment) -> None:
        try:
            self._segments.put_nowait(seg)
        except asyncio.QueueFull:
            metrics.incr(
                "stt_segments_dropped_total",
                {"camera": str(self.camera_id), "stage": "segment_queue"},
            )
            try:
                self._segments.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._segments.put_nowait(seg)
            except asyncio.QueueFull:
                metrics.incr(
                    "stt_segments_dropped_total",
                    {"camera": str(self.camera_id), "stage": "segment_queue_full"},
                )

    # ---- stt worker --------------------------------------------------

    async def _stt_worker(self, idx: int) -> None:
        try:
            while not self._stopping.is_set():
                seg = await self._segments.get()
                await self._dispatch(seg)
        except asyncio.CancelledError:
            raise

    async def _dispatch(self, seg: SpeechSegment) -> None:
        if self._provider is None:
            return
        if time.monotonic() < self._cooldown_until:
            metrics.incr(
                "stt_segments_total",
                {
                    "camera": str(self.camera_id),
                    "provider": self._provider.kind,
                    "status": "cooldown_skipped",
                },
            )
            return
        provider = self._provider
        last_exc: Exception | None = None
        for attempt in range(1, AUDIO_STT_RETRIES + 1):
            t0 = time.monotonic()
            try:
                result = await provider.transcribe(seg)
                latency = time.monotonic() - t0
                metrics.observe_latency(
                    "stt_latency_seconds", latency, {"provider": provider.kind}
                )
                if result.error:
                    raise RuntimeError(result.error)
                metrics.incr(
                    "stt_segments_total",
                    {
                        "camera": str(self.camera_id),
                        "provider": provider.kind,
                        "status": "ok",
                    },
                )
                await self._write(self.camera_id, seg, result)
                return
            except Exception as exc:
                last_exc = exc
                metrics.incr(
                    "stt_segments_total",
                    {
                        "camera": str(self.camera_id),
                        "provider": provider.kind,
                        "status": f"retry_{attempt}",
                    },
                )
                await asyncio.sleep(min(0.5 * (2 ** (attempt - 1)), 5.0))
        # Retries exhausted. Cool down so we do not bombard a broken
        # backend.
        self._cooldown_until = time.monotonic() + AUDIO_STT_COOLDOWN_S
        logger.warning(
            "stt provider %s exhausted retries for %s. cooldown %ss. last=%s",
            self._provider_kind,
            self.camera_id,
            AUDIO_STT_COOLDOWN_S,
            last_exc,
        )
        metrics.incr(
            "stt_segments_total",
            {
                "camera": str(self.camera_id),
                "provider": self._provider_kind,
                "status": "failed",
            },
        )


class AudioPipelineManager:
    """Reconciles per-camera routers against the DB toggle state.

    The camera manager calls :meth:`sync` on every poll. New cameras
    that have ``audio_capture_enabled=true`` get a router. Cameras that
    flipped off get their router stopped. Stream URL or provider
    changes restart the router.
    """

    def __init__(self, write_callback) -> None:
        self._write = write_callback
        self._routers: dict[uuid.UUID, CameraAudioRouter] = {}
        self._configs: dict[uuid.UUID, tuple] = {}

    def is_active(self, camera_id: uuid.UUID) -> bool:
        return camera_id in self._routers

    async def sync(self, cameras: Iterable, stream_url_resolver) -> None:
        """``stream_url_resolver(camera) -> str`` returns the mux URL.
        Pass through from manager to keep this module decoupled from
        MediaMTX wiring.
        """
        from shared.config import settings

        if not settings.audio_enabled:
            await self.stop_all()
            return

        seen: set[uuid.UUID] = set()
        for cam in cameras:
            if not getattr(cam, "audio_capture_enabled", False):
                continue
            if not getattr(cam, "audio_transcribe_enabled", False):
                continue
            url = stream_url_resolver(cam)
            if not url:
                continue
            seen.add(cam.id)
            cfg = (url, _resolve_provider_kind(cam), _resolve_provider_kwargs(cam))
            existing = self._configs.get(cam.id)
            if existing == cfg:
                continue
            # Config changed or new. Restart cleanly.
            if cam.id in self._routers:
                await self._routers[cam.id].stop()
            kind = cfg[1]
            kwargs = cfg[2]
            router = CameraAudioRouter(
                camera_id=cam.id,
                stream_url=url,
                provider_kind=kind,
                provider_kwargs=kwargs,
                write_callback=self._write,
            )
            try:
                await router.start()
            except Exception:
                logger.exception("failed to start audio router for %s", cam.id)
                continue
            self._routers[cam.id] = router
            self._configs[cam.id] = cfg

        # Stop routers for cameras no longer in the desired set.
        for cam_id in list(self._routers.keys()):
            if cam_id not in seen:
                await self._routers[cam_id].stop()
                self._routers.pop(cam_id, None)
                self._configs.pop(cam_id, None)

    async def stop_all(self) -> None:
        for router in list(self._routers.values()):
            await router.stop()
        self._routers.clear()
        self._configs.clear()


def _resolve_provider_kind(camera) -> str:
    # Phase 1. always faster_whisper unless an explicit STT provider row
    # is bound. Mock provider only via env override for tests.
    from shared.config import settings

    if getattr(settings, "audio_stt_provider", None):
        return settings.audio_stt_provider  # type: ignore[attr-defined]
    return "faster_whisper"


def _resolve_provider_kwargs(camera) -> dict:
    from shared.config import settings

    model = getattr(settings, "audio_stt_model", None) or "small.en"
    return {"model": model, "device": "cpu"}
