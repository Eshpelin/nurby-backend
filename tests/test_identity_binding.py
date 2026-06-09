"""Tests for HAR identity binding. The careful part: tracker_id -> the right person."""

import uuid

from services.perception.identity_binding import (
    IdentityBinder,
    bind_faces_to_tracks,
)

P1 = str(uuid.uuid4())
P2 = str(uuid.uuid4())
CAM = "cam-1"


def _track(tid, bbox, body=None):
    d = {"tracker_id": tid, "bbox": bbox, "label": "person"}
    if body:
        d["body_cluster_id"] = body
    return d


def _face(pid, bbox, name="Mum", dist=0.4):
    return {"person_id": pid, "person_name": name, "bbox": bbox, "match_distance": dist}


# ── pure single-frame binding ────────────────────────────────────────────────

def test_face_in_track_binds():
    out = bind_faces_to_tracks(
        [_track(1, [100, 100, 200, 400])],
        [_face(P1, [130, 120, 170, 180])],
    )
    assert out[1]["person_id"] == P1
    assert out[1]["person_name"] == "Mum"


def test_unknown_face_does_not_bind():
    out = bind_faces_to_tracks(
        [_track(1, [100, 100, 200, 400])],
        [_face(None, [130, 120, 170, 180])],
    )
    assert out == {}


def test_face_outside_all_tracks_does_not_bind():
    out = bind_faces_to_tracks(
        [_track(1, [100, 100, 200, 400])],
        [_face(P1, [10, 10, 30, 30])],
    )
    assert out == {}


def test_tightest_containing_track_wins():
    # big box 10 contains the face; small box 11 also contains it -> 11 wins
    out = bind_faces_to_tracks(
        [_track(10, [0, 0, 600, 600]), _track(11, [100, 100, 200, 300])],
        [_face(P1, [140, 150, 160, 200])],
    )
    assert 11 in out and 10 not in out
    assert out[11]["person_id"] == P1


def test_closer_match_wins_when_two_faces_same_track():
    out = bind_faces_to_tracks(
        [_track(1, [0, 0, 400, 400])],
        [_face(P1, [10, 10, 30, 30], dist=0.9), _face(P2, [50, 50, 70, 70], dist=0.2)],
    )
    assert out[1]["person_id"] == P2  # the closer (smaller distance) face


def test_track_without_id_skipped():
    out = bind_faces_to_tracks(
        [{"bbox": [0, 0, 400, 400], "label": "person"}],
        [_face(P1, [10, 10, 30, 30])],
    )
    assert out == {}


# ── stateful binder: the three identity states + hold + expiry ───────────────

def test_state_person_body_unknown():
    b = IdentityBinder(ttl=90)
    res = b.update(
        CAM,
        [
            _track(1, [100, 100, 200, 400]),                 # will be person
            _track(2, [400, 100, 500, 400], body="bc-9"),    # body only
            _track(3, [600, 100, 700, 400]),                 # unknown
        ],
        [_face(P1, [130, 120, 170, 180])],
        now=0.0,
    )
    assert res[1]["state"] == "person" and res[1]["person_id"] == P1
    assert res[2]["state"] == "body" and res[2]["body_cluster_id"] == "bc-9"
    assert res[3]["state"] == "unknown" and res[3]["person_id"] is None


def test_binding_held_through_face_occlusion():
    b = IdentityBinder(ttl=90)
    b.update(CAM, [_track(1, [100, 100, 200, 400])], [_face(P1, [130, 120, 170, 180])], now=0.0)
    # next keyframe: same track, NO face (person turned away / eating)
    res = b.update(CAM, [_track(1, [105, 100, 205, 400])], [], now=10.0)
    assert res[1]["state"] == "person" and res[1]["person_id"] == P1


def test_binding_expires_after_ttl_and_no_stale_reuse():
    b = IdentityBinder(ttl=30)
    b.update(CAM, [_track(1, [100, 100, 200, 400])], [_face(P1, [130, 120, 170, 180])], now=0.0)
    # track 1 gone for > ttl; a NEW person reuses tracker_id 1 later
    res = b.update(CAM, [_track(1, [100, 100, 200, 400])], [], now=100.0)
    # the old binding must have expired -> tracker_id 1 is now unknown, not P1
    assert res[1]["state"] == "unknown"
    assert res[1]["person_id"] is None


def test_closer_face_corrects_earlier_binding():
    b = IdentityBinder(ttl=90)
    b.update(CAM, [_track(1, [0, 0, 400, 400])], [_face(P1, [10, 10, 30, 30], dist=0.9)], now=0.0)
    res = b.update(CAM, [_track(1, [0, 0, 400, 400])], [_face(P2, [10, 10, 30, 30], dist=0.2)], now=5.0)
    assert res[1]["person_id"] == P2  # corrected to the more confident match


def test_identity_for_and_reset():
    b = IdentityBinder()
    b.update(CAM, [_track(1, [100, 100, 200, 400])], [_face(P1, [130, 120, 170, 180])], now=0.0)
    assert b.identity_for(CAM, 1)["person_id"] == P1
    assert b.identity_for(CAM, 99) is None
    b.reset(CAM)
    assert b.identity_for(CAM, 1) is None


def test_per_camera_isolation():
    b = IdentityBinder()
    b.update("a", [_track(1, [100, 100, 200, 400])], [_face(P1, [130, 120, 170, 180])], now=0.0)
    res_b = b.update("b", [_track(1, [100, 100, 200, 400])], [], now=0.0)
    # same tracker_id on a different camera must NOT inherit camera a's binding
    assert res_b[1]["state"] == "unknown"
