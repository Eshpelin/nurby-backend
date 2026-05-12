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
from shared.models import Camera, Observation, Provider, Transcript
from services.perception.detector import ObjectDetector
from services.perception.faces import FaceRecognizer
from services.perception.plates import detect_plates
from services.perception.vlm import VLMClient, get_active_provider
from services.perception.vlm_queue import VLMQueue, VLMJob
from services.search.embeddings import generate_embedding, get_embedding_provider
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
        self._vlm_queue = VLMQueue(self._vlm)
        self._rule_engine = RuleEngine()
        self._camera_cache: dict[str, Camera] = {}
        self._camera_cache_time: float = 0
        self._camera_cache_lock = asyncio.Lock()
        self._vlm_last_call: dict[str, float] = {}  # camera_id -> last VLM call timestamp
        # Per-camera object trackers for loitering / line-cross.
        from services.perception.tracker import ObjectTracker
        self._trackers: dict[str, ObjectTracker] = {}
        self._ObjectTracker = ObjectTracker

    async def _get_redis(self):
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def run(self):
        """Main loop. Consume from Redis stream and process each keyframe."""
        # Wire WebSocket broadcast for VLM status updates
        try:
            from services.api.ws import broadcast
            self._vlm_queue.set_broadcast(broadcast)
        except ImportError:
            logger.warning("WebSocket broadcast not available for VLM status")

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
            async with self._camera_cache_lock:
                # Double-check after acquiring lock
                if now - self._camera_cache_time > 30:
                    try:
                        async with async_session() as db:
                            result = await db.execute(select(Camera))
                            cameras = result.scalars().all()
                            self._camera_cache = {str(c.id): c for c in cameras}
                            for c in cameras:
                                db.expunge(c)
                        self._camera_cache_time = _time.monotonic()
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

    async def _get_refiner_provider(
        self, cam: Camera | None, primary: Provider
    ) -> Provider | None:
        """Resolve the refiner provider for this camera. Returns None
        when not configured, or when the user accidentally set the
        refiner to the same row as the primary (which would just
        re-run the same model). The UI prevents this but we double-
        check on the worker side too."""
        if cam is None or not getattr(cam, "vlm_refiner_provider_id", None):
            return None
        if cam.vlm_refiner_provider_id == primary.id:
            return None
        try:
            async with async_session() as db:
                p = await db.get(Provider, cam.vlm_refiner_provider_id)
                if p:
                    db.expunge(p)
                    return p
        except Exception:
            logger.exception("refiner provider lookup failed")
        return None

    def _should_call_vlm(self, camera_id: str, cam: Camera | None) -> bool:
        """Check if enough time passed since last VLM call for this camera."""
        import time as _time
        interval = cam.vlm_interval if cam else 0
        if interval <= 0:
            return True
        last = self._vlm_last_call.get(camera_id, 0)
        return (_time.monotonic() - last) >= interval

    def _build_vlm_context(
        self,
        cam: Camera | None,
        timestamp: datetime,
        faces: list[dict],
        detections: list[dict],
    ) -> str | None:
        """Assemble a multimodal context block from specialist models.

        VLMs are bad at OCR and unreliable at face ID on small crops. We
        already nailed those signals upstream (InsightFace, plate OCR,
        face clustering). Pass the answers in plain English so the VLM
        treats them as ground truth instead of re-guessing from pixels.

        Returns None when there is nothing worth saying so we don't
        bloat the prompt for empty scenes.
        """
        parts: list[str] = []

        # Camera + location. Helps the VLM reason about indoor vs
        # outdoor and use the room name in the description.
        cam_bits: list[str] = []
        if cam and cam.name:
            cam_bits.append(cam.name)
        if cam and cam.location_label:
            cam_bits.append(cam.location_label)
        if cam_bits:
            parts.append(f"Camera: {' / '.join(cam_bits)}.")

        # Time of day. Local time string is enough. Helps disambiguate
        # delivery vs intruder, sunrise vs dusk, etc.
        try:
            local_ts = timestamp.astimezone()
            parts.append(f"Local time: {local_ts.strftime('%H:%M %a %b %d')}.")
        except Exception:
            pass

        # Face recognition results. Named matches first, then unknown
        # count + cluster ids when present.
        if faces:
            named = [f for f in faces if f.get("person_name")]
            unknown = [f for f in faces if not f.get("person_name")]
            if named:
                names = ", ".join(sorted({f["person_name"] for f in named}))
                parts.append(f"Identified people: {names}.")
            if unknown:
                clusters = sorted(
                    {f.get("cluster_id") for f in unknown if f.get("cluster_id")}
                )
                if clusters:
                    parts.append(
                        f"Unknown faces: {len(unknown)}"
                        f" (recurring cluster ids: {', '.join(str(c)[:8] for c in clusters)})."
                    )
                else:
                    parts.append(f"Unknown faces: {len(unknown)}.")

        # License plate OCR. Plates ride inside detections with label
        # license_plate. Pull the OCR text out so the VLM sees the
        # actual string, not just the label.
        plate_texts = [
            d.get("plate_text")
            for d in detections
            if d.get("label") == "license_plate" and d.get("plate_text")
        ]
        if plate_texts:
            parts.append(f"License plates read: {', '.join(plate_texts)}.")

        return " ".join(parts) if parts else None

    async def _get_ptz_pose(self, camera_id: str) -> dict | None:
        """Read the most recent known PTZ pose for the camera from
        Redis. Returns None when nothing is cached. The PTZ control
        endpoints write this key whenever the user moves the camera
        to a preset; a future ONVIF GetStatus poller can refresh it
        between presets.
        """
        try:
            r = await self._get_redis()
            raw = await r.get(f"nurby:ptz_pose:{camera_id}")
            if not raw:
                return None
            import json as _json

            data = _json.loads(raw)
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    async def _recent_heard_text(
        self, camera_id: str, ts: datetime, lookback_seconds: int = 8
    ) -> str | None:
        """Pull any transcripts that finalized in the last few seconds for
        this camera so the VLM can fuse audio context into its first-pass
        description. Avoids the post-hoc re-enrichment round-trip when
        speech was already heard before the keyframe arrived.

        Post-hoc enrichment still runs for transcripts that finalize
        AFTER the VLM call, so we never lose late speech.
        """
        try:
            from datetime import timedelta as _td

            cutoff = ts - _td(seconds=lookback_seconds)
            async with async_session() as db:
                rows = (
                    await db.execute(
                        select(Transcript.text)
                        .where(Transcript.camera_id == uuid.UUID(camera_id))
                        .where(Transcript.filtered.is_(False))
                        .where(Transcript.started_at >= cutoff)
                        .where(Transcript.started_at <= ts)
                        .order_by(Transcript.started_at.asc())
                        .limit(5)
                    )
                ).scalars().all()
            if not rows:
                return None
            joined = " ".join(t.strip() for t in rows if t and t.strip())
            return joined or None
        except Exception:
            logger.debug("recent_heard_text lookup failed", exc_info=True)
            return None

    async def _process_keyframe(self, data: dict):
        """Process a single motion keyframe through detection and VLM."""
        camera_id = data.get(b"camera_id", b"").decode()
        timestamp_str = data.get(b"timestamp", b"").decode()
        motion_score = float(data.get(b"motion_score", b"0").decode())
        frame_bytes = data.get(b"frame", b"")

        if not frame_bytes or not camera_id:
            return

        # Skip audio-only cameras. Their ingestion path doesn't publish
        # video keyframes today, but defensive in case a stray frame
        # arrives during a mode flip.
        try:
            cam_quick = await self._get_camera_config(camera_id)
            if cam_quick is not None and getattr(cam_quick, "audio_only", False):
                return
        except Exception:
            pass

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

        # Step 0. Apply motion zone masking before detection
        frame = self._apply_motion_zones(frame, cam)

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

        # Step 1b. Run license plate detection on vehicle crops
        if detections:
            has_vehicle = any(d["label"] in ("car", "truck", "bus", "motorcycle", "van") for d in detections)
            if has_vehicle:
                try:
                    detections = detect_plates(frame, detections)
                    plates = [d for d in detections if d["label"] == "license_plate"]
                    if plates:
                        plate_texts = [d.get("plate_text", "?") for d in plates]
                        logger.info("Plates for camera %s. %s", camera_id, ", ".join(plate_texts))
                except Exception:
                    logger.exception("Plate detection failed for camera %s", camera_id)

        # Signal recording trigger if camera uses on_object or clip mode
        if cam and cam.recording_mode in ("on_object", "clip") and detections:
            trigger_labels = cam.recording_trigger_objects or []
            if trigger_labels:
                detected_labels = {d["label"] for d in detections}
                if detected_labels & set(trigger_labels):
                    await self._set_record_trigger(camera_id)
            else:
                # No specific labels configured means any detection triggers recording
                await self._set_record_trigger(camera_id)

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

            # Cluster unknown faces for auto-discovery (skip for outdoor cameras
            # to avoid flooding suggestions with passersby)
            is_outdoor = cam and getattr(cam, "scene_mode", "indoor") == "outdoor"
            if faces and not is_outdoor:
                for face in faces:
                    if not face.get("person_id"):
                        cluster_id = await self._face.cluster_unknown_face(face, camera_id, frame)
                        if cluster_id:
                            face["cluster_id"] = str(cluster_id)

        # Smart privacy zones. Refresh auto-detected zones from the
        # current frame's detections and apply Gaussian blur BEFORE
        # the thumbnail + VLM encode paths see the frame. Anything
        # downstream (thumbnail, VLM call, embedding) only ever sees
        # the redacted version.
        try:
            from services.perception.privacy import (
                apply_privacy_blur,
                get_active_zones,
                refresh_privacy_zones,
            )

            targets = (
                list(cam.privacy_zone_targets or [])
                if cam and getattr(cam, "privacy_zone_targets", None)
                else []
            )
            # Optional. Read a cached PTZ pose from Redis. Set by
            # the PTZ control endpoints + (future) ONVIF GetStatus
            # poller. When present, auto zones tag themselves with
            # this pose so they only fire when the camera returns
            # to it. When absent, falls back to freshness alone.
            current_pose = await self._get_ptz_pose(camera_id)
            if targets:
                await refresh_privacy_zones(
                    camera_id=camera_id,
                    detections=detections,
                    frame_shape=frame.shape,
                    targets=targets,
                    current_pose=current_pose,
                )
            zones = await get_active_zones(camera_id, current_pose=current_pose)
            if zones:
                strength = (
                    int(cam.privacy_zone_blur_strength)
                    if cam and getattr(cam, "privacy_zone_blur_strength", None)
                    else 55
                )
                frame = apply_privacy_blur(frame, zones, strength=strength)
        except Exception:
            logger.exception("privacy blur failed camera=%s", camera_id)

        # Step 3. Save thumbnail
        thumbnail_path = await self._save_thumbnail(camera_id, timestamp, frame, detections)

        # Step 4. Store observation in database (immediately, without waiting for VLM)
        person_detections = None
        if faces:
            person_detections = {
                "faces": [
                    {
                        "bbox": f["bbox"],
                        "person_id": f.get("person_id"),
                        "person_name": f.get("person_name"),
                        "match_distance": f.get("match_distance"),
                        "cluster_id": f.get("cluster_id"),
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
            vlm_description=None,  # VLM patches this async
            vlm_provider=None,
            confidence=None,
            thumbnail_path=thumbnail_path,
        )

        # Step 5. Queue VLM call async (non-blocking)
        vlm_triggered = True
        if cam and cam.vlm_trigger == "on_object":
            trigger_labels = cam.vlm_trigger_objects or []
            if trigger_labels:
                detected_labels = {d["label"] for d in detections}
                vlm_triggered = bool(detected_labels & set(trigger_labels))
            else:
                vlm_triggered = len(detections) > 0

        if vlm_triggered and self._should_call_vlm(camera_id, cam) and observation_id:
            import time as _time
            provider = await self._get_provider_for_camera(cam)
            if provider:
                self._vlm_last_call[camera_id] = _time.monotonic()
                heard_text = await self._recent_heard_text(camera_id, timestamp)
                extra_context = self._build_vlm_context(
                    cam=cam,
                    timestamp=timestamp,
                    faces=faces,
                    detections=detections,
                )
                refiner_provider = await self._get_refiner_provider(cam, provider)
                refiner_triggers = (
                    cam.vlm_refiner_trigger_objects
                    if cam and isinstance(cam.vlm_refiner_trigger_objects, list)
                    else None
                )
                refiner_keywords = (
                    cam.vlm_refiner_keywords
                    if cam and isinstance(cam.vlm_refiner_keywords, list)
                    else None
                )
                await self._vlm_queue.enqueue(VLMJob(
                    camera_id=camera_id,
                    observation_id=observation_id,
                    frame=frame.copy(),  # copy since frame may be reused
                    detections=detections,
                    provider=provider,
                    system_prompt=cam.vlm_prompt if cam else None,
                    max_tokens=cam.vlm_max_tokens if cam else 200,
                    max_input_tokens=getattr(cam, "vlm_max_input_tokens", None) if cam else None,
                    timestamp=timestamp,
                    heard_text=heard_text,
                    extra_context=extra_context,
                    refiner_provider=refiner_provider,
                    refiner_trigger_objects=refiner_triggers,
                    refiner_keywords=refiner_keywords,
                    refiner_max_tokens=getattr(cam, "vlm_refiner_max_tokens", None) if cam else None,
                    refiner_max_input_tokens=getattr(cam, "vlm_refiner_max_input_tokens", None) if cam else None,
                ))

        # Step 6. Generate description embedding for detections (VLM embedding added later by queue)
        if observation_id and detections:
            asyncio.ensure_future(
                self._generate_and_store_embedding(
                    observation_id=observation_id,
                    vlm_description="",
                    detections=detections,
                    person_detections=person_detections,
                )
            )

        # Step 6b. Update per-camera tracker and evaluate spatial events.
        tracker = self._trackers.get(camera_id)
        if tracker is None:
            tracker = self._ObjectTracker()
            self._trackers[camera_id] = tracker
        tracker.update(detections)
        from services.perception.spatial_events import evaluate as eval_spatial
        loitering_events, line_cross_events = eval_spatial(
            tracker, cam.motion_zones if cam else None
        )

        # Step 7. Evaluate rules against this observation
        rule_data = {
            "observation_id": str(observation_id) if observation_id else None,
            "camera_id": camera_id,
            "timestamp": timestamp.isoformat(),
            "motion_score": motion_score,
            "object_detections": {
                "objects": [
                    {
                        "label": d["label"],
                        "confidence": d["confidence"],
                        "bbox": d["bbox"],
                        "tracker_id": d.get("tracker_id"),
                    }
                    for d in detections
                ],
                "count": len(detections),
            },
            "person_detections": person_detections,
            "loitering_events": loitering_events,
            "line_cross_events": line_cross_events,
            "tracks": [
                {
                    "track_id": tr.track_id,
                    "label": tr.label,
                    "bbox": tr.bbox,
                    "prev_bbox": tr.prev_bbox,
                }
                for tr in tracker.tracks.values()
            ],
            "vlm_description": None,  # VLM runs async, not available at rule eval time
            "confidence": None,
        }
        try:
            await self._rule_engine.evaluate(rule_data)
        except Exception:
            logger.exception("Rule evaluation failed for camera %s", camera_id)

    @staticmethod
    def _apply_motion_zones(frame: np.ndarray, cam: Camera | None) -> np.ndarray:
        """Mask the frame according to configured motion zones.

        Include zones white-list specific regions. Everything outside is blacked out.
        Exclude zones black out specific regions. The rest remains visible.
        """
        if cam is None or not cam.motion_zones:
            return frame

        zones = cam.motion_zones
        if not isinstance(zones, list) or len(zones) == 0:
            return frame

        h, w = frame.shape[:2]
        include_zones = [z for z in zones if z.get("type") == "include" and z.get("points")]
        exclude_zones = [z for z in zones if z.get("type") == "exclude" and z.get("points")]

        if include_zones:
            # Build a mask that is black everywhere, white inside include polygons
            mask = np.zeros((h, w), dtype=np.uint8)
            for zone in include_zones:
                pts = np.array(zone["points"], dtype=np.int32)
                cv2.fillPoly(mask, [pts], 255)
            frame = cv2.bitwise_and(frame, frame, mask=mask)

        if exclude_zones:
            # Black out exclude polygon regions
            for zone in exclude_zones:
                pts = np.array(zone["points"], dtype=np.int32)
                cv2.fillPoly(frame, [pts], (0, 0, 0))

        return frame

    async def _set_record_trigger(self, camera_id: str):
        """Set Redis key to trigger recording in stream worker."""
        try:
            r = await self._get_redis()
            key = f"nurby:record_trigger:{camera_id}"
            await r.setex(key, 30, "1")  # 30 second TTL
            logger.debug("Set record trigger for camera %s", camera_id)
        except Exception:
            logger.exception("Failed to set record trigger")

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
                is_plate = det["label"] == "license_plate"
                color = (0, 200, 255) if is_plate else (0, 255, 0)
                label = det.get("plate_text", "") if is_plate else f"{det['label']} {det['confidence']:.0%}"
                if is_plate and det.get("plate_text"):
                    label = f"PLATE {det['plate_text']}"
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    annotated, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
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
                        **({"plate_text": d["plate_text"]} if d.get("plate_text") else {}),
                    }
                    for d in detections
                ],
                "count": len(detections),
            }

            async with async_session() as db:
                # Debounce. if the previous observation on this camera was
                # recent and carries the same label set + same named faces,
                # extend it (bump ended_at) instead of minting a new row.
                # Keeps the timeline readable when someone just moves around.
                from datetime import timedelta

                DEBOUNCE_WINDOW = timedelta(minutes=2)

                def _scene_signature(labels: list[str], face_names: list[str]) -> tuple:
                    """Collapse noisy label sets into a stable scene signature.

                    YOLO flickers between transient labels (chair, tie, cell
                    phone, etc.) even when the scene is the same person
                    walking around. Bucket labels into high-level categories
                    so minor flaps don't spawn a new observation row."""
                    lset = set(labels)
                    categories = set()
                    if "person" in lset:
                        categories.add("person")
                    if lset & {"car", "truck", "bus", "motorcycle", "bicycle"}:
                        categories.add("vehicle")
                    if lset & {"cat", "dog", "bird"}:
                        categories.add("animal")
                    if lset & {"backpack", "handbag", "suitcase", "umbrella"}:
                        categories.add("package")
                    if lset & {"knife", "gun"}:
                        categories.add("weapon")
                    # Everything else ignored for signature purposes.
                    return (
                        tuple(sorted(categories)),
                        tuple(sorted(face_names)),
                    )

                new_labels = sorted({d["label"] for d in detections})
                new_names = sorted({
                    f.get("person_name")
                    for f in ((person_detections or {}).get("faces") or [])
                    if f.get("person_name")
                })
                new_sig = _scene_signature(new_labels, new_names)

                recent_q = (
                    select(Observation)
                    .where(Observation.camera_id == camera_id)
                    .order_by(Observation.started_at.desc())
                    .limit(1)
                )
                recent = (await db.execute(recent_q)).scalar_one_or_none()
                if recent is not None:
                    recent_anchor = recent.ended_at or recent.started_at
                    if timestamp - recent_anchor <= DEBOUNCE_WINDOW:
                        recent_labels = sorted({
                            o.get("label")
                            for o in ((recent.object_detections or {}).get("objects") or [])
                        })
                        recent_names = sorted({
                            f.get("person_name")
                            for f in ((recent.person_detections or {}).get("faces") or [])
                            if f.get("person_name")
                        })
                        recent_sig = _scene_signature(recent_labels, recent_names)
                        same_scene = recent_sig == new_sig and len(new_sig[0]) > 0
                        if same_scene:
                            recent.ended_at = timestamp
                            # Keep the freshest VLM caption if we just got one.
                            if vlm_description:
                                recent.vlm_description = vlm_description
                                recent.vlm_provider = vlm_provider
                            # Do NOT overwrite thumbnail_path here. The VLM
                            # queue pairs the caption with the exact frame
                            # it analyzed and updates thumbnail_path when
                            # that caption lands. Overwriting with every
                            # incoming frame would show a newer image than
                            # the caption on display.
                            if confidence is not None:
                                recent.confidence = confidence
                            await db.commit()
                            logger.info(
                                "Extended observation %s on camera %s (same scene, %s)",
                                recent.id, camera_id, ",".join(new_labels) or "no-labels",
                            )
                            return recent.id

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
                await db.flush()
                # Link the observation to an open incident on this
                # camera matching its signature, or open a new one.
                # Done in the same session so observation.incident_id
                # and the incident row's occurrence_count + last_seen
                # advance atomically.
                try:
                    from services.perception.incident_tracker import assign_incident

                    cam_for_inc = await db.get(Camera, camera_id)
                    if cam_for_inc is not None:
                        inc_id = await assign_incident(db, cam_for_inc, obs)
                        if inc_id is not None:
                            obs.incident_id = inc_id
                except Exception:
                    logger.exception("incident assignment failed obs=%s", obs.id)
                await db.commit()
                await db.refresh(obs)
                logger.info("Stored observation %s for camera %s with %d detections", obs.id, camera_id, len(detections))

                # Invalidate starred recaps when any identified face appears.
                if person_detections and person_detections.get("faces"):
                    person_ids = [
                        str(f.get("person_id"))
                        for f in person_detections["faces"]
                        if f.get("person_id")
                    ]
                    if person_ids:
                        try:
                            from services.recap import invalidate_person_recaps
                            from services.api.ws import broadcast as _ws_broadcast

                            await invalidate_person_recaps(db, person_ids)
                            await _ws_broadcast({
                                "type": "person_seen",
                                "person_ids": person_ids,
                                "camera_id": str(camera_id),
                                "observation_id": str(obs.id),
                            })
                        except Exception:
                            logger.exception("Failed to invalidate starred recaps")

                return obs.id
        except Exception:
            logger.exception("Failed to store observation")
            return None

    async def _generate_and_store_embedding(
        self,
        observation_id: uuid.UUID,
        vlm_description: str,
        detections: list[dict],
        person_detections: dict | None = None,
    ) -> None:
        """Generate a description embedding and update the observation record.

        Combines VLM description, object detection summary, and person names
        into a single text for richer embeddings. Runs asynchronously so the
        main pipeline is not blocked. Failures are logged but never crash
        the pipeline.
        """
        try:
            # Build combined text for embedding
            parts = []
            if vlm_description:
                parts.append(vlm_description)

            # Add object detection summary
            if detections:
                labels = [d["label"] for d in detections]
                parts.append("Objects detected. " + ", ".join(labels))

            # Add person names from face detections
            if person_detections and person_detections.get("faces"):
                named = [
                    f["person_name"]
                    for f in person_detections["faces"]
                    if f.get("person_name")
                ]
                if named:
                    parts.append("People present. " + ", ".join(named))

            embed_text = ". ".join(parts)

            provider = await get_embedding_provider()
            embedding = await generate_embedding(embed_text, provider)

            async with async_session() as db:
                obs = await db.get(Observation, observation_id)
                if obs:
                    obs.description_embedding = embedding
                    await db.commit()
                    logger.debug(
                        "Stored description embedding for observation %s",
                        observation_id,
                    )
        except Exception:
            logger.warning(
                "Failed to generate embedding for observation %s. "
                "Search will fall back to keyword matching.",
                observation_id,
            )
