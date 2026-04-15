"""
Perception pipeline. Reads motion keyframes from Redis stream,
runs object detection, calls VLM for descriptions, stores observations.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np
import redis.asyncio as aioredis

from shared.config import settings
from shared.database import async_session
from shared.models import Observation, Provider
from services.perception.detector import ObjectDetector
from services.perception.vlm import VLMClient, get_active_provider
from sqlalchemy import select

logger = logging.getLogger("nurby.perception.pipeline")

REDIS_STREAM_KEY = "nurby:motion"
CONSUMER_GROUP = "perception"
CONSUMER_NAME = f"worker-{os.getpid()}"
BLOCK_MS = 5000  # Block for 5 seconds waiting for new messages
THUMBNAIL_DIR = os.path.join(settings.thumbnails_path, "observations")


class PerceptionPipeline:
    def __init__(self):
        self._redis = None
        self._detector = ObjectDetector()
        self._vlm = VLMClient()

    async def _get_redis(self):
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def run(self):
        """Main loop. Consume from Redis stream and process each keyframe."""
        r = await self._get_redis()

        # Create consumer group if it doesn't exist
        try:
            await r.xgroup_create(REDIS_STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
            logger.info("Created consumer group '%s'", CONSUMER_GROUP)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.info("Consumer group '%s' already exists", CONSUMER_GROUP)

        logger.info("Listening for motion keyframes on '%s'", REDIS_STREAM_KEY)

        while True:
            try:
                messages = await r.xreadgroup(
                    CONSUMER_GROUP,
                    CONSUMER_NAME,
                    {REDIS_STREAM_KEY: ">"},
                    count=1,
                    block=BLOCK_MS,
                )

                if not messages:
                    continue

                for stream_name, entries in messages:
                    for msg_id, data in entries:
                        try:
                            await self._process_keyframe(data)
                        except Exception:
                            logger.exception("Error processing keyframe %s", msg_id)
                        finally:
                            await r.xack(REDIS_STREAM_KEY, CONSUMER_GROUP, msg_id)

            except Exception:
                logger.exception("Error reading from Redis stream")
                await asyncio.sleep(2)

    async def _process_keyframe(self, data: dict):
        """Process a single motion keyframe through detection and VLM."""
        camera_id = data.get(b"camera_id", b"").decode()
        timestamp_str = data.get(b"timestamp", b"").decode()
        motion_score = float(data.get(b"motion_score", b"0").decode())
        frame_bytes = data.get(b"frame", b"")

        if not frame_bytes or not camera_id:
            return

        # Decode JPEG frame
        frame_arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(frame_arr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("Failed to decode keyframe for camera %s", camera_id)
            return

        timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now(timezone.utc)

        logger.info(
            "Processing keyframe from camera %s (motion=%.3f)",
            camera_id, motion_score,
        )

        # Step 1. Run object detection
        detections = await self._detector.detect(frame)
        detection_summary = self._detector.summarize(detections)
        logger.info("Detections for camera %s. %s", camera_id, detection_summary)

        # Step 2. Save thumbnail
        thumbnail_path = await self._save_thumbnail(camera_id, timestamp, frame, detections)

        # Step 3. Call VLM for scene description (if provider configured)
        vlm_description = None
        vlm_provider_name = None
        confidence = None

        provider = await get_active_provider()
        if provider:
            vlm_description = await self._vlm.describe(
                frame, detections, provider
            )
            vlm_provider_name = provider.name
            confidence = 0.8  # placeholder until VLM returns confidence
            logger.info("VLM description for camera %s. %s", camera_id, vlm_description)

        # Step 4. Store observation in database
        await self._store_observation(
            camera_id=uuid.UUID(camera_id),
            timestamp=timestamp,
            detections=detections,
            vlm_description=vlm_description,
            vlm_provider=vlm_provider_name,
            confidence=confidence,
            thumbnail_path=thumbnail_path,
        )

    async def _save_thumbnail(
        self,
        camera_id: str,
        timestamp: datetime,
        frame: np.ndarray,
        detections: list[dict],
    ) -> str | None:
        """Draw bounding boxes on frame and save as thumbnail."""
        try:
            annotated = frame.copy()
            for det in detections:
                x1, y1, x2, y2 = det["bbox"]
                label = f"{det['label']} {det['confidence']:.0%}"
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(
                    annotated, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                )

            os.makedirs(THUMBNAIL_DIR, exist_ok=True)
            filename = f"{camera_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
            path = os.path.join(THUMBNAIL_DIR, filename)
            cv2.imwrite(path, annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
            return path
        except Exception:
            logger.exception("Failed to save thumbnail")
            return None

    async def _store_observation(
        self,
        camera_id: uuid.UUID,
        timestamp: datetime,
        detections: list[dict],
        vlm_description: str | None,
        vlm_provider: str | None,
        confidence: float | None,
        thumbnail_path: str | None,
    ):
        """Store observation in Postgres."""
        try:
            # Build structured detection data
            object_detections = {
                "objects": [
                    {
                        "label": d["label"],
                        "confidence": d["confidence"],
                        "bbox": d["bbox"],
                    }
                    for d in detections
                ],
                "count": len(detections),
            }

            async with async_session() as db:
                obs = Observation(
                    camera_id=camera_id,
                    started_at=timestamp,
                    object_detections=object_detections,
                    vlm_description=vlm_description,
                    vlm_provider=vlm_provider,
                    confidence=confidence,
                    thumbnail_path=thumbnail_path,
                )
                db.add(obs)
                await db.commit()
                logger.info("Stored observation for camera %s with %d detections", camera_id, len(detections))
        except Exception:
            logger.exception("Failed to store observation")
