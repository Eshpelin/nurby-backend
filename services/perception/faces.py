"""
Face detection and recognition module.

Uses face_recognition library (dlib-based) for detection and 128-dim
embedding generation. Matches detected faces against known embeddings
stored in pgvector.
"""

import asyncio
import logging
import uuid

import numpy as np

from shared.database import async_session
from shared.models import FaceEmbedding, Person
from sqlalchemy import select

logger = logging.getLogger("nurby.perception.faces")

# Cosine similarity threshold for face matching
MATCH_THRESHOLD = 0.6


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
