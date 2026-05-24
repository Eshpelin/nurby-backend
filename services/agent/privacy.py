"""Pre-VLM redaction pipeline for agent analyzer frames.

The perception pipeline applies privacy_blur + privacy_zones + nudity
blur before frames leave the box during ingest. The agent's analyzer
path bypasses that pipeline (it pulls historical recordings on
demand), so it MUST re-apply the same layers before any frame bytes
travel to a VLM provider.

Order of operations matches docs/agent-design.md section 6.1.

1. Camera privacy zones via ``services.perception.privacy``.
2. Per-Person face blur for any Person with ``privacy_blur=True``.
3. Nudity safety floor via NudeNet (always-on for agent frames).

Every call returns a copy of the input frame plus a
``RedactionReport`` describing what was applied. The report is stored
on the audit row so the household can verify their privacy settings
were honored.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select

from shared.app_settings import get_setting
from shared.models import FaceEmbedding, Person

logger = logging.getLogger("nurby.agent.privacy")


@dataclass
class RedactionReport:
    """Audit record describing what redaction layers fired on a frame.

    ``privacy_zones_applied``. count of PrivacyZone rows that matched
        on the source camera and were blurred.
    ``person_faces_blurred``. list of Person UUIDs whose faces were
        located + blurred via the protected-face pass.
    ``nudity_regions_blurred``. count of NudeNet bboxes blurred.
    ``errors``. soft failures (e.g. NudeNet not installed). Never
        raised. always logged + reported.
    """

    privacy_zones_applied: int = 0
    person_faces_blurred: list[uuid.UUID] = field(default_factory=list)
    nudity_regions_blurred: int = 0
    errors: list[str] = field(default_factory=list)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "privacy_zones_applied": self.privacy_zones_applied,
            "person_faces_blurred": [str(pid) for pid in self.person_faces_blurred],
            "nudity_regions_blurred": self.nudity_regions_blurred,
            "errors": list(self.errors),
        }


# Classes from NudeNet that warrant a forced blur. Kept in sync with
# services.ingestion.blur_worker.UNSAFE_NUDITY_CLASSES so the agent
# pipeline matches the recording pipeline.
UNSAFE_NUDITY_CLASSES = {
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}


async def _load_protected_person_ids(db) -> set[uuid.UUID]:
    """Persons flagged for privacy_blur. Returns just their UUIDs.

    The face match step uses FaceRecognizer.match_faces which already
    returns Person UUIDs by looking up via FaceEmbedding; we use this
    set to filter the matches down to only the protected subset.
    """
    try:
        rows = (
            await db.execute(
                select(Person.id).where(Person.privacy_blur.is_(True))
            )
        ).all()
        return {row[0] for row in rows}
    except Exception:
        logger.exception("failed to load protected person ids")
        return set()


def _blur_bbox(frame: np.ndarray, bbox: tuple[int, int, int, int], kernel: int = 61) -> None:
    """In-place gaussian blur of a bbox on ``frame``. Caller must
    already have copied the frame if it wants to preserve the input."""
    import cv2

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(w, int(x2))
    y2 = min(h, int(y2))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    k = kernel if kernel % 2 == 1 else kernel + 1
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return
    frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)


async def redact_frame(
    frame: np.ndarray,
    camera_id: uuid.UUID | str,
    db,
    *,
    current_pose: dict | None = None,
) -> tuple[np.ndarray, RedactionReport]:
    """Apply the mandatory pre-VLM redaction pipeline.

    Always returns a *copy* of the input frame so the caller's array is
    never mutated. The returned frame is the redacted output, the
    report describes what was applied.
    """
    report = RedactionReport()
    if frame is None or getattr(frame, "size", 0) == 0:
        return frame, report

    # Defensive copy. downstream apply_privacy_blur also copies but we
    # want to guarantee invariant even when later steps mutate.
    out = frame.copy()

    # ── 1. Camera privacy zones ──
    try:
        from services.perception.privacy import apply_privacy_blur, get_active_zones

        zones = await get_active_zones(camera_id, current_pose=current_pose)
        if zones:
            out = apply_privacy_blur(out, zones)
            report.privacy_zones_applied = len(zones)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("privacy zone redaction failed")
        report.errors.append(f"privacy_zones:{type(exc).__name__}")

    # ── 2. Per-Person privacy_blur face pass ──
    try:
        protected = await _load_protected_person_ids(db)
        if protected:
            from services.perception.faces import FaceRecognizer

            recognizer = FaceRecognizer()
            faces = await recognizer.detect_and_embed(out)
            if faces:
                matched = await recognizer.match_faces(faces)
                for face in matched:
                    pid_str = face.get("person_id")
                    if not pid_str:
                        continue
                    try:
                        pid = uuid.UUID(pid_str)
                    except (TypeError, ValueError):
                        continue
                    if pid not in protected:
                        continue
                    bbox = face.get("bbox") or []
                    if len(bbox) >= 4:
                        _blur_bbox(out, tuple(int(v) for v in bbox[:4]))
                        report.person_faces_blurred.append(pid)
    except Exception as exc:
        logger.exception("person face redaction failed")
        report.errors.append(f"person_faces:{type(exc).__name__}")

    # ── 3. Nudity safety floor (always-on for agent frames) ──
    try:
        min_score = float(await get_setting("nudity_blur_min_score", 0.5))
    except Exception:
        min_score = 0.5
    try:
        from services.ingestion.blur_worker import _get_nude_detector

        detector = _get_nude_detector()
        if detector is not None:
            try:
                results = detector.detect(out)
            except Exception:
                logger.debug("NudeDetector inference failed", exc_info=True)
                results = []
            for det in results or []:
                cls = det.get("class") or ""
                score = float(det.get("score") or 0.0)
                box = det.get("box") or []
                if cls not in UNSAFE_NUDITY_CLASSES:
                    continue
                if score < min_score:
                    continue
                if len(box) != 4:
                    continue
                bx, by, bw, bh = box
                _blur_bbox(out, (int(bx), int(by), int(bx + bw), int(by + bh)))
                report.nudity_regions_blurred += 1
        else:
            report.errors.append("nudenet:unavailable")
    except Exception as exc:
        logger.exception("nudity redaction failed")
        report.errors.append(f"nudity:{type(exc).__name__}")

    return out, report
