"""
Face detection and recognition module.

Uses face_recognition library (dlib-based) for detection and 128-dim
embedding generation. Matches detected faces against known embeddings
stored in pgvector.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import numpy as np

from shared.database import async_session
from shared.models import FaceEmbedding, Person
from sqlalchemy import select

logger = logging.getLogger("nurby.perception.faces")

# Cosine similarity threshold for face matching
MATCH_THRESHOLD = 0.6

# Max distance to consider same person cluster
CLUSTER_THRESHOLD = 0.5


class FaceRecognizer:
    def __init__(self):
        self._lib = None

    def _load(self):
        if self._lib is None:
            try:
                import face_recognition
                self._lib = face_recognition
                logger.info("face_recognition library loaded")
            except ImportError:
                logger.warning(
                    "face_recognition not installed. Face recognition disabled. "
                    "Install with: pip install face_recognition"
                )
        return self._lib

    async def detect_and_embed(self, frame: np.ndarray) -> list[dict]:
        """Detect faces in frame, return list of {bbox, embedding}."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._detect_sync, frame)

    def _detect_sync(self, frame: np.ndarray) -> list[dict]:
        lib = self._load()
        if lib is None:
            return []

        # Convert BGR (OpenCV) to RGB (face_recognition)
        rgb = frame[:, :, ::-1]

        # Detect face locations
        locations = lib.face_locations(rgb, model="hog")
        if not locations:
            return []

        # Generate 128-dim embeddings
        encodings = lib.face_encodings(rgb, locations)

        faces = []
        for (top, right, bottom, left), encoding in zip(locations, encodings):
            faces.append({
                "bbox": [left, top, right, bottom],
                "embedding": encoding.tolist(),
            })

        logger.debug("Detected %d face(s)", len(faces))
        return faces

    async def match_faces(self, faces: list[dict]) -> list[dict]:
        """Match detected faces against known embeddings in DB.

        Returns faces with added person_id + display_name if matched.
        """
        if not faces:
            return faces

        # Load all known embeddings
        known = await self._load_known_embeddings()
        if not known:
            return faces

        for face in faces:
            face_emb = np.array(face["embedding"])
            best_match = None
            best_distance = float("inf")

            for person_id, person_name, known_emb in known:
                distance = np.linalg.norm(face_emb - known_emb)
                if distance < best_distance:
                    best_distance = distance
                    best_match = (person_id, person_name)

            if best_match and best_distance < MATCH_THRESHOLD:
                face["person_id"] = str(best_match[0])
                face["person_name"] = best_match[1]
                face["match_distance"] = round(float(best_distance), 4)
                logger.info(
                    "Matched face to %s (distance=%.4f)",
                    best_match[1], best_distance,
                )
            else:
                face["person_id"] = None
                face["person_name"] = None
                face["match_distance"] = None

        return faces

    async def _load_known_embeddings(self) -> list[tuple]:
        """Load all face embeddings from DB. Returns [(person_id, name, np.array)]."""
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(
                        FaceEmbedding.person_id,
                        Person.display_name,
                        FaceEmbedding.embedding,
                    ).join(Person, FaceEmbedding.person_id == Person.id)
                    .where(Person.consent_given == True)
                )
                rows = result.all()
                return [
                    (row[0], row[1], np.array(row[2]))
                    for row in rows
                ]
        except Exception:
            logger.exception("Failed to load known embeddings")
            return []

    async def cluster_unknown_face(self, face: dict, camera_id: str, frame: np.ndarray | None = None) -> uuid.UUID | None:
        """Cluster an unmatched face. Either adds to existing cluster or creates new one.

        face dict must have 'embedding' and 'bbox' keys.
        Returns the cluster_id the face was assigned to, so callers can persist
        it on the observation for later grouping.
        """
        face_emb = np.array(face["embedding"])

        # Load existing pending clusters
        clusters = await self._load_pending_clusters()

        best_cluster_id = None
        best_distance = float("inf")

        for cluster_id, rep_emb in clusters:
            distance = np.linalg.norm(face_emb - rep_emb)
            if distance < best_distance:
                best_distance = distance
                best_cluster_id = cluster_id

        # Save face crop thumbnail
        thumbnail_path = None
        if frame is not None:
            thumbnail_path = self._save_face_crop(face, frame, camera_id)

        if best_cluster_id and best_distance < CLUSTER_THRESHOLD:
            # Add to existing cluster
            await self._add_to_cluster(best_cluster_id, face_emb, camera_id, thumbnail_path)
            logger.info("Added face to cluster %s (distance=%.4f)", best_cluster_id, best_distance)
            return best_cluster_id
        else:
            # Create new cluster
            new_id = await self._create_cluster(face_emb, camera_id, thumbnail_path)
            logger.info("Created new face cluster %s", new_id)
            if new_id:
                # Fire-and-forget VLM appearance description on the initial crop
                asyncio.create_task(self._generate_appearance_description(new_id, thumbnail_path))
            return new_id

    def _save_face_crop(self, face: dict, frame: np.ndarray, camera_id: str) -> str | None:
        """Crop face from frame and save as thumbnail."""
        try:
            import cv2
            import os
            from shared.config import settings

            bbox = face["bbox"]  # [left, top, right, bottom]
            h, w = frame.shape[:2]

            # Add padding around face (20%)
            pad_x = int((bbox[2] - bbox[0]) * 0.2)
            pad_y = int((bbox[3] - bbox[1]) * 0.2)
            x1 = max(0, bbox[0] - pad_x)
            y1 = max(0, bbox[1] - pad_y)
            x2 = min(w, bbox[2] + pad_x)
            y2 = min(h, bbox[3] + pad_y)

            crop = frame[y1:y2, x1:x2]

            face_dir = os.path.join(settings.thumbnails_path, "faces")
            os.makedirs(face_dir, exist_ok=True)

            filename = f"{camera_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
            path = os.path.join(face_dir, filename)
            cv2.imwrite(path, crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            return path
        except Exception:
            logger.exception("Failed to save face crop")
            return None

    async def _load_pending_clusters(self) -> list[tuple]:
        """Load pending face clusters. Returns [(cluster_id, np.array)]."""
        try:
            from shared.models import FaceCluster
            async with async_session() as db:
                result = await db.execute(
                    select(FaceCluster.id, FaceCluster.representative_embedding)
                    .where(FaceCluster.status == "pending")
                )
                return [
                    (row[0], np.array(row[1]))
                    for row in result.all()
                ]
        except Exception:
            logger.exception("Failed to load face clusters")
            return []

    async def _add_to_cluster(self, cluster_id: uuid.UUID, embedding: np.ndarray, camera_id: str, thumbnail_path: str | None):
        """Add a face sighting to an existing cluster and update representative embedding."""
        try:
            from shared.models import FaceCluster, FaceClusterSample
            async with async_session() as db:
                cluster = await db.get(FaceCluster, cluster_id)
                if not cluster:
                    return

                # Add sample
                sample = FaceClusterSample(
                    cluster_id=cluster_id,
                    camera_id=uuid.UUID(camera_id),
                    embedding=embedding.tolist(),
                    thumbnail_path=thumbnail_path,
                )
                db.add(sample)

                # Update cluster stats
                cluster.sighting_count += 1
                cluster.last_seen_at = datetime.now(timezone.utc)

                # Update representative embedding (running average)
                old_emb = np.array(cluster.representative_embedding)
                n = cluster.sighting_count
                new_rep = ((old_emb * (n - 1)) + embedding) / n
                cluster.representative_embedding = new_rep.tolist()

                # Update thumbnail if this is a better crop (use first few samples)
                if cluster.sighting_count <= 5 and thumbnail_path:
                    cluster.sample_thumbnail_path = thumbnail_path

                await db.commit()
        except Exception:
            logger.exception("Failed to add to cluster %s", cluster_id)

    async def _create_cluster(self, embedding: np.ndarray, camera_id: str, thumbnail_path: str | None) -> uuid.UUID | None:
        """Create a new face cluster from a single face detection."""
        try:
            from shared.models import FaceCluster, FaceClusterSample
            from sqlalchemy import text
            async with async_session() as db:
                # Allocate sequential label number. Sequence guarantees uniqueness
                # across concurrent inserts.
                label_row = await db.execute(text("SELECT nextval('face_cluster_label_seq')"))
                label_num = int(label_row.scalar() or 0)

                cluster = FaceCluster(
                    representative_embedding=embedding.tolist(),
                    sample_thumbnail_path=thumbnail_path,
                    first_camera_id=uuid.UUID(camera_id),
                    sighting_count=1,
                    auto_label_number=label_num,
                    appearance_description_status="pending",
                )
                db.add(cluster)
                await db.flush()

                sample = FaceClusterSample(
                    cluster_id=cluster.id,
                    camera_id=uuid.UUID(camera_id),
                    embedding=embedding.tolist(),
                    thumbnail_path=thumbnail_path,
                )
                db.add(sample)
                await db.commit()
                return cluster.id
        except Exception:
            logger.exception("Failed to create face cluster")
            return None

    async def _generate_appearance_description(self, cluster_id: uuid.UUID, thumbnail_path: str | None):
        """Run a VLM pass on the face crop to produce a short appearance label.

        Examples. "Caucasian male, 30s, blue jacket". Stored on the cluster so
        the UI can show a meaningful hint next to "Unknown 645".
        """
        if not thumbnail_path:
            return
        try:
            import os
            import cv2
            from shared.models import FaceCluster
            from services.perception.vlm import VLMClient, get_active_provider

            if not os.path.exists(thumbnail_path):
                return
            img = cv2.imread(thumbnail_path)
            if img is None:
                return

            provider = await get_active_provider()
            if not provider:
                async with async_session() as db:
                    cluster = await db.get(FaceCluster, cluster_id)
                    if cluster:
                        cluster.appearance_description_status = "failed"
                        await db.commit()
                return

            system_prompt = (
                "You describe the appearance of a person from a single face crop. "
                "Return 8 words or fewer. Cover apparent demographics (ethnicity, "
                "gender, age range) and any obvious clothing or accessories visible. "
                "No speculation beyond what the image shows. No sentences, just a "
                "short label. Example. 'Caucasian male, 30s, dark jacket'."
            )
            client = VLMClient()
            desc = await client.describe(
                frame=img,
                detections=[],
                provider=provider,
                system_prompt=system_prompt,
                max_tokens=40,
            )

            async with async_session() as db:
                cluster = await db.get(FaceCluster, cluster_id)
                if not cluster:
                    return
                if desc:
                    cluster.appearance_description = desc.strip().strip('"').strip(".")
                    cluster.appearance_description_status = "done"
                else:
                    cluster.appearance_description_status = "failed"
                await db.commit()
        except Exception:
            logger.exception("Failed to generate appearance description for cluster %s", cluster_id)

    @staticmethod
    def embed_from_image(image_bytes: bytes) -> list[float] | None:
        """Generate face embedding from image bytes (for upload flow)."""
        try:
            import face_recognition
            import numpy as np

            nparr = np.frombuffer(image_bytes, dtype=np.uint8)
            import cv2
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            rgb = img[:, :, ::-1]
            encodings = face_recognition.face_encodings(rgb)
            if not encodings:
                return None
            return encodings[0].tolist()
        except Exception:
            logger.exception("Failed to generate embedding from image")
            return None
