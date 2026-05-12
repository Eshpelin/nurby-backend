"""Smart privacy zones. detection + frame blur.

Two responsibilities.

1. ``refresh_privacy_zones(camera_id, detections, frame_shape)``
   walks the YOLO detections for the current frame, matches them
   against the camera's ``privacy_zone_targets`` list, and upserts
   PrivacyZone rows. Auto-zones are short-lived. they refresh on
   every keyframe so a bed scoped in the early morning still
   matches the bed at noon even if the camera nudges slightly.

2. ``apply_privacy_blur(frame, zones, strength)`` returns a copy of
   the frame with the listed zones gaussian-blurred. Polygon coords
   are normalized 0-1 so the same zone applies across resolution
   changes. Called from the perception pipeline BEFORE VLM encode
   + thumbnail write.

Recording pipeline blur is a follow-up. ffmpeg stream-copy means
we'd need to decode-blur-reencode the rolling clips, which is hot.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from sqlalchemy import select

from shared.database import async_session
from shared.models import PrivacyZone

logger = logging.getLogger("nurby.perception.privacy")


# Object labels we recognize as private targets. The camera's
# ``privacy_zone_targets`` list filters this set further. Lowercase.
# Keep tight. each entry should be something OIV7 / common YOLO
# models actually detect with reasonable confidence.
SUPPORTED_TARGETS: set[str] = {
    "bed",
    "tv",
    "monitor",
    "laptop",
    "computer monitor",
    "computer keyboard",
    "cell phone",
    "mobile phone",
    "window",
    "door",
    "toilet",
    "bathtub",
    "mirror",
    "picture frame",
}


def bbox_to_polygon(
    bbox: list[float] | list[int],
    frame_w: int,
    frame_h: int,
) -> list[list[float]]:
    """Convert [x1, y1, x2, y2] (pixel) into a 4-point polygon in
    normalized 0-1 coords. Slight outward padding (4%) so the blur
    fully covers the object even when the bbox is tight."""
    if not bbox or len(bbox) < 4:
        return []
    x1, y1, x2, y2 = bbox[:4]
    pad_x = (x2 - x1) * 0.04
    pad_y = (y2 - y1) * 0.04
    x1 = max(0.0, (x1 - pad_x) / max(1, frame_w))
    y1 = max(0.0, (y1 - pad_y) / max(1, frame_h))
    x2 = min(1.0, (x2 + pad_x) / max(1, frame_w))
    y2 = min(1.0, (y2 + pad_y) / max(1, frame_h))
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _iou_norm(a: list[list[float]], b: list[list[float]]) -> float:
    """Cheap IOU for normalized rect polygons."""
    if not a or not b:
        return 0.0
    ax1 = min(p[0] for p in a)
    ay1 = min(p[1] for p in a)
    ax2 = max(p[0] for p in a)
    ay2 = max(p[1] for p in a)
    bx1 = min(p[0] for p in b)
    by1 = min(p[1] for p in b)
    bx2 = max(p[0] for p in b)
    by2 = max(p[1] for p in b)
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(1e-9, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1e-9, (bx2 - bx1) * (by2 - by1))
    return inter / (area_a + area_b - inter)


def matches_targets(label: str | None, targets: list[str] | None) -> bool:
    if not label or not targets:
        return False
    lbl = label.lower()
    for t in targets:
        if not t:
            continue
        if lbl == t.lower():
            return True
    return False


# PTZ pose match tolerance. Defaults assume degrees for pan/tilt
# and a 0-1 normalized zoom from ONVIF. Tweak per-deployment via
# camera settings if presets are tighter / wider apart.
PTZ_PAN_TOLERANCE_DEG = 5.0
PTZ_TILT_TOLERANCE_DEG = 5.0
PTZ_ZOOM_TOLERANCE = 0.1


def ptz_pose_matches(a: dict | None, b: dict | None) -> bool:
    """Compare two PTZ poses with tolerance. Returns True when both
    are missing (fixed camera) OR when pan/tilt/zoom all fall within
    the configured slop. Missing keys count as zero / wildcard so a
    camera that doesn't report zoom still matches.
    """
    if not a and not b:
        return True
    if not a or not b:
        # One side has a pose, the other doesn't. Be strict so a
        # PTZ-tagged zone doesn't apply on a frame where the camera
        # could not report its pose (e.g. ONVIF read failure).
        return False
    try:
        if abs(float(a.get("pan", 0)) - float(b.get("pan", 0))) > PTZ_PAN_TOLERANCE_DEG:
            return False
        if abs(float(a.get("tilt", 0)) - float(b.get("tilt", 0))) > PTZ_TILT_TOLERANCE_DEG:
            return False
        if abs(float(a.get("zoom", 0)) - float(b.get("zoom", 0))) > PTZ_ZOOM_TOLERANCE:
            return False
        return True
    except (TypeError, ValueError):
        return False


async def get_active_zones(
    camera_id: uuid.UUID | str,
    current_pose: dict | None = None,
) -> list[dict]:
    """Pull active privacy zones for the camera, filtered to ones
    that should fire on the current frame.

    Filters applied in this order.
    1. ``active=True``.
    2. Freshness. auto zones whose ``last_seen_at`` is older than
       their ``stale_after_seconds`` are skipped. Manual / locked
       zones ignore freshness.
    3. PTZ pose. zones with a stored ``ptz_pose`` only fire when the
       camera's current pose matches. Zones with null pose fire
       regardless (fixed cameras, manual zones).
    """
    if isinstance(camera_id, str):
        try:
            camera_id = uuid.UUID(camera_id)
        except ValueError:
            return []
    try:
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(PrivacyZone)
                    .where(PrivacyZone.camera_id == camera_id)
                    .where(PrivacyZone.active.is_(True))
                )
            ).scalars().all()
        now = datetime.now(timezone.utc)
        out: list[dict] = []
        for z in rows:
            is_manual_or_locked = z.source != "auto" or z.locked
            if not is_manual_or_locked:
                # Freshness gate for auto zones.
                last = z.last_seen_at
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                stale_s = int(z.stale_after_seconds or 60)
                if last is None or (now - last).total_seconds() > stale_s:
                    continue
            # PTZ pose match. When the zone has a pose, only apply
            # at that pose. When the zone has no pose, it applies
            # whether or not the camera reports one.
            if z.ptz_pose and not ptz_pose_matches(z.ptz_pose, current_pose):
                continue
            out.append(
                {
                    "id": str(z.id),
                    "label": z.label,
                    "polygon": z.polygon,
                    "source": z.source,
                    "locked": z.locked,
                    "ptz_pose": z.ptz_pose,
                }
            )
        return out
    except Exception:
        logger.debug("privacy zone lookup failed", exc_info=True)
        return []


def apply_privacy_blur(
    frame: np.ndarray,
    zones: list[dict],
    strength: int = 55,
) -> np.ndarray:
    """Return a copy of ``frame`` with each zone's polygon
    gaussian-blurred. Polygons are normalized 0-1; we scale to the
    actual frame size on the fly.

    Strength is the Gaussian kernel size (odd; capped). 55 is heavy
    enough to obscure faces on a monitor; 25 just softens. Defaults
    favor privacy.
    """
    if frame is None or frame.size == 0 or not zones:
        return frame
    h, w = frame.shape[:2]
    k = max(5, int(strength) | 1)  # force odd
    k = min(151, k)
    out = frame.copy()
    for z in zones:
        poly = z.get("polygon") or []
        if len(poly) < 3:
            continue
        try:
            pts = np.array(
                [[int(p[0] * w), int(p[1] * h)] for p in poly],
                dtype=np.int32,
            )
        except (TypeError, ValueError):
            continue
        x1 = max(0, int(pts[:, 0].min()))
        y1 = max(0, int(pts[:, 1].min()))
        x2 = min(w, int(pts[:, 0].max()))
        y2 = min(h, int(pts[:, 1].max()))
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        # Blur the bounding rect, then mask via polygon so non-
        # rectangular zones look natural.
        roi = out[y1:y2, x1:x2]
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        local_pts = pts - np.array([x1, y1])
        cv2.fillPoly(mask, [local_pts], 255)
        mask3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) if roi.ndim == 3 else mask
        out[y1:y2, x1:x2] = np.where(mask3 > 0, blurred, roi)
    return out


async def refresh_privacy_zones(
    camera_id: uuid.UUID | str,
    detections: list[dict],
    frame_shape: tuple[int, int],
    targets: list[str] | None,
    min_score: float = 0.4,
    current_pose: dict | None = None,
) -> None:
    """Upsert auto privacy zones from the current detection set.

    For each detection whose label is in ``targets`` and exceeds
    ``min_score``, find a matching auto zone (same label, IOU > 0.5)
    and refresh its last_seen_at + polygon. Otherwise insert a new
    auto zone. Locked or manual zones are never touched.
    """
    if not detections or not targets:
        return
    target_set = {str(t).lower() for t in targets if t}
    if not target_set:
        return
    if isinstance(camera_id, str):
        try:
            camera_id = uuid.UUID(camera_id)
        except ValueError:
            return
    fh, fw = frame_shape[:2]
    incoming: list[dict[str, Any]] = []
    for d in detections:
        lbl = (d.get("label") or "").lower()
        if lbl not in target_set:
            continue
        score = float(d.get("confidence") or 0.0)
        if score < min_score:
            continue
        bbox = d.get("bbox")
        if not bbox:
            continue
        poly = bbox_to_polygon(bbox, fw, fh)
        if not poly:
            continue
        incoming.append(
            {"label": lbl, "polygon": poly, "score": score}
        )
    if not incoming:
        return

    try:
        async with async_session() as db:
            existing = (
                await db.execute(
                    select(PrivacyZone)
                    .where(PrivacyZone.camera_id == camera_id)
                    .where(PrivacyZone.source == "auto")
                )
            ).scalars().all()
            # Bucket by label AND pose so a PTZ camera with the same
            # label seen at two different presets keeps two zones.
            existing_by_key: dict[tuple, list[PrivacyZone]] = {}
            for z in existing:
                pose_key = _pose_key(z.ptz_pose)
                existing_by_key.setdefault(
                    (z.label.lower(), pose_key), []
                ).append(z)

            now = datetime.now(timezone.utc)
            pose_key = _pose_key(current_pose)
            for inc in incoming:
                bucket_key = (inc["label"], pose_key)
                matches = existing_by_key.get(bucket_key, [])
                best: PrivacyZone | None = None
                best_iou = 0.0
                for ez in matches:
                    iou = _iou_norm(ez.polygon or [], inc["polygon"])
                    if iou > best_iou:
                        best = ez
                        best_iou = iou
                if best is not None and best_iou >= 0.4 and not best.locked:
                    best.polygon = inc["polygon"]
                    best.auto_score = inc["score"]
                    best.last_seen_at = now
                    if current_pose is not None:
                        best.ptz_pose = current_pose
                elif best is None or best_iou < 0.4:
                    db.add(
                        PrivacyZone(
                            camera_id=camera_id,
                            label=inc["label"],
                            polygon=inc["polygon"],
                            source="auto",
                            auto_score=inc["score"],
                            active=True,
                            locked=False,
                            detected_at=now,
                            last_seen_at=now,
                            ptz_pose=current_pose,
                        )
                    )
            await db.commit()
    except Exception:
        logger.exception("privacy zone refresh failed cam=%s", camera_id)


def _pose_key(pose: dict | None) -> tuple:
    """Discretize a PTZ pose so two near-identical poses bucket
    together. Buckets are 10° pan/tilt and 0.2 zoom slots — wider
    than the apply-time tolerance so the upsert is forgiving while
    the apply gate is strict."""
    if not pose:
        return ("fixed",)
    try:
        return (
            round(float(pose.get("pan", 0)) / 10.0),
            round(float(pose.get("tilt", 0)) / 10.0),
            round(float(pose.get("zoom", 0)) / 0.2),
        )
    except (TypeError, ValueError):
        return ("fixed",)
