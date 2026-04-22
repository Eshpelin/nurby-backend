"""
Fast-lane live detection worker.

Reads every incoming webcam frame off a high-rate Redis stream,
runs YOLO inline (no VLM, no DB writes), and caches the resulting
detections per camera so the dashboard overlay can track fast
movement. Complements the slow perception pipeline which is gated
by per-camera cadence and does the expensive VLM + storage work.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import cv2
import numpy as np
import redis.asyncio as aioredis

from services.perception.detector import ObjectDetector
from shared.config import settings

logger = logging.getLogger("nurby.perception.live_detector")

LIVE_STREAM_KEY = "nurby:live_motion"
LIVE_GROUP = "live_det"
LIVE_CONSUMER = f"worker-{os.getpid()}"
LIVE_CACHE_PREFIX = "nurby:live_det:"
LIVE_CACHE_TTL = 4  # seconds. overlay fades if stale
BLOCK_MS = 2000


class LiveDetector:
    def __init__(self) -> None:
        self._detector = ObjectDetector()
        self._redis = None
        # Track per-camera busy state so slow inference doesn't queue up.
        # If a camera's worker is still running, newer frames for it are
        # dropped from the fast lane.
        self._inflight: set[str] = set()

    async def _r(self):
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def run(self):
        r = await self._r()
        try:
            await r.xgroup_create(LIVE_STREAM_KEY, LIVE_GROUP, id="$", mkstream=True)
            logger.info("Created consumer group '%s'", LIVE_GROUP)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info("Live detector listening on '%s'", LIVE_STREAM_KEY)

        while True:
            try:
                messages = await r.xreadgroup(
                    LIVE_GROUP,
                    LIVE_CONSUMER,
                    {LIVE_STREAM_KEY: ">"},
                    count=8,
                    block=BLOCK_MS,
                )
                if not messages:
                    continue
                for _stream, entries in messages:
                    for msg_id, data in entries:
                        asyncio.create_task(self._handle(data))
                        await r.xack(LIVE_STREAM_KEY, LIVE_GROUP, msg_id)
            except Exception:
                logger.exception("Live detector read error")
                await asyncio.sleep(1)

    async def _handle(self, data: dict):
        camera_id = data.get(b"camera_id", b"").decode()
        if not camera_id:
            return
        # Drop if a previous frame for this camera is still being processed.
        if camera_id in self._inflight:
            return
        self._inflight.add(camera_id)
        try:
            frame_bytes = data.get(b"frame", b"")
            if not frame_bytes:
                return
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return
            h, w = frame.shape[:2]

            try:
                detections = await self._detector.detect(frame, confidence=0.3)
            except Exception:
                logger.exception("live YOLO failed for %s", camera_id)
                return

            payload = {
                "camera_id": camera_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "width": w,
                "height": h,
                "detections": [
                    {
                        "label": d["label"],
                        "confidence": d["confidence"],
                        "bbox": d["bbox"],
                    }
                    for d in detections
                ],
            }

            r = await self._r()
            await r.set(
                f"{LIVE_CACHE_PREFIX}{camera_id}",
                json.dumps(payload),
                ex=LIVE_CACHE_TTL,
            )
        finally:
            self._inflight.discard(camera_id)
