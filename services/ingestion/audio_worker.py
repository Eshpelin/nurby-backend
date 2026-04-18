"""Per-camera audio capture + event detection.

Uses PyAV to demux the audio stream from an RTSP/HLS URL, accumulates
1-second mono windows at 32kHz, hands them to the PANNs classifier,
and stores detections + notifies the rule engine via a Redis stream.

Runs in its own asyncio task per camera so OpenCV (video) and PyAV
(audio) never contend. Gracefully no-ops if PyAV cannot find an audio
stream (many cheap cameras publish video only).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from shared.app_settings import get_setting
from shared.config import settings
from shared.database import async_session
from shared.models import AudioDetection
from services.events.engine import RuleEngine

# Shared engine. caches rules, avoids per-event DB reload.
_rule_engine: Optional["RuleEngine"] = None


def _get_rule_engine() -> "RuleEngine":
    global _rule_engine
    if _rule_engine is None:
        _rule_engine = RuleEngine()
    return _rule_engine

logger = logging.getLogger("nurby.ingestion.audio")

AUDIO_COOLDOWN = 8.0  # seconds between emissions of the same label
RECONNECT_DELAY = 5
REDIS_STREAM_KEY = "nurby:audio"
REDIS_STREAM_MAXLEN = 500


class AudioWorker:
    def __init__(self, camera_id: uuid.UUID, stream_url: str):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self._running = True
        self._last_emit: dict[str, float] = {}
        self._redis = None

    def stop(self):
        self._running = False

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def run(self):
        # Respect runtime toggle. Re-checked on each reconnect attempt.
        while self._running:
            try:
                enabled = bool(await get_setting("audio_events", True))
                if not enabled:
                    await asyncio.sleep(30)
                    continue
                await self._process_audio()
            except Exception:
                logger.exception("Audio worker error for camera %s", self.camera_id)
            if self._running:
                await asyncio.sleep(RECONNECT_DELAY)

    async def _process_audio(self):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._pyav_loop)

    def _pyav_loop(self):
        """Sync PyAV pump. Runs in a worker thread."""
        try:
            import av  # type: ignore
        except ImportError:
            logger.error("PyAV not installed. audio events disabled")
            self._running = False
            return

        from services.perception import audio as audio_cls

        try:
            container = av.open(
                self.stream_url,
                options={"rtsp_transport": "tcp", "stimeout": "5000000"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("No audio stream for camera %s. %s", self.camera_id, exc)
            return

        astreams = [s for s in container.streams if s.type == "audio"]
        if not astreams:
            logger.info("Camera %s has no audio track", self.camera_id)
            try: container.close()
            except Exception: pass
            return

        astream = astreams[0]
        target_sr = audio_cls.SAMPLE_RATE
        window_samples = audio_cls.WINDOW_SAMPLES

        try:
            resampler = av.audio.resampler.AudioResampler(
                format="flt", layout="mono", rate=target_sr,
            )
        except Exception:
            logger.exception("Failed to init audio resampler")
            container.close()
            return

        buf = np.zeros(0, dtype=np.float32)
        logger.info("Audio stream open for camera %s at %sHz", self.camera_id, target_sr)

        try:
            for packet in container.demux(astream):
                if not self._running:
                    break
                for frame in packet.decode():
                    resampled_frames = resampler.resample(frame)
                    if not isinstance(resampled_frames, list):
                        resampled_frames = [resampled_frames]
                    for rf in resampled_frames:
                        if rf is None:
                            continue
                        arr = rf.to_ndarray().reshape(-1).astype(np.float32)
                        buf = np.concatenate([buf, arr]) if buf.size else arr
                        while buf.size >= window_samples:
                            window = buf[:window_samples]
                            buf = buf[window_samples:]
                            self._handle_window(window)
        except Exception:
            logger.exception("PyAV demux error for camera %s", self.camera_id)
        finally:
            try: container.close()
            except Exception: pass

    def _handle_window(self, window: np.ndarray):
        """Classify a 1s window and emit events that pass cooldown."""
        from services.perception import audio as audio_cls

        # Quick loudness gate. skip silence to save inference cost.
        rms = float(np.sqrt(np.mean(window * window) + 1e-12))
        if rms < 0.005:
            return

        try:
            # get_setting is async but we are in a thread. call via sync fallback.
            # The default 0.35 is fine for a first pass.
            min_score = 0.3
            events = audio_cls.classify(window, min_score=min_score)
        except Exception:
            logger.exception("Audio classify failed")
            return

        if not events:
            return

        now = time.monotonic()
        to_emit = []
        for ev in events:
            last = self._last_emit.get(ev["label"], 0.0)
            if now - last < AUDIO_COOLDOWN:
                continue
            self._last_emit[ev["label"]] = now
            to_emit.append(ev)

        if not to_emit:
            return

        # Dispatch to async tasks without blocking the decode loop.
        loop = asyncio.new_event_loop() if False else None  # placeholder
        for ev in to_emit:
            asyncio.run_coroutine_threadsafe(
                self._emit(ev),
                _get_main_loop(),
            )

    async def _emit(self, event: dict):
        """Persist detection and publish to Redis for rule dispatch."""
        label = event["label"]
        score = event["score"]
        raw = event.get("raw_class")
        logger.info("Audio event camera=%s label=%s score=%.3f", self.camera_id, label, score)

        try:
            async with async_session() as db:
                db.add(AudioDetection(
                    camera_id=self.camera_id,
                    label=label,
                    score=score,
                    raw_class=raw,
                ))
                await db.commit()
        except Exception:
            logger.exception("Failed to persist audio detection")

        try:
            r = await self._get_redis()
            await r.xadd(
                REDIS_STREAM_KEY,
                {
                    "camera_id": str(self.camera_id),
                    "label": label,
                    "score": f"{score:.4f}",
                    "raw_class": raw or "",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                },
                maxlen=REDIS_STREAM_MAXLEN,
                approximate=True,
            )
        except Exception:
            logger.exception("Failed to publish audio event to redis")

        # Feed the rule engine directly so audio_event triggers fire.
        rule_data = {
            "observation_id": None,
            "camera_id": str(self.camera_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "audio_event": {"label": label, "score": score, "raw_class": raw},
            "confidence": score,
        }
        try:
            await _get_rule_engine().evaluate(rule_data)
        except Exception:
            logger.exception("Rule engine failed for audio event")


# Module-level main-loop reference so threaded PyAV pump can schedule coroutines.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop):
    global _main_loop
    _main_loop = loop


def _get_main_loop() -> asyncio.AbstractEventLoop:
    if _main_loop is None:
        raise RuntimeError("Audio worker main loop not registered")
    return _main_loop
