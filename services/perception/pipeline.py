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
from shared.models import Camera, Observation, Provider
from services.perception.detector import ObjectDetector
from services.perception.faces import FaceRecognizer
from services.perception.vlm import VLMClient, get_active_provider
from services.events.engine import RuleEngine
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
        self._face = FaceRecognizer()
        self._vlm = VLMClient()
        self._rule_engine = RuleEngine()
        self._camera_cache: dict[str, Camera] = {}
        self._camera_cache_time: float = 0
        self._vlm_last_call: dict[str, float] = {}  # camera_id -> last VLM call timestamp

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

    async def _get_camera_config(self, camera_id: str) -> Camera | None:
        """Fetch camera config with caching (30s TTL)."""
        import time as _time
        now = _time.monotonic()
        if now - self._camera_cache_time > 30:
            try:
                async with async_session() as db:
                    result = await db.execute(select(Camera))
                    cameras = result.scalars().all()
                    self._camera_cache = {str(c.id): c for c in cameras}
                    for c in cameras:
                        db.expunge(c)
                self._camera_cache_time = now
            except Exception:
                logger.exception("Failed to load camera configs")
        return self._camera_cache.get(camera_id)

    async def _get_provider_for_camera(self, cam: Camera | None) -> Provider | None:
        """Get VLM provider. Per-camera override if set, else system default."""
        if cam and cam.vlm_provider_id:
            try:
                async with async_session() as db:
                    provider = await db.get(Provider, cam.vlm_provider_id)
                    if provider:
                        db.expunge(provider)
                        return provider
            except Exception:
                logger.exception("Failed to fetch camera-specific provider")
        return await get_active_provider()

    def _should_call_vlm(self, camera_id: str, cam: Camera | None) -> bool:
        """Check if enough time passed since last VLM call for this camera."""
        import time as _time
        interval = cam.vlm_interval if cam else 0
        if interval <= 0:
            return True
        last = self._vlm_last_call.get(camera_id, 0)
        return (_time.monotonic() - last) >= interval

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

        # Load per-camera config
        cam = await self._get_camera_config(camera_id)

        logger.info(
            "Processing keyframe from camera %s (motion=%.3f)",
            camera_id, motion_score,
        )

        # Step 1. Run object detection (if enabled for this camera)
        detections = []
        if cam is None or cam.detect_objects:
            model_configs = cam.detection_models if cam and cam.detection_models else None

            if model_configs and len(model_configs) > 0:
                # Multi-model path
                merge_strategy = cam.detection_merge if cam else "any"
                consensus_min = cam.detection_consensus_min if cam else 2
                detections = await self._detector.detect_multi(
                    frame, model_configs, merge=merge_strategy, consensus_min=consensus_min
                )
            else:
                # Single model fallback
                confidence_threshold = cam.object_confidence if cam else 0.35
                detections = await self._detector.detect(frame, confidence=confidence_threshold)

            detection_summary = self._detector.summarize(detections)
            logger.info("Detections for camera %s. %s", camera_id, detection_summary)

        # Step 2. Run face detection and matching (if enabled for this camera)
        faces = []
        if cam is None or cam.detect_faces:
            faces = await self._face.detect_and_embed(frame)
            if faces:
                faces = await self._face.match_faces(faces)
                matched = [f for f in faces if f.get("person_id")]
                logger.info(
                    "Faces for camera %s. %d detected, %d matched",
                    camera_id, len(faces), len(matched),
                )

        # Step 3. Save thumbnail
        thumbnail_path = await self._save_thumbnail(camera_id, timestamp, frame, detections)

        # Step 4. Call VLM for scene description (respecting trigger, interval, and provider)
        vlm_description = None
        vlm_provider_name = None
        confidence = None

        # Check VLM trigger condition
        vlm_triggered = True
        if cam and cam.vlm_trigger == "on_object":
            trigger_labels = cam.vlm_trigger_objects or []
            if trigger_labels:
                detected_labels = {d["label"] for d in detections}
                vlm_triggered = bool(detected_labels & set(trigger_labels))
            else:
                # on_object mode with empty list means "any detection"
                vlm_triggered = len(detections) > 0

            if not vlm_triggered:
                logger.debug(
                    "VLM skipped for camera %s. No matching trigger objects in detections",
                    camera_id,
                )

        if vlm_triggered and self._should_call_vlm(camera_id, cam):
            import time as _time
            provider = await self._get_provider_for_camera(cam)
            if provider:
                custom_prompt = cam.vlm_prompt if cam else None
                max_tokens = cam.vlm_max_tokens if cam else 200
                vlm_description = await self._vlm.describe(
                    frame, detections, provider,
                    system_prompt=custom_prompt,
                    max_tokens=max_tokens,
                )
                vlm_provider_name = provider.name
                confidence = 0.8  # placeholder until VLM returns confidence
                self._vlm_last_call[camera_id] = _time.monotonic()
                logger.info("VLM description for camera %s. %s", camera_id, vlm_description)

        # Step 5. Store observation in database
        person_detections = None
        if faces:
            person_detections = {
                "faces": [
                    {
                        "bbox": f["bbox"],
                        "person_id": f.get("person_id"),
                        "person_name": f.get("person_name"),
                        "match_distance": f.get("match_distance"),
                    }
                    for f in faces
                ],
                "count": len(faces),
            }

        observation_id = await self._store_observation(
            camera_id=uuid.UUID(camera_id),
            timestamp=timestamp,
            detections=detections,
            person_detections=person_detections,
            vlm_description=vlm_description,
            vlm_provider=vlm_provider_name,
            confidence=confidence,
            thumbnail_path=thumbnail_path,
        )

        # Step 6. Evaluate rules against this observation
        rule_data = {
            "observation_id": str(observation_id) if observation_id else None,
            "camera_id": camera_id,
            "timestamp": timestamp.isoformat(),
            "motion_score": motion_score,
            "object_detections": {
                "objects": [
                    {"label": d["label"], "confidence": d["confidence"], "bbox": d["bbox"]}
                    for d in detections
                ],
                "count": len(detections),
            },
            "person_detections": person_detections,
            "vlm_description": vlm_description,
            "confidence": confidence,
        }
        try:
            await self._rule_engine.evaluate(rule_data)
        except Exception:
            logger.exception("Rule evaluation failed for camera %s", camera_id)

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
        person_detections: dict | None = None,
        vlm_description: str | None = None,
        vlm_provider: str | None = None,
        confidence: float | None = None,
        thumbnail_path: str | None = None,
    ) -> uuid.UUID | None:
        """Store observation in Postgres. Returns observation ID."""
        try:
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
                    person_detections=person_detections,
                    vlm_description=vlm_description,
                    vlm_provider=vlm_provider,
                    confidence=confidence,
                    thumbnail_path=thumbnail_path,
                )
                db.add(obs)
                await db.commit()
                await db.refresh(obs)
                logger.info("Stored observation %s for camera %s with %d detections", obs.id, camera_id, len(detections))
                return obs.id
        except Exception:
            logger.exception("Failed to store observation")
            return None
