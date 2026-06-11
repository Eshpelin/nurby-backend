"""Lightweight centroid/IoU tracker for object detections.

No deep SORT, no Kalman filter. Matches each incoming detection to the
nearest previously-tracked detection by IoU, assigns stable integer IDs,
and expires tracks that are not seen for `max_missed` frames.

Enough to power loitering and line-cross triggers without the cost of a
full MOT stack. Per-camera state lives inside the pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

IOU_MATCH_THRESHOLD = 0.2  # below this, detections are considered new tracks
MAX_MISSED = 15             # expire track after N keyframes without a hit


def _iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _centroid(b: list[int]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


@dataclass
class Track:
    track_id: int
    label: str
    bbox: list[int]
    prev_bbox: list[int] | None
    first_seen: float
    last_seen: float
    missed: int = 0
    # Per-zone entry timestamps. {zone_name: monotonic_seconds}
    zone_entries: dict[str, float] = field(default_factory=dict)


class ObjectTracker:
    def __init__(self, label_filter: tuple[str, ...] = ("person", "car", "truck", "dog", "cat")):
        self._tracks: dict[int, Track] = {}
        self._next_id = 1
        self._label_filter = label_filter

    @property
    def tracks(self) -> dict[int, Track]:
        return self._tracks

    def update(self, detections: list[dict]) -> list[dict]:
        """Match detections to tracks in-place. Returns same list with
        `tracker_id` added to each dict. Detections whose label is not in
        `label_filter` are still returned but without a tracker_id."""
        now = time.monotonic()

        # Prepare candidates to track (skip uninteresting labels)
        trackable = [d for d in detections if d.get("label") in self._label_filter]
        untouched_ids = set(self._tracks.keys())

        # Greedy IoU match. for each detection pick best available track.
        for det in trackable:
            best_id = -1
            best_iou = IOU_MATCH_THRESHOLD
            for tid in untouched_ids:
                tr = self._tracks[tid]
                if tr.label != det["label"]:
                    continue
                v = _iou(det["bbox"], tr.bbox)
                if v > best_iou:
                    best_iou = v
                    best_id = tid
            if best_id != -1:
                tr = self._tracks[best_id]
                tr.prev_bbox = tr.bbox
                tr.bbox = list(det["bbox"])
                tr.last_seen = now
                tr.missed = 0
                det["tracker_id"] = best_id
                untouched_ids.discard(best_id)
            else:
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = Track(
                    track_id=tid,
                    label=det["label"],
                    bbox=list(det["bbox"]),
                    prev_bbox=None,
                    first_seen=now,
                    last_seen=now,
                )
                det["tracker_id"] = tid

        # Age out tracks not matched this tick.
        expired = []
        for tid in untouched_ids:
            tr = self._tracks[tid]
            tr.missed += 1
            if tr.missed > MAX_MISSED:
                expired.append(tid)
        for tid in expired:
            self._tracks.pop(tid, None)

        return detections
