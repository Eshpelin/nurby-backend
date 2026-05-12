"""
Body re-identification module.

Pairs with services/perception/faces.py. Where face recognition uses
ArcFace on the face crop to recover identity, this module computes an
OSNet appearance embedding on the full-body crop. The embedding lives
in a space where same-individual crops sit close together even when
camera, angle, or lighting changes, so we can link the same person
across cameras and over time when their face is not visible.

Pipeline.
1. For every YOLO person detection on a keyframe, crop the bbox.
2. Compute a 512-dim OSNet embedding + a coarse HSV color histogram.
3. Cluster against existing body_clusters via pgvector cosine search.
4. Either append to the nearest cluster or open a new one.
5. Promote a cluster to "confirmed" once a face hit in the same frame
   maps it to a face cluster with a known Person.

Threshold tuning.
- OSNet ships normalized embeddings; cosine distance ~0.30 is the
  same-identity boundary on Market1501. We pick a slightly tighter
  default (0.27) because cross-camera lighting variance in a home is
  smaller than the Market1501 benchmark distribution.
- Color histogram chi-square is a soft tie-breaker, not a gate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

import numpy as np

from shared.config import settings
from shared.database import async_session
from shared.models import BodyCluster, BodyClusterSample, FaceCluster, Person
from sqlalchemy import select

logger = logging.getLogger("nurby.perception.reid")

# Cosine distance thresholds. OSNet outputs normalized embeddings so
# the cosine distance is bounded in [0, 2].
MATCH_THRESHOLD = 0.30   # link to a known Person via body
CLUSTER_THRESHOLD = 0.27  # tighter band for opening / merging clusters

# Limit. number of clusters compared per query before we give up and
# open a new one. pgvector ANN with ivfflat handles the heavy lifting
# but we still cap to bound per-frame latency.
MAX_CANDIDATES = 32


class BodyReID:
    """Lazy-loaded OSNet body re-identification model."""

    def __init__(self):
        self._model = None
        self._transform = None
        self._torch = None
        self._device = "cpu"
        # Sticky flag. Once load fails we don't retry every frame.
        self._load_failed = False

    def _load(self):
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        # Import torch and torchreid separately so the log clearly says
        # which one is missing. Both are heavy. Either being absent
        # disables body re-id without breaking the rest of perception.
        try:
            import torch  # type: ignore
        except ImportError:
            logger.warning(
                "torch not installed. Body re-identification disabled. "
                "Install torch + torchreid in the perception container."
            )
            self._load_failed = True
            return None
        try:
            from torchreid.utils import FeatureExtractor  # type: ignore
        except ImportError:
            logger.warning(
                "torchreid not installed. Body re-identification disabled. "
                "Install with. pip install torchreid"
            )
            self._load_failed = True
            return None
        try:
            self._torch = torch
            # Pick the best device once and stick with it. cuda > mps > cpu.
            if torch.cuda.is_available():
                self._device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self._device = "mps"
            else:
                self._device = "cpu"
            # osnet_x1_0. Market1501 + MSMT17 pretrained. ~2M params,
            # ~5ms CPU per crop. Swap to osnet_ain_x1_0 for slightly
            # better cross-domain results when GPU headroom allows.
            extractor = FeatureExtractor(
                model_name="osnet_x1_0",
                model_path="",
                device=self._device,
            )
            self._model = extractor
            logger.info("OSNet body ReID loaded on %s", self._device)
        except Exception:
            logger.exception("Failed to load OSNet. Body re-identification disabled.")
            self._load_failed = True
            return None
        return self._model

    # ------------------------------------------------------------------
    # Public API

    async def embed_persons(
        self, frame: np.ndarray, person_detections: list[dict],
    ) -> list[dict]:
        """Compute body embeddings for every person bbox.

        Augments each detection dict in-place with `body_embedding`
        (list[float]) and `color_histogram` (dict). Returns the same
        list for ergonomics.
        """
        if not person_detections:
            return person_detections
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._embed_sync, frame, person_detections,
        )

    def _embed_sync(self, frame: np.ndarray, persons: list[dict]) -> list[dict]:
        model = self._load()
        if model is None:
            return persons
        crops = []
        valid = []
        h, w = frame.shape[:2]
        for det in persons:
            bbox = det.get("bbox")
            if not bbox:
                continue
            x1, y1, x2, y2 = [max(0, int(v)) for v in bbox]
            x2 = min(w, x2)
            y2 = min(h, y2)
            if x2 - x1 < 32 or y2 - y1 < 64:
                # Too small to be useful for ReID. Skip.
                continue
            crops.append(frame[y1:y2, x1:x2])
            valid.append(det)
        if not crops:
            return persons
        try:
            features = model(crops)  # torch.Tensor [N, 512]
        except Exception:
            logger.exception("OSNet inference failed")
            return persons
        # Normalize once so cosine distance == 1 - dot.
        feats = features.cpu().numpy()
        norms = np.linalg.norm(feats, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        feats = feats / norms

        for det, crop, emb in zip(valid, crops, feats):
            det["body_embedding"] = emb.tolist()
            det["color_histogram"] = _hsv_histogram(crop)
        return persons

    async def cluster_body(
        self,
        det: dict,
        camera_id: str,
        frame: np.ndarray | None,
        face_cluster_ids: list[uuid.UUID] | None = None,
    ) -> uuid.UUID | None:
        """Assign a body detection to a cluster.

        If `face_cluster_ids` is non-empty (one or more co-located face
        hits on the same frame), and any of those face clusters maps to
        a Person, we use that Person as a strong prior. The resulting
        body cluster will be tagged `confirmed` and linked to the
        Person.
        """
        emb = det.get("body_embedding")
        if not emb:
            return None
        body_emb = np.array(emb, dtype=np.float32)

        person_id, face_cluster_id = await self._resolve_face_prior(face_cluster_ids)

        candidates = await self._search_clusters(body_emb)
        best_id = None
        best_distance = float("inf")
        for cluster_id, rep_emb, cluster_person_id in candidates:
            distance = _cosine_distance(body_emb, rep_emb)
            # When we have a face prior, only merge with clusters that
            # are unlinked or already linked to the same Person.
            if person_id is not None and cluster_person_id is not None and cluster_person_id != person_id:
                continue
            if distance < best_distance:
                best_distance = distance
                best_id = cluster_id

        thumbnail_path = None
        if frame is not None:
            thumbnail_path = self._save_body_crop(det, frame, camera_id)

        if best_id is not None and best_distance < CLUSTER_THRESHOLD:
            await self._add_to_cluster(
                best_id, body_emb, det, camera_id, thumbnail_path,
                face_prior_person_id=person_id,
                face_cluster_id=face_cluster_id,
            )
            return best_id

        new_id = await self._create_cluster(
            body_emb, det, camera_id, thumbnail_path,
            face_prior_person_id=person_id,
            face_cluster_id=face_cluster_id,
        )
        return new_id

    # ------------------------------------------------------------------
    # Internals

    async def _resolve_face_prior(
        self, face_cluster_ids: list[uuid.UUID] | None,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        """Return (person_id, face_cluster_id) for the strongest face prior."""
        if not face_cluster_ids:
            return None, None
        try:
            async with async_session() as db:
                rows = await db.execute(
                    select(FaceCluster.id, FaceCluster.person_id)
                    .where(FaceCluster.id.in_(face_cluster_ids))
                    .where(FaceCluster.person_id.is_not(None))
                )
                for row in rows.all():
                    return row.person_id, row.id
        except Exception:
            logger.exception("face prior lookup failed")
        return None, None

    async def _search_clusters(
        self, body_emb: np.ndarray,
    ) -> list[tuple[uuid.UUID, np.ndarray, uuid.UUID | None]]:
        """Top-K nearest body clusters by cosine distance."""
        try:
            async with async_session() as db:
                # pgvector cosine search. `<=>` is cosine distance for
                # vector_cosine_ops index. Cast to list for SQL bind.
                stmt = (
                    select(
                        BodyCluster.id,
                        BodyCluster.representative_embedding,
                        BodyCluster.person_id,
                    )
                    .where(BodyCluster.status != "ignored")
                    .order_by(BodyCluster.representative_embedding.cosine_distance(body_emb.tolist()))
                    .limit(MAX_CANDIDATES)
                )
                rows = await db.execute(stmt)
                out = []
                for row in rows.all():
                    rep = np.array(row.representative_embedding, dtype=np.float32)
                    out.append((row.id, rep, row.person_id))
                return out
        except Exception:
            logger.exception("body cluster search failed")
            return []

    async def _add_to_cluster(
        self,
        cluster_id: uuid.UUID,
        emb: np.ndarray,
        det: dict,
        camera_id: str,
        thumbnail_path: str | None,
        face_prior_person_id: uuid.UUID | None,
        face_cluster_id: uuid.UUID | None,
    ) -> None:
        try:
            async with async_session() as db:
                cluster = await db.get(BodyCluster, cluster_id)
                if cluster is None:
                    return
                # Running mean of the representative embedding.
                n = max(1, cluster.sighting_count)
                rep = np.array(cluster.representative_embedding, dtype=np.float32)
                new_rep = (rep * n + emb) / (n + 1)
                norm = np.linalg.norm(new_rep)
                if norm > 0:
                    new_rep = new_rep / norm
                cluster.representative_embedding = new_rep.tolist()
                cluster.sighting_count = n + 1
                cluster.last_seen_at = datetime.now(timezone.utc)
                if face_prior_person_id is not None:
                    if cluster.person_id is None:
                        cluster.person_id = face_prior_person_id
                    cluster.linked_face_cluster_id = face_cluster_id
                    cluster.confidence = "confirmed"
                sample = BodyClusterSample(
                    cluster_id=cluster_id,
                    camera_id=uuid.UUID(camera_id),
                    embedding=emb.tolist(),
                    color_histogram=det.get("color_histogram"),
                    thumbnail_path=thumbnail_path,
                    bbox=det.get("bbox"),
                )
                db.add(sample)
                await db.commit()
        except Exception:
            logger.exception("Failed to append body sample to cluster %s", cluster_id)

    async def _create_cluster(
        self,
        emb: np.ndarray,
        det: dict,
        camera_id: str,
        thumbnail_path: str | None,
        face_prior_person_id: uuid.UUID | None,
        face_cluster_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        try:
            async with async_session() as db:
                cluster = BodyCluster(
                    representative_embedding=emb.tolist(),
                    representative_color=det.get("color_histogram"),
                    sample_thumbnail_path=thumbnail_path,
                    first_camera_id=uuid.UUID(camera_id),
                    person_id=face_prior_person_id,
                    linked_face_cluster_id=face_cluster_id,
                    confidence="confirmed" if face_prior_person_id else "tentative",
                )
                db.add(cluster)
                await db.flush()
                sample = BodyClusterSample(
                    cluster_id=cluster.id,
                    camera_id=uuid.UUID(camera_id),
                    embedding=emb.tolist(),
                    color_histogram=det.get("color_histogram"),
                    thumbnail_path=thumbnail_path,
                    bbox=det.get("bbox"),
                )
                db.add(sample)
                await db.commit()
                return cluster.id
        except Exception:
            logger.exception("Failed to create body cluster")
            return None

    def _save_body_crop(self, det: dict, frame: np.ndarray, camera_id: str) -> str | None:
        try:
            import cv2

            bbox = det.get("bbox")
            if not bbox:
                return None
            h, w = frame.shape[:2]
            x1 = max(0, int(bbox[0]))
            y1 = max(0, int(bbox[1]))
            x2 = min(w, int(bbox[2]))
            y2 = min(h, int(bbox[3]))
            crop = frame[y1:y2, x1:x2]
            body_dir = os.path.join(settings.thumbnails_path, "bodies")
            os.makedirs(body_dir, exist_ok=True)
            filename = (
                f"{camera_id}_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
                f"{uuid.uuid4().hex[:8]}.jpg"
            )
            path = os.path.join(body_dir, filename)
            cv2.imwrite(path, crop)
            return path
        except Exception:
            logger.exception("Failed to save body crop")
            return None


# ------------------------------------------------------------------
# Helpers

def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


def _hsv_histogram(crop: np.ndarray) -> dict:
    """Coarse HSV histogram for color similarity. Cheap clothing prior.

    32 bins on Hue, 8 on Saturation. Skips dark/under-saturated pixels
    (background, shadows) to focus on clothing color.
    """
    try:
        import cv2
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        mask = (s > 30) & (v > 40)
        if not mask.any():
            return {"h": [], "s": []}
        hist_h = np.bincount(h[mask].ravel(), minlength=180)[:180]
        hist_s = np.bincount(s[mask].ravel(), minlength=256)[:256]
        # Compress to 32+8 bins.
        hist_h = hist_h.reshape(32, -1).sum(axis=1)
        hist_s = hist_s.reshape(8, -1).sum(axis=1)
        h_norm = hist_h / max(1, hist_h.sum())
        s_norm = hist_s / max(1, hist_s.sum())
        return {"h": h_norm.round(4).tolist(), "s": s_norm.round(4).tolist()}
    except Exception:
        return {"h": [], "s": []}
