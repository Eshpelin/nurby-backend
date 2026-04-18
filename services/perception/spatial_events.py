"""Loitering and line-cross event detection from tracked bboxes.

Consumes a tracker state + a camera's `motion_zones` config and emits
two event lists per pipeline tick.

- loitering. a track stayed inside a polygon zone (type="loiter" or
  "include" with `loiter_threshold_seconds` set) for at least N seconds.
- line_cross. a track's centroid crossed a zone of `type="tripwire"`,
  whose `points` is a two-point list defining the line. Direction can
  be filtered via `direction` ("in", "out", "any").
"""

from __future__ import annotations

import time
from typing import Iterable

from services.perception.tracker import ObjectTracker, Track, _centroid


def _point_in_polygon(pt: tuple[float, float], poly: list[list[int]]) -> bool:
    """Ray-casting point-in-polygon."""
    x, y = pt
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def _segments_cross(p1, p2, q1, q2) -> bool:
    """True if segment p1-p2 crosses segment q1-q2."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    return ccw(p1, q1, q2) != ccw(p2, q1, q2) and ccw(p1, p2, q1) != ccw(p1, p2, q2)


def _cross_direction(prev_c, cur_c, line_a, line_b) -> str:
    """Sign of the cross product tells which side the point is on.
    Returns "in" if we went from negative side to positive, "out" otherwise.
    Convention is arbitrary but consistent, so a rule can pick its side."""
    def side(pt):
        return (line_b[0] - line_a[0]) * (pt[1] - line_a[1]) - (line_b[1] - line_a[1]) * (pt[0] - line_a[0])
    prev_sign = side(prev_c)
    cur_sign = side(cur_c)
    return "in" if cur_sign > prev_sign else "out"


def evaluate(tracker: ObjectTracker, zones: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """Return (loitering_events, line_cross_events) for this tick."""
    loiter_events: list[dict] = []
    cross_events: list[dict] = []
    if not zones:
        return loiter_events, cross_events

    now = time.monotonic()

    for zone in zones:
        ztype = zone.get("type")
        name = zone.get("name") or "zone"
        pts = zone.get("points") or []

        if ztype in ("loiter", "include") and zone.get("loiter_threshold_seconds"):
            threshold = float(zone["loiter_threshold_seconds"])
            if len(pts) < 3:
                continue
            for tr in list(tracker.tracks.values()):
                c = _centroid(tr.bbox)
                if _point_in_polygon(c, pts):
                    entry = tr.zone_entries.get(name)
                    if entry is None:
                        tr.zone_entries[name] = now
                    elif now - entry >= threshold:
                        loiter_events.append({
                            "zone_name": name,
                            "tracker_id": tr.track_id,
                            "label": tr.label,
                            "duration_seconds": round(now - entry, 2),
                            "threshold_seconds": threshold,
                            "bbox": tr.bbox,
                        })
                        # Reset so the event re-fires per threshold window if still inside.
                        tr.zone_entries[name] = now
                else:
                    tr.zone_entries.pop(name, None)

        elif ztype == "tripwire" and len(pts) >= 2:
            a, b = pts[0], pts[1]
            want_dir = zone.get("direction", "any")
            for tr in list(tracker.tracks.values()):
                if tr.prev_bbox is None:
                    continue
                prev_c = _centroid(tr.prev_bbox)
                cur_c = _centroid(tr.bbox)
                if _segments_cross(prev_c, cur_c, a, b):
                    direction = _cross_direction(prev_c, cur_c, a, b)
                    if want_dir != "any" and want_dir != direction:
                        continue
                    cross_events.append({
                        "zone_name": name,
                        "tracker_id": tr.track_id,
                        "label": tr.label,
                        "direction": direction,
                        "bbox": tr.bbox,
                    })

    return loiter_events, cross_events
