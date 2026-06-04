"""
License plate detection and OCR.

When vehicle objects (car, truck, bus, motorcycle) are detected,
crops the detection region and runs OCR to extract plate text.

Uses EasyOCR as a lightweight, self-contained solution that
works offline without external API calls.
"""

import logging
from functools import lru_cache

import cv2
import numpy as np

logger = logging.getLogger("nurby.perception.plates")

# Vehicle labels that should trigger plate detection
VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle", "van"}

# Minimum detection region size to attempt plate read
MIN_CROP_WIDTH = 60
MIN_CROP_HEIGHT = 40


@lru_cache(maxsize=1)
def _get_reader():
    """Lazy-load EasyOCR reader. Cached so it only loads once."""
    try:
        import easyocr
        # Models are baked into the image (~/.EasyOCR/model). download_enabled
        # False keeps it fully offline. EasyOCR's auto-download fails TLS here
        # and on locked-down hosts, so never reach for the network at runtime.
        reader = easyocr.Reader(
            ["en"], gpu=False, verbose=False, download_enabled=False
        )
        logger.info("EasyOCR reader initialized for plate detection")
        return reader
    except ImportError:
        logger.warning(
            "easyocr not installed. License plate OCR disabled. "
            "Install with: pip install easyocr"
        )
        return None
    except Exception:
        logger.exception("Failed to initialize EasyOCR reader")
        return None


def _clean_plate_text(text: str) -> str | None:
    """Clean and validate extracted plate text.

    Strips whitespace, uppercases, removes non-alphanumeric chars,
    and validates minimum length.
    """
    cleaned = "".join(c for c in text.upper() if c.isalnum() or c == " ").strip()
    # Most license plates are 4-10 chars
    if len(cleaned.replace(" ", "")) < 3:
        return None
    if len(cleaned) > 15:
        return None
    return cleaned


def _preprocess_plate_crop(crop: np.ndarray) -> np.ndarray:
    """Preprocess cropped region for better OCR accuracy.

    Converts to grayscale, applies adaptive threshold, and
    resizes small crops for better character recognition.
    """
    h, w = crop.shape[:2]

    # Upscale small crops
    if w < 200:
        scale = 200 / w
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Denoise
    gray = cv2.bilateralFilter(gray, 11, 17, 17)

    return gray


def detect_plates(
    frame: np.ndarray,
    detections: list[dict],
) -> list[dict]:
    """Detect license plates in vehicle detection regions.

    Takes the full frame and list of YOLO detections. For each vehicle
    detection, crops the lower portion (where plates typically are),
    runs OCR, and returns updated detections with plate_text field.

    Args:
        frame: Full BGR image
        detections: List of detection dicts with label, confidence, bbox

    Returns:
        Updated detections list. Vehicle detections gain a plate_text field.
        Non-vehicle detections pass through unchanged.
        Also appends new license_plate detections with plate text.
    """
    reader = _get_reader()
    if reader is None:
        return detections

    frame_h, frame_w = frame.shape[:2]
    updated = list(detections)
    new_plate_detections = []

    for det in updated:
        if det["label"] not in VEHICLE_LABELS:
            continue

        x1, y1, x2, y2 = det["bbox"]

        # Clamp to frame bounds
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(frame_w, x2)
        y2 = min(frame_h, y2)

        crop_w = x2 - x1
        crop_h = y2 - y1

        if crop_w < MIN_CROP_WIDTH or crop_h < MIN_CROP_HEIGHT:
            continue

        # Focus on lower 60% of vehicle bbox (plates are usually at bottom)
        plate_y1 = y1 + int(crop_h * 0.4)
        plate_crop = frame[plate_y1:y2, x1:x2]

        if plate_crop.size == 0:
            continue

        try:
            processed = _preprocess_plate_crop(plate_crop)
            # Run OCR on preprocessed crop
            results = reader.readtext(processed, detail=1)

            if not results:
                continue

            # Take highest confidence result
            best_text = None
            best_conf = 0.0
            best_bbox_local = None

            for bbox_pts, text, conf in results:
                if conf < 0.3:
                    continue
                cleaned = _clean_plate_text(text)
                if cleaned and conf > best_conf:
                    best_text = cleaned
                    best_conf = conf
                    best_bbox_local = bbox_pts

            if best_text:
                det["plate_text"] = best_text
                logger.info(
                    "Plate detected on %s. text=%s confidence=%.2f",
                    det["label"], best_text, best_conf,
                )

                # Calculate plate bbox in frame coordinates
                if best_bbox_local is not None:
                    pts = np.array(best_bbox_local, dtype=np.int32)
                    px1 = int(x1 + pts[:, 0].min())
                    py1 = int(plate_y1 + pts[:, 1].min())
                    px2 = int(x1 + pts[:, 0].max())
                    py2 = int(plate_y1 + pts[:, 1].max())
                else:
                    px1, py1, px2, py2 = x1, plate_y1, x2, y2

                new_plate_detections.append({
                    "label": "license_plate",
                    "confidence": round(best_conf, 2),
                    "bbox": [px1, py1, px2, py2],
                    "plate_text": best_text,
                })

        except Exception:
            logger.debug("OCR failed for vehicle crop", exc_info=True)
            continue

    updated.extend(new_plate_detections)
    return updated
