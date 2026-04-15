"""
Object detector using YOLO. Runs inference on frames and returns
structured detection results with bounding boxes and labels.

Uses ultralytics YOLOv8 nano model by default for fast inference
on CPU. Model is downloaded automatically on first run.
"""

import asyncio
import logging
from functools import lru_cache

import numpy as np

logger = logging.getLogger("nurby.perception.detector")

# COCO classes we care about for home security
RELEVANT_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "cat", "dog", "bird",
    "backpack", "umbrella", "handbag", "suitcase",
    "cell phone", "laptop",
}

DEFAULT_CONFIDENCE = 0.35


class ObjectDetector:
    def __init__(self, model_name: str = "yolov8n.pt", confidence: float = DEFAULT_CONFIDENCE):
        self._model_name = model_name
        self._confidence = confidence
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from ultralytics import YOLO
                logger.info("Loading YOLO model '%s'", self._model_name)
                self._model = YOLO(self._model_name)
                logger.info("YOLO model loaded")
            except ImportError:
                logger.warning(
                    "ultralytics not installed. Object detection disabled. "
                    "Install with: pip install ultralytics"
                )
        return self._model

    async def detect(self, frame: np.ndarray) -> list[dict]:
        """Run object detection on a frame. Returns list of detections."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._detect_sync, frame)

    def _detect_sync(self, frame: np.ndarray) -> list[dict]:
        """Synchronous detection (runs in thread pool)."""
        model = self._load_model()
        if model is None:
            return []

        results = model(frame, conf=self._confidence, verbose=False)
        detections = []

        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i])
                label = model.names[cls_id]
                conf = float(boxes.conf[i])

                # Filter to relevant classes
                if label not in RELEVANT_CLASSES:
                    continue

                x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                detections.append({
                    "label": label,
                    "confidence": round(conf, 3),
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "class_id": cls_id,
                })

        return detections

    @staticmethod
    def summarize(detections: list[dict]) -> str:
        """Human-readable summary of detections."""
        if not detections:
            return "No relevant objects detected"

        counts: dict[str, int] = {}
        for d in detections:
            label = d["label"]
            counts[label] = counts.get(label, 0) + 1

        parts = []
        for label, count in sorted(counts.items(), key=lambda x: -x[1]):
            if count == 1:
                parts.append(label)
            else:
                parts.append(f"{count} {label}s")

        return ", ".join(parts)
