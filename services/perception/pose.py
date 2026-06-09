"""Pose inference wrapper for HAR (Phase 2).

Returns, per frame, a list of ``{bbox, keypoints}`` where keypoints is 17 (x, y, conf) in
COCO order. Two backends behind one call, matching the design doc:

- ``ultralytics`` (default, runnable): a one-stage YOLO pose model. Already a dependency, so
  no new install. It detects persons AND keypoints in one pass.
- ``rtmlib`` (preferred on a real deployment): RTMPose via onnxruntime, Apache-2.0,
  CPU-fast. Loaded lazily; if rtmlib is absent we fall back to ultralytics.

INTEGRATION-PENDING runtime: weights download on first use and inference runs on the live
frame inside the ingestion executor. The shape contract here is what the rest of HAR
consumes, so callers (HARRunner) are testable without a model.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("nurby.perception.pose")


class PoseEstimator:
    """Lazy pose model. ``infer(frame)`` -> list of ``{bbox, keypoints}``."""

    def __init__(self, backend: str = "ultralytics", model_path: str | None = None,
                 conf: float = 0.3):
        self.backend = backend
        self.model_path = model_path
        self.conf = conf
        self._model = None

    def _load(self):  # pragma: no cover - needs weights/network
        if self._model is not None:
            return self._model
        if self.backend == "rtmlib":
            try:
                from rtmlib import Body  # type: ignore

                self._model = ("rtmlib", Body(mode="lightweight", backend="onnxruntime", device="cpu"))
                return self._model
            except Exception:
                logger.warning("rtmlib unavailable, falling back to ultralytics pose")
        from ultralytics import YOLO

        self._model = ("ultralytics", YOLO(self.model_path or "yolo11n-pose.pt"))
        return self._model

    def infer(self, frame) -> list[dict]:  # pragma: no cover - needs weights/network
        kind, model = self._load()
        out: list[dict] = []
        try:
            if kind == "rtmlib":
                keypoints, scores = model(frame)
                for kp, sc in zip(keypoints, scores):
                    pts = [(float(x), float(y), float(c)) for (x, y), c in zip(kp, sc)]
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    bbox = [min(xs), min(ys), max(xs), max(ys)] if pts else None
                    out.append({"bbox": bbox, "keypoints": pts})
            else:
                res = model(frame, verbose=False, conf=self.conf)[0]
                kps = getattr(res, "keypoints", None)
                boxes = getattr(res, "boxes", None)
                if kps is None or kps.data is None:
                    return out
                for i, person in enumerate(kps.data):
                    pts = [(float(x), float(y), float(c)) for x, y, c in person.tolist()]
                    bbox = None
                    if boxes is not None and i < len(boxes):
                        bbox = [float(v) for v in boxes.xyxy[i].tolist()]
                    out.append({"bbox": bbox, "keypoints": pts})
        except Exception:
            logger.debug("pose inference failed", exc_info=True)
        return out
