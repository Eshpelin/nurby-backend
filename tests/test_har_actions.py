"""Tests for the pose-geometry action classifier and the action state machine."""

from services.perception.har_actions import (
    GeometricActionBackend,
    PoseFrame,
    STGCNActionBackend,
)
from services.perception.har_state import ActionStateMachine


def _kps(joints: dict):
    """Build a 17-keypoint list; joints maps index -> (x, y), conf 1.0; others conf 0."""
    out = [(0.0, 0.0, 0.0)] * 17
    for i, (x, y) in joints.items():
        out[i] = (float(x), float(y), 1.0)
    return out


# COCO indices: 5/6 shoulders, 11/12 hips, 13/14 knees, 15/16 ankles
def _standing(x=300.0):
    return _kps({5: (x - 20, 100), 6: (x + 20, 100), 11: (x - 15, 200), 12: (x + 15, 200),
                 13: (x - 15, 300), 14: (x + 15, 300), 15: (x - 15, 400), 16: (x + 15, 400)})


def _sitting():
    # knees near hip height (thigh horizontal) -> compressed
    return _kps({5: (290, 100), 6: (310, 100), 11: (290, 200), 12: (310, 200),
                 13: (285, 212), 14: (315, 212), 15: (285, 300), 16: (315, 300)})


def _lying():
    # torso horizontal: shoulders left, hips right, ~same y
    return _kps({5: (100, 200), 6: (110, 205), 11: (300, 205), 12: (310, 210),
                 13: (400, 208), 14: (410, 212)})


def _frame(kps, ts=0.0):
    return PoseFrame(keypoints=kps, bbox=[280, 90, 330, 410], ts=ts)


def test_standing():
    b = GeometricActionBackend()
    act, conf = b.classify([_frame(_standing(), t) for t in range(5)])
    assert act == "standing" and conf > 0.5


def test_sitting():
    b = GeometricActionBackend()
    act, _ = b.classify([_frame(_sitting(), t) for t in range(5)])
    assert act == "sitting"


def test_lying_down():
    b = GeometricActionBackend()
    act, _ = b.classify([_frame(_lying(), t) for t in range(5)])
    assert act == "lying_down"


def test_walking_from_motion():
    b = GeometricActionBackend()
    # standing posture but centroid sweeps across > 0.6 * body height (300) over the window
    frames = [_frame(_standing(x=200 + i * 60), ts=float(i)) for i in range(5)]
    act, _ = b.classify(frames)
    assert act == "walking"


def test_geometry_does_not_fabricate_eating():
    # a seated person is 'sitting', never 'eating' (geometry can't know) -> honest
    b = GeometricActionBackend()
    act, _ = b.classify([_frame(_sitting(), t) for t in range(5)])
    assert act in ("sitting", "unknown")
    assert act != "eating"


def test_empty_or_lowconf_unknown():
    b = GeometricActionBackend()
    assert b.classify([])[0] == "unknown"
    assert b.classify([_frame([(0.0, 0.0, 0.0)] * 17)])[0] == "unknown"


def test_stgcn_backend_is_an_unwired_stub():
    import pytest

    with pytest.raises(NotImplementedError):
        STGCNActionBackend().classify([_frame(_standing())])


# ── state machine ────────────────────────────────────────────────────────────

def test_stable_action_no_emit_until_transition():
    sm = ActionStateMachine(window=3, min_dwell=1.0)
    out = []
    for t in range(5):
        out += sm.observe("c", 1, "standing", 0.9, float(t))
    assert out == []  # one continuous run, nothing finalised yet
    # transition to walking, held past min_dwell, then flush
    for t in range(5, 10):
        out += sm.observe("c", 1, "walking", 0.8, float(t))
    assert any(s["action"] == "standing" for s in out)  # the standing run got closed
    seg = [s for s in out if s["action"] == "standing"][0]
    # smoothing (window=3) lags the transition by ~1 frame; standing closes at 6.0 not 5.0
    assert seg["started_at"] == 0.0 and seg["ended_at"] == 6.0


def test_single_frame_flicker_absorbed():
    sm = ActionStateMachine(window=5, min_dwell=1.0)
    out = []
    seq = ["standing"] * 4 + ["sitting"] + ["standing"] * 4   # one-frame blip
    for t, a in enumerate(seq):
        out += sm.observe("c", 1, a, 0.9, float(t))
    # smoothing absorbs the single 'sitting' frame -> no sitting segment emitted
    assert not any(s["action"] == "sitting" for s in out)


def test_flush_closes_open_segment():
    sm = ActionStateMachine(window=3, min_dwell=1.0)
    for t in range(5):
        sm.observe("c", 1, "eating", 0.9, float(t))
    closed = sm.flush("c", 1, now=5.0)
    assert len(closed) == 1 and closed[0]["action"] == "eating"
    assert closed[0]["confidence_avg"] == 0.9


def test_too_short_segment_not_emitted_on_flush():
    sm = ActionStateMachine(window=3, min_dwell=5.0)
    sm.observe("c", 1, "walking", 0.9, 0.0)
    assert sm.flush("c", 1, now=1.0) == []   # 1s < 5s min dwell
