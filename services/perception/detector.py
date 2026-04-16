"""
Object detector using YOLO. Runs inference on frames and returns
structured detection results with bounding boxes and labels.

Supports multiple YOLO models with a shared model cache. Models are
loaded once and reused across cameras. Detection results from
multiple models can be merged using configurable strategies.
"""

import asyncio
import logging
from collections import defaultdict

import numpy as np

logger = logging.getLogger("nurby.perception.detector")

# Default classes for home security when no label_filter is set.
# Models with a custom label_filter bypass this entirely.
DEFAULT_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "cat", "dog", "bird",
    "backpack", "umbrella", "handbag", "suitcase",
    "cell phone", "laptop",
}

# Full COCO class set for reference (YOLOv8 supports all 80)
COCO_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "airplane", "bus",
    "train", "truck", "boat", "traffic light", "fire hydrant",
    "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
    "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat",
    "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "mouse", "remote", "keyboard", "cell phone", "microwave", "oven",
    "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
}

DEFAULT_CONFIDENCE = 0.35
DEFAULT_MODEL = "yolov8n.pt"

# Class-level model cache shared across all ObjectDetector instances
_model_cache: dict[str, object] = {}


def _compute_iou(box_a: list[int], box_b: list[int]) -> float:
    """Compute Intersection over Union for two bounding boxes.

    Each box is [x1, y1, x2, y2].
    """
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection == 0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - intersection

    if union == 0:
        return 0.0
    return intersection / union


class ObjectDetector:
    def __init__(self, model_name: str = DEFAULT_MODEL, confidence: float = DEFAULT_CONFIDENCE):
        self._model_name = model_name
        self._confidence = confidence

    @staticmethod
    def _load_model(model_name: str):
        """Load a YOLO model, using the shared cache to avoid reloading."""
        if model_name in _model_cache:
            return _model_cache[model_name]

        try:
            from ultralytics import YOLO
            logger.info("Loading YOLO model '%s'", model_name)
            model = YOLO(model_name)
            _model_cache[model_name] = model
            logger.info("YOLO model '%s' loaded and cached", model_name)
            return model
        except ImportError:
            logger.warning(
                "ultralytics not installed. Object detection disabled. "
                "Install with pip install ultralytics"
            )
            return None

    async def detect(self, frame: np.ndarray, confidence: float | None = None) -> list[dict]:
        """Run object detection on a frame using the default model.

        Returns list of detections. Backward compatible single-model path.
        """
        conf = confidence if confidence is not None else self._confidence
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._detect_sync, frame, conf, self._model_name, None)

    async def detect_multi(
        self,
        frame: np.ndarray,
        model_configs: list[dict],
        merge: str = "any",
        consensus_min: int = 2,
    ) -> list[dict]:
        """Run detection with multiple models and merge results.

        Each entry in model_configs should have.
            model (str) - model filename, e.g. "yolov8n.pt"
            confidence (float) - confidence threshold for this model
            enabled (bool) - whether this model is active
            label_filter (list[str] | None) - optional list of labels to keep

        The merge parameter controls how detections are combined.
            "any"       - union all detections, apply NMS to remove duplicates
            "consensus" - only keep detections where consensus_min+ models agree
            "best"      - for each location, keep highest confidence detection
        """
        enabled_configs = [c for c in model_configs if c.get("enabled", True)]
        if not enabled_configs:
            return []

        loop = asyncio.get_event_loop()

        # Run each model in the thread pool
        tasks = []
        for cfg in enabled_configs:
            model_name = cfg.get("model", DEFAULT_MODEL)
            conf = cfg.get("confidence", DEFAULT_CONFIDENCE)
            label_filter = cfg.get("label_filter")
            tasks.append(
                loop.run_in_executor(None, self._detect_sync, frame, conf, model_name, label_filter)
            )

        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect detections from all models, tagging each with its source model
        all_detections = []
        for cfg, result in zip(enabled_configs, all_results):
            if isinstance(result, Exception):
                logger.error("Detection failed for model '%s'. %s", cfg.get("model"), result)
                continue
            model_name = cfg.get("model", DEFAULT_MODEL)
            for det in result:
                det["model"] = model_name
                all_detections.append(det)

        if not all_detections:
            return []

        return self._merge_detections(all_detections, merge, consensus_min)

    def _detect_sync(
        self,
        frame: np.ndarray,
        confidence: float,
        model_name: str,
        label_filter: list[str] | None = None,
    ) -> list[dict]:
        """Synchronous detection with a specific model (runs in thread pool)."""
        model = self._load_model(model_name)
        if model is None:
            return []

        results = model(frame, conf=confidence, verbose=False)
        detections = []

        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i])
                label = model.names[cls_id]
                conf_val = float(boxes.conf[i])

                # Apply label filter. Per-model filter takes priority,
                # otherwise fall back to default security-relevant classes
                allowed = set(label_filter) if label_filter else DEFAULT_CLASSES
                if label not in allowed:
                    continue

                x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                detections.append({
                    "label": label,
                    "confidence": round(conf_val, 3),
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "class_id": cls_id,
                })

        return detections

    def _merge_detections(
        self,
        detections: list[dict],
        strategy: str,
        consensus_min: int,
    ) -> list[dict]:
        """Merge detections from multiple models using the given strategy."""
        if strategy == "consensus":
            return self._merge_consensus(detections, consensus_min)
        elif strategy == "best":
            return self._merge_best(detections)
        else:
            # Default "any" strategy. Union with NMS
            return self._merge_any(detections)

    def _merge_any(self, detections: list[dict], iou_threshold: float = 0.5) -> list[dict]:
        """Union all detections and apply NMS to remove duplicate bboxes.

        When two detections overlap (IoU > threshold) and share the same label,
        the one with higher confidence is kept.
        """
        if not detections:
            return []

        # Sort by confidence descending
        sorted_dets = sorted(detections, key=lambda d: d["confidence"], reverse=True)
        kept = []

        for det in sorted_dets:
            is_duplicate = False
            for existing in kept:
                if det["label"] == existing["label"] and _compute_iou(det["bbox"], existing["bbox"]) > iou_threshold:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(det)

        return kept

    def _merge_consensus(self, detections: list[dict], min_models: int) -> list[dict]:
        """Keep only detections where min_models+ models detected the same
        label in an overlapping bounding box region (IoU > 0.5).
        """
        if not detections:
            return []

        # Group detections into clusters of overlapping same-label detections
        clusters: list[list[dict]] = []

        for det in detections:
            placed = False
            for cluster in clusters:
                # Check if this detection overlaps with any in the cluster
                if cluster[0]["label"] == det["label"]:
                    for existing in cluster:
                        if _compute_iou(det["bbox"], existing["bbox"]) > 0.5:
                            cluster.append(det)
                            placed = True
                            break
                if placed:
                    break
            if not placed:
                clusters.append([det])

        # Only keep clusters where enough distinct models contributed
        result = []
        for cluster in clusters:
            distinct_models = {d.get("model", "unknown") for d in cluster}
            if len(distinct_models) >= min_models:
                # Pick the highest confidence detection from the cluster
                best = max(cluster, key=lambda d: d["confidence"])
                best["model"] = "consensus"
                result.append(best)

        return result

    def _merge_best(self, detections: list[dict], iou_threshold: float = 0.5) -> list[dict]:
        """For each unique object location, keep only the detection
        with the highest confidence regardless of which model produced it.
        """
        if not detections:
            return []

        # Sort by confidence descending
        sorted_dets = sorted(detections, key=lambda d: d["confidence"], reverse=True)
        kept = []

        for det in sorted_dets:
            is_duplicate = False
            for existing in kept:
                if _compute_iou(det["bbox"], existing["bbox"]) > iou_threshold:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(det)

        return kept

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
