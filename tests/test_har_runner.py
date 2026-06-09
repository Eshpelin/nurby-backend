"""Orchestration test for HARRunner: poses -> track -> classify -> segments + identity.

Drives the runner with scripted skeletons and an injected identity function (no real model,
no DB, no Redis), proving the glue produces identity-attributed action segments and a live
snapshot. The live model / DB / WS wiring is integration-pending and exercised on a real
deployment.
"""

import uuid

from services.perception.har_runner import HARRunner

PID = str(uuid.uuid4())
BBOX = [280, 90, 330, 410]  # constant so the IoU tracker keeps one track id


def _kps(joints):
    out = [(0.0, 0.0, 0.0)] * 17
    for i, (x, y) in joints.items():
        out[i] = (float(x), float(y), 1.0)
    return out


def _standing():
    return _kps({5: (290, 100), 6: (310, 100), 11: (290, 200), 12: (310, 200),
                 13: (290, 300), 14: (310, 300), 15: (290, 400), 16: (310, 400)})


def _lying():
    return _kps({5: (100, 200), 6: (110, 205), 11: (300, 205), 12: (310, 210),
                 13: (400, 208), 14: (410, 212)})


def _pose(kps):
    return {"bbox": BBOX, "keypoints": kps}


def test_runner_emits_identity_attributed_segment():
    ident = {"person_id": PID, "person_name": "Mum"}
    runner = HARRunner("cam-1", min_frames=3, window=5,
                       identity_fn=lambda c, t: ident)

    all_segments = []
    # 5 frames standing, then 5 frames lying -> a standing segment closes on the transition
    for t in range(5):
        segs, live = runner.process_poses([_pose(_standing())], now=float(t))
        all_segments += segs
        assert live and live[0]["action"] in ("standing", "unknown")
    for t in range(5, 11):
        segs, live = runner.process_poses([_pose(_lying())], now=float(t))
        all_segments += segs

    standing = [s for s in all_segments if s["action"] == "standing"]
    assert standing, f"expected a standing segment, got {[s['action'] for s in all_segments]}"
    seg = standing[0]
    assert seg["person_id"] == PID and seg["person_name"] == "Mum"
    assert seg["camera_id"] == "cam-1"
    # min_frames=3, so the first 2 frames are 'unknown' (window not deep enough) and the
    # standing run correctly starts at frame 2, not 0. Warmup is expected, not a bug.
    assert seg["started_at"] == 2.0


def test_runner_no_identity_leaves_person_none():
    runner = HARRunner("cam-2", min_frames=3, window=5, identity_fn=lambda c, t: None)
    segs_all = []
    for t in range(5):
        segs_all += runner.process_poses([_pose(_standing())], now=float(t))[0]
    for t in range(5, 11):
        segs_all += runner.process_poses([_pose(_lying())], now=float(t))[0]
    # unknown-identity tracks still produce segments, but with no person attached
    assert any(s["person_id"] is None for s in segs_all)


def test_runner_flush_closes_on_track_loss():
    runner = HARRunner("cam-3", min_frames=3, window=5, identity_fn=lambda c, t: {"person_id": PID})
    for t in range(5):
        runner.process_poses([_pose(_standing())], now=float(t))
    # person leaves frame (no poses) for long enough that the tracker drops the track
    closed = []
    for t in range(5, 25):
        closed += runner.process_poses([], now=float(t))[0]
    assert any(s["action"] == "standing" and s["person_id"] == PID for s in closed)
