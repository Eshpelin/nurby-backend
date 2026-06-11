"""Bind a tracker_id to a person identity, the careful part of HAR.

Everything downstream of tracking (pose windows, the action model, VLM fusion, the
state machine) is only as good as the answer to one question: *which real person is
this track?* If a track is bound to the wrong person, every action we record for them
is wrong. So this module is deliberately small, conservative, and heavily tested.

Inputs are what the perception pipeline already has on a keyframe:
- person tracks: the YOLO person detections after `ObjectTracker.update`, each carrying a
  stable ``tracker_id`` and a ``bbox`` (and, after body re-id, an optional
  ``body_cluster_id``).
- faces: the matched faces, each carrying ``person_id`` / ``person_name`` (when the face
  matched an enrolled, consented Person) and a ``bbox``.

A track gets a ``person_id`` when a recognised face's centre sits inside that track's box
(tightest box wins under overlap). The binding is then **held for the life of the track**,
so it survives the face being occluded (e.g. while eating or turned away), which is the
v1 weakness this fixes. Identity has three honest states, never invented:
- ``person_id`` bound (a recognised, consented person),
- ``body_cluster_id`` only (a re-identified body with no confirmed Person),
- neither (an unknown, transient person).

Guardian-facing surfaces must only show actions for the ``person_id`` state; the other two
are stored without identity or dropped, never shown to a family.

The module is pure and side-effect free (state is an in-memory dict keyed by camera) so the
binding contract is unit-testable without a pipeline, a tracker, or a database. The same
logic serves keyframe binding in perception and, later, dense-track binding in ingestion via
a shared Redis map; only the source of the track boxes differs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# A binding is dropped this many seconds after its track was last seen. Keyframes are
# sparse (seconds apart) and a person can be briefly occluded or leave and return, so this
# is generous on purpose. Tune per deployment; injectable for tests.
DEFAULT_TTL_SECONDS = 90.0


@dataclass
class Binding:
    person_id: str
    person_name: str | None
    last_seen: float
    # match_distance of the face that created/last-refreshed this binding, lower is better.
    # Lets a closer face correct an earlier weaker bind.
    match_distance: float


def _center(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _inside(pt, box) -> bool:
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _valid_box(b) -> bool:
    return bool(b) and len(b) == 4


def bind_faces_to_tracks(person_tracks, faces) -> dict[int, dict]:
    """Pure, stateless single-frame binding. Return ``{tracker_id: {person_id,
    person_name, match_distance}}`` for the tracks a recognised face landed inside.

    A face binds to the **tightest** track box whose region contains the face centre, so
    when a small person box overlaps a large one, the face attributes to the small (closer)
    one. Faces without a ``person_id`` (unknown / unconsented) never bind. Tracks without a
    ``tracker_id`` are skipped."""
    tracks = [
        t
        for t in (person_tracks or [])
        if t.get("tracker_id") is not None and _valid_box(t.get("bbox"))
    ]
    out: dict[int, dict] = {}
    for f in faces or []:
        pid = f.get("person_id")
        fb = f.get("bbox")
        if not pid or not _valid_box(fb):
            continue
        fc = _center(fb)
        containing = [t for t in tracks if _inside(fc, t["bbox"])]
        if not containing:
            continue
        winner = min(containing, key=lambda t: _area(t["bbox"]))
        tid = int(winner["tracker_id"])
        dist = f.get("match_distance")
        dist = float(dist) if isinstance(dist, (int, float)) else 1.0
        # If two faces fall in the same track this frame, keep the closer match.
        prev = out.get(tid)
        if prev is None or dist < prev["match_distance"]:
            out[tid] = {
                "person_id": str(pid),
                "person_name": (str(f["person_name"]) if f.get("person_name") else None),
                "match_distance": dist,
            }
    return out


class IdentityBinder:
    """Stateful per-camera binder that holds bindings across keyframes.

    Call ``update`` once per processed keyframe with that camera's tracked person
    detections and matched faces. Query ``identity_for`` to get the held identity of a
    track even on frames where its face is not visible. Bindings expire ``ttl`` seconds
    after a track was last seen, so a reused tracker_id cannot inherit a stale person.
    """

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS):
        self.ttl = ttl
        # camera_id -> {tracker_id -> Binding}
        self._state: dict[str, dict[int, Binding]] = {}

    def update(self, camera_id, person_tracks, faces, *, now: float | None = None) -> dict[int, dict]:
        """Fold this keyframe's face hits into the held bindings and expire stale ones.
        Returns the current ``{tracker_id: identity}`` for tracks present this frame, where
        identity is ``{person_id, person_name, body_cluster_id, state}`` and ``state`` is one
        of ``person`` | ``body`` | ``unknown``."""
        now = time.monotonic() if now is None else now
        cam = str(camera_id)
        held = self._state.setdefault(cam, {})

        present_ids: set[int] = set()
        present_track_box: dict[int, list] = {}
        present_body: dict[int, str] = {}
        for t in person_tracks or []:
            tid = t.get("tracker_id")
            if tid is None or not _valid_box(t.get("bbox")):
                continue
            tid = int(tid)
            present_ids.add(tid)
            present_track_box[tid] = t["bbox"]
            if t.get("body_cluster_id"):
                present_body[tid] = str(t["body_cluster_id"])

        # 1. Apply this frame's face->track bindings (closer match can overwrite).
        fresh = bind_faces_to_tracks(person_tracks, faces)
        for tid, info in fresh.items():
            prev = held.get(tid)
            if prev is None or info["match_distance"] <= prev.match_distance:
                held[tid] = Binding(
                    person_id=info["person_id"],
                    person_name=info["person_name"],
                    last_seen=now,
                    match_distance=info["match_distance"],
                )

        # 2. Expire stale bindings FIRST, using last_seen from prior frames. A track absent
        #    longer than ttl loses its identity, so a reappearing or reused tracker_id never
        #    inherits a stale person. Face hits in step 1 already refreshed last_seen, so a
        #    genuinely-present person is never wrongly expired.
        for tid in list(held):
            if now - held[tid].last_seen > self.ttl:
                del held[tid]

        # 3. Refresh last_seen for held tracks still present this frame. Continuous presence
        #    holds the binding through face occlusion (the v1 weakness this fixes).
        for tid in present_ids:
            if tid in held:
                held[tid].last_seen = now

        # 4. Build the per-track identity view for tracks present this frame.
        result: dict[int, dict] = {}
        for tid in present_ids:
            b = held.get(tid)
            if b is not None:
                result[tid] = {
                    "person_id": b.person_id,
                    "person_name": b.person_name,
                    "body_cluster_id": present_body.get(tid),
                    "state": "person",
                }
            elif tid in present_body:
                result[tid] = {
                    "person_id": None,
                    "person_name": None,
                    "body_cluster_id": present_body[tid],
                    "state": "body",
                }
            else:
                result[tid] = {
                    "person_id": None,
                    "person_name": None,
                    "body_cluster_id": None,
                    "state": "unknown",
                }
        return result

    def identity_for(self, camera_id, tracker_id) -> dict | None:
        """The held identity for a track, or None if unbound. Does not expire here; call
        ``update`` to drive expiry."""
        b = self._state.get(str(camera_id), {}).get(int(tracker_id))
        if b is None:
            return None
        return {"person_id": b.person_id, "person_name": b.person_name, "state": "person"}

    def reset(self, camera_id=None) -> None:
        if camera_id is None:
            self._state.clear()
        else:
            self._state.pop(str(camera_id), None)
