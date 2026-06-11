"""Privacy blur post-processor.

When a recording finishes, check whether any face embedding belongs to a
person marked `privacy_blur`. If so, walk the recording with OpenCV, run
face recognition on sampled frames, and re-encode the clip with a heavy
Gaussian blur covering the face + an expanded head/torso region for any
frame where a protected face is found. Blur is propagated across a window
of neighbouring frames so a single miss between samples does not leave a
flash of the real person visible.

This runs in the ingestion service event loop as a background task so a
slow blur pass never blocks live recording. Failures never touch the
original file. the recording stays usable with blur_status='failed'.
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

import numpy as np
from sqlalchemy import select

from shared.app_settings import get_setting
from shared.database import async_session
from shared.models import FaceEmbedding, Person, Recording

logger = logging.getLogger("nurby.ingestion.blur")

# Classes from NudeNet that warrant a forced blur. "COVERED" variants are
# deliberately excluded. blurring clothing is too aggressive for a home
# camera feed. FACE_* is also excluded. that's the job of privacy_blur.
UNSAFE_NUDITY_CLASSES = {
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}

# Cache the detector. loading ONNX once is fine, doing it per recording is not.
_nude_detector = None


def _get_nude_detector():
    global _nude_detector
    if _nude_detector is None:
        try:
            from nudenet import NudeDetector
            _nude_detector = NudeDetector()
            logger.info("NudeDetector loaded for nudity blur pass")
        except Exception:
            logger.exception("Failed to load NudeDetector. nudity blur disabled")
            _nude_detector = False  # sentinel so we do not retry per frame
    return _nude_detector or None


# How densely to probe the clip. 1 means every frame, 5 means every 5th.
FRAME_STRIDE = 3
# Blur a window of N frames on each side of a match, so occasional
# detection misses do not expose the real face.
PROPAGATION_WINDOW = 12
# How much to grow the face bbox to cover head and upper torso. 2.5x vertical,
# 1.8x horizontal, anchored at the face centre but shifted down.
EXPAND_X = 1.8
EXPAND_Y = 2.5
Y_SHIFT_FRAC = 0.6  # shift the expanded box downward so it covers the torso
# Gaussian kernel size for blur. Larger = more private, slower.
BLUR_KERNEL = 61
# Face-recognition distance threshold. Matches FaceRecognizer.MATCH_THRESHOLD.
MATCH_THRESHOLD = 0.6


async def _load_protected_embeddings() -> list[tuple[uuid.UUID, str, np.ndarray]]:
    """Embeddings for persons with privacy_blur enabled."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(FaceEmbedding.person_id, Person.display_name, FaceEmbedding.embedding)
                .join(Person, FaceEmbedding.person_id == Person.id)
                .where(Person.privacy_blur.is_(True))
            )
            return [(row[0], row[1], np.array(row[2])) for row in result.all()]
    except Exception:
        logger.exception("Failed to load protected embeddings")
        return []


async def _update_status(recording_id: uuid.UUID, status: str, error: Optional[str] = None, new_path: Optional[str] = None):
    try:
        async with async_session() as db:
            rec = await db.get(Recording, recording_id)
            if not rec:
                return
            rec.blur_status = status
            rec.blur_error = (error[:500] if error else None)
            if new_path:
                rec.file_path = new_path
            await db.commit()
    except Exception:
        logger.exception("Failed to update blur status for %s", recording_id)


def _expand_bbox(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Grow a face bbox to roughly cover head + upper torso."""
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    bw = (x2 - x1) * EXPAND_X
    bh = (y2 - y1) * EXPAND_Y
    cy = cy + (y2 - y1) * Y_SHIFT_FRAC  # drop centre to cover torso
    nx1 = max(0, int(cx - bw / 2))
    ny1 = max(0, int(cy - bh / 2))
    nx2 = min(w, int(cx + bw / 2))
    ny2 = min(h, int(cy + bh / 2))
    return nx1, ny1, nx2, ny2


def _process_sync(
    src_path: str,
    dst_path: str,
    protected_embs: list[np.ndarray],
    nudity_blur_enabled: bool,
    nudity_min_score: float,
) -> tuple[bool, str | None, bool]:
    """Run the blur pass synchronously. Returns (ok, error, any_matches).

    Kept sync so it can be dispatched to a thread. OpenCV, face_recognition
    and NudeDetector all release the GIL for their heavy work.
    """
    import cv2
    face_lib = None
    if protected_embs:
        try:
            import face_recognition  # noqa. F401
            face_lib = face_recognition
        except ImportError:
            return False, "face_recognition not installed", False

    nude_detector = _get_nude_detector() if nudity_blur_enabled else None

    cap = cv2.VideoCapture(src_path)
    if not cap.isOpened():
        return False, "cannot open source video", False

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # First pass. sample frames, detect matches, record bbox timeline per frame index.
    # Stored as list of (left, top, right, bottom) per sampled frame, then
    # propagated to a window of neighbours.
    matches_per_frame: dict[int, list[tuple[int, int, int, int]]] = {}
    any_matches = False

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % FRAME_STRIDE == 0:
            rgb = frame[:, :, ::-1]

            # Protected face pass.
            if face_lib is not None:
                locs = face_lib.face_locations(rgb, model="hog")
                if locs:
                    encs = face_lib.face_encodings(rgb, locs)
                    for (top, right, bottom, left), enc in zip(locs, encs):
                        for p_emb in protected_embs:
                            dist = float(np.linalg.norm(np.array(enc) - p_emb))
                            if dist < MATCH_THRESHOLD:
                                x1, y1, x2, y2 = _expand_bbox(left, top, right, bottom, w, h)
                                matches_per_frame.setdefault(frame_idx, []).append((x1, y1, x2, y2))
                                any_matches = True
                                break

            # Nudity pass. NudeNet returns class/score/box[x,y,w,h].
            if nude_detector is not None:
                try:
                    results = nude_detector.detect(frame)
                    for det in results or []:
                        cls = det.get("class") or ""
                        score = float(det.get("score") or 0.0)
                        box = det.get("box") or []
                        if cls not in UNSAFE_NUDITY_CLASSES:
                            continue
                        if score < nudity_min_score:
                            continue
                        if len(box) != 4:
                            continue
                        bx, by, bw, bh = box
                        x1 = max(0, int(bx))
                        y1 = max(0, int(by))
                        x2 = min(w, int(bx + bw))
                        y2 = min(h, int(by + bh))
                        if x2 <= x1 or y2 <= y1:
                            continue
                        matches_per_frame.setdefault(frame_idx, []).append((x1, y1, x2, y2))
                        any_matches = True
                except Exception:
                    logger.exception("NudeDetector frame %d failed", frame_idx)

        frame_idx += 1

    cap.release()

    if not any_matches:
        return True, None, False

    # Propagate each match forward and backward to cover gaps between samples.
    propagated: dict[int, list[tuple[int, int, int, int]]] = {}
    for fi, boxes in matches_per_frame.items():
        for delta in range(-PROPAGATION_WINDOW, PROPAGATION_WINDOW + 1):
            target = fi + delta
            if 0 <= target < total:
                propagated.setdefault(target, []).extend(boxes)

    # Second pass. apply blur and write output.
    cap = cv2.VideoCapture(src_path)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(dst_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        cap.release()
        return False, "cannot open writer", True

    kernel = BLUR_KERNEL if BLUR_KERNEL % 2 == 1 else BLUR_KERNEL + 1
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        boxes = propagated.get(idx)
        if boxes:
            for (x1, y1, x2, y2) in boxes:
                if x2 <= x1 or y2 <= y1:
                    continue
                roi = frame[y1:y2, x1:x2]
                if roi.size == 0:
                    continue
                blurred = cv2.GaussianBlur(roi, (kernel, kernel), 0)
                frame[y1:y2, x1:x2] = blurred
        writer.write(frame)
        idx += 1

    cap.release()
    writer.release()
    return True, None, True


async def process_recording(recording_id: uuid.UUID):
    """Entry point. Decide whether to blur and dispatch the work."""
    protected = await _load_protected_embeddings()
    nudity_blur_enabled = bool(await get_setting("nudity_blur", True))
    nudity_min_score = float(await get_setting("nudity_blur_min_score", 0.5))

    # Short-circuit only if nothing at all to check.
    if not protected and not nudity_blur_enabled:
        await _update_status(recording_id, "skipped")
        return

    async with async_session() as db:
        rec = await db.get(Recording, recording_id)
        if not rec:
            return
        src_path = rec.file_path

    if not src_path or not os.path.exists(src_path):
        await _update_status(recording_id, "failed", "source file missing")
        return

    await _update_status(recording_id, "processing")

    base, ext = os.path.splitext(src_path)
    tmp_path = f"{base}.blurring{ext or '.mp4'}"
    final_path = f"{base}.blurred{ext or '.mp4'}"

    try:
        embs = [e for (_, _, e) in protected]
        loop = asyncio.get_event_loop()
        ok, err, any_matches = await loop.run_in_executor(
            None, _process_sync, src_path, tmp_path, embs,
            nudity_blur_enabled, nudity_min_score,
        )

        if not ok:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except OSError: pass
            await _update_status(recording_id, "failed", err or "unknown blur failure")
            return

        if not any_matches:
            # No protected person ever appeared. Keep original, nothing to do.
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except OSError: pass
            await _update_status(recording_id, "skipped")
            logger.info("Blur skipped for %s. no protected faces found", recording_id)
            return

        # Swap original for blurred version. Original is removed so the
        # unblurred footage never lingers on disk.
        try:
            os.replace(tmp_path, final_path)
        except OSError as exc:
            await _update_status(recording_id, "failed", f"rename failed. {exc}")
            return

        try:
            os.remove(src_path)
        except OSError:
            logger.warning("Could not remove original %s", src_path)

        await _update_status(recording_id, "done", new_path=final_path)
        logger.info("Blurred recording %s saved to %s", recording_id, final_path)

    except Exception as exc:
        logger.exception("Blur worker failed for %s", recording_id)
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except OSError: pass
        await _update_status(recording_id, "failed", str(exc))


def schedule(recording_id: uuid.UUID):
    """Fire-and-forget entry from the stream worker. Swallows scheduler errors."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(process_recording(recording_id))
    except RuntimeError:
        # No running loop. Fall back to a fresh one in a worker thread.
        import threading
        def _runner():
            asyncio.run(process_recording(recording_id))
        threading.Thread(target=_runner, daemon=True).start()
