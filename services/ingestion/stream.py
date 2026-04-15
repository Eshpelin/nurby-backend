"""
Stream worker. Connects to a single RTSP stream, decodes frames,
runs motion detection, writes recording segments to disk,
and publishes motion keyframes to Redis for the perception pipeline.
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np

from shared.config import settings
from shared.database import async_session
from shared.models import Camera, Recording

logger = logging.getLogger("nurby.ingestion.stream")

SEGMENT_DURATION = 300  # 5-minute recording segments
MOTION_FRAME_INTERVAL = 5  # Check motion every N frames
RECONNECT_DELAY = 5  # Seconds between reconnection attempts
MOTION_THRESHOLD = 0.01  # Minimum motion score to trigger event
MOTION_COOLDOWN = 3.0  # Seconds between motion keyframe publishes
REDIS_STREAM_KEY = "nurby:motion"  # Redis stream for motion keyframes
REDIS_STREAM_MAXLEN = 1000  # Max entries in stream


class StreamWorker:
    def __init__(self, camera_id: uuid.UUID, stream_url: str, recording_enabled: bool):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.recording_enabled = recording_enabled
        self._running = True
        self._prev_gray = None
        self._last_motion_publish = 0.0
        self._redis = None

    def stop(self):
        self._running = False

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def run(self):
        """Main loop. Connects to stream, decodes frames, detects motion, records."""
        while self._running:
            try:
                await self._process_stream()
            except Exception:
                logger.exception("Stream error for camera %s", self.camera_id)
            if self._running:
                logger.info("Reconnecting to camera %s in %ds", self.camera_id, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _process_stream(self):
        loop = asyncio.get_event_loop()

        cap = await loop.run_in_executor(None, self._open_capture)
        if cap is None:
            return

        status = "recording" if self.recording_enabled else "live"
        await self._update_camera_status(status)

        # Read stream properties
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        await self._update_camera_properties(width, height, fps)

        writer = None
        segment_start = None
        segment_path = None
        frame_count = 0

        try:
            while self._running:
                ret, frame = await loop.run_in_executor(None, cap.read)
                if not ret:
                    logger.warning("Lost connection to camera %s", self.camera_id)
                    break

                frame_count += 1

                # Motion detection on interval
                if frame_count % MOTION_FRAME_INTERVAL == 0:
                    motion_score = self._detect_motion(frame)
                    if motion_score > MOTION_THRESHOLD:
                        logger.debug(
                            "Motion detected on camera %s (score=%.4f)",
                            self.camera_id,
                            motion_score,
                        )
                        # Publish keyframe to Redis with cooldown
                        now = time.monotonic()
                        if now - self._last_motion_publish >= MOTION_COOLDOWN:
                            self._last_motion_publish = now
                            await self._publish_keyframe(frame, motion_score)

                # Recording
                if self.recording_enabled:
                    now = datetime.now(timezone.utc)

                    # Start new segment if needed
                    if writer is None or (
                        segment_start
                        and (now - segment_start).total_seconds() >= SEGMENT_DURATION
                    ):
                        # Finalize previous segment
                        if writer is not None:
                            await loop.run_in_executor(None, writer.release)
                            await self._save_recording(segment_path, segment_start, now)

                        segment_start = now
                        segment_path = self._segment_path(now)
                        os.makedirs(os.path.dirname(segment_path), exist_ok=True)

                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        writer = cv2.VideoWriter(
                            segment_path, fourcc, fps, (width, height)
                        )

                    writer.write(frame)

                # Yield control to event loop
                await asyncio.sleep(0)

        finally:
            if writer is not None:
                writer.release()
                if segment_path and segment_start:
                    await self._save_recording(
                        segment_path, segment_start, datetime.now(timezone.utc)
                    )
            cap.release()
            if self._redis:
                await self._redis.aclose()
                self._redis = None
            await self._update_camera_status("offline")

    def _open_capture(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self.stream_url)
        if not cap.isOpened():
            logger.error("Failed to open stream for camera %s at %s", self.camera_id, self.stream_url)
            return None
        logger.info("Connected to camera %s", self.camera_id)
        return cap

    def _detect_motion(self, frame: np.ndarray) -> float:
        """Simple frame-differencing motion detector. Returns a score 0..1."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return 0.0

        delta = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray

        thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
        score = float(np.sum(thresh) / (thresh.size * 255))
        return score

    async def _publish_keyframe(self, frame: np.ndarray, motion_score: float):
        """Encode frame as JPEG and publish to Redis stream for perception."""
        try:
            # Encode frame as JPEG (quality 85 for good balance of size and quality)
            _, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            jpeg_bytes = jpeg_buf.tobytes()

            r = await self._get_redis()
            await r.xadd(
                REDIS_STREAM_KEY,
                {
                    "camera_id": str(self.camera_id),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "motion_score": str(round(motion_score, 4)),
                    "frame": jpeg_bytes,
                },
                maxlen=REDIS_STREAM_MAXLEN,
                approximate=True,
            )
            logger.debug("Published motion keyframe for camera %s", self.camera_id)
        except Exception:
            logger.exception("Failed to publish keyframe to Redis")

    def _segment_path(self, start: datetime) -> str:
        date_dir = start.strftime("%Y-%m-%d")
        filename = f"{self.camera_id}_{start.strftime('%H%M%S')}.mp4"
        return os.path.join(settings.recordings_path, str(self.camera_id), date_dir, filename)

    async def _update_camera_status(self, status: str):
        try:
            async with async_session() as db:
                camera = await db.get(Camera, self.camera_id)
                if camera:
                    camera.status = status
                    await db.commit()
        except Exception:
            logger.exception("Failed to update camera status")

    async def _update_camera_properties(self, width: int, height: int, fps: float):
        try:
            async with async_session() as db:
                camera = await db.get(Camera, self.camera_id)
                if camera:
                    camera.width = width
                    camera.height = height
                    camera.fps = fps
                    await db.commit()
        except Exception:
            logger.exception("Failed to update camera properties")

    async def _save_recording(self, file_path: str, started_at: datetime, ended_at: datetime):
        try:
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None
            duration = (ended_at - started_at).total_seconds()

            async with async_session() as db:
                recording = Recording(
                    camera_id=self.camera_id,
                    file_path=file_path,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_seconds=duration,
                    file_size_bytes=file_size,
                )
                db.add(recording)
                await db.commit()
                logger.info("Saved recording segment %s (%.1fs)", file_path, duration)
        except Exception:
            logger.exception("Failed to save recording metadata")
