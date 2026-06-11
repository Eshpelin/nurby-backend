"""Named-zone enter/exit for guardian alerts.

The journey tracker fires entered_zone/left_zone when a person moves between
*cameras*, treating a whole camera as one zone. This adds true sub-camera named
zones: a camera's ``motion_zones`` polygons (the same ones used for loiter and
tripwire rules). When a recognised person's bbox centre crosses into or out of a
named polygon, a guardian entered_zone/left_zone event fires with the polygon's
own name (for example "Playground" or "Nap room"), not the camera name.

State is per (person, camera): the set of zones the person was last inside. The
pure helpers are side-effect free for testing; ``process`` does the matching and
emits via the guardian lifecycle.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (person_id, camera_id) -> set of zone names the person is currently inside.
_state: dict[tuple[str, str], set[str]] = {}


def _point_in_polygon(pt, poly) -> bool:
    """Ray-cast point-in-polygon. ``poly`` is a list of [x, y]."""
    if not poly or len(poly) < 3:
        return False
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def _bbox_center(bbox) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def zones_for_point(center, motion_zones) -> set[str]:
    """Names of the polygon zones that contain ``center``. Only named polygon
    zones are considered (loiter/include/named); tripwire lines are ignored."""
    names: set[str] = set()
    for z in motion_zones or []:
        if not isinstance(z, dict):
            continue
        name = z.get("name")
        pts = z.get("points") or z.get("polygon")
        if not name or not pts or len(pts) < 3:
            continue
        if z.get("type") == "tripwire":
            continue
        if _point_in_polygon(center, pts):
            names.add(str(name))
    return names


def diff_zones(prev: set[str], cur: set[str]) -> tuple[set[str], set[str]]:
    """(entered, left) zone names between two ticks."""
    return cur - prev, prev - cur


def reset_state() -> None:
    _state.clear()


async def process(camera, faces) -> list[dict]:
    """For each recognised face in ``faces`` (needs ``person_id``, ``person_name``,
    ``bbox``), emit guardian entered_zone/left_zone for named polygon crossings on
    this camera. Returns the list of emitted transitions (for tests/telemetry)."""
    motion_zones = getattr(camera, "motion_zones", None)
    if not motion_zones or not faces:
        return []

    emitted: list[dict] = []
    cam_id = str(getattr(camera, "id", ""))
    for f in faces:
        if not isinstance(f, dict):
            continue
        pid = f.get("person_id")
        name = f.get("person_name")
        bbox = f.get("bbox")
        if not pid or not name or not bbox or len(bbox) != 4:
            continue
        key = (str(pid), cam_id)
        cur = zones_for_point(_bbox_center(bbox), motion_zones)
        prev = _state.get(key, set())
        entered, left = diff_zones(prev, cur)
        _state[key] = cur
        for zone_name in sorted(left):
            await _safe_emit("left_zone", name, camera, zone_name)
            emitted.append({"kind": "left_zone", "person": name, "zone": zone_name})
        for zone_name in sorted(entered):
            await _safe_emit("entered_zone", name, camera, zone_name)
            emitted.append({"kind": "entered_zone", "person": name, "zone": zone_name})
    return emitted


async def _safe_emit(kind: str, person_name: str, camera, zone_name: str) -> None:
    from services.guardian.lifecycle import notify_journey_event

    try:
        await notify_journey_event(
            kind, "person", person_name, getattr(camera, "id", None), zone=zone_name
        )
    except Exception:  # noqa: BLE001
        logger.debug("guardian zone emit failed kind=%s zone=%s", kind, zone_name, exc_info=True)
