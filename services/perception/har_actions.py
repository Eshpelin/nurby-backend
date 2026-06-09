"""Pose-window -> action classification (Phase 3 brain), with a pluggable backend.

Two backends behind one interface so the expensive model can drop in later without
touching callers:

- ``GeometricActionBackend`` (default, runnable here): classifies posture-derived actions
  (standing, sitting, lying_down, walking) from 17-keypoint COCO skeletons using joint
  geometry and motion across the window. It is honest about its reach: actions that geometry
  cannot determine (eating, drinking, sleeping, playing, interacting) return ``unknown`` and
  are left to the VLM/ST-GCN. ``fallen`` is NOT decided here; the existing fall module
  (geometry hold + VLM confirm) owns that, so we never double-decide a critical alert.
- ``STGCNActionBackend`` (adapter seam): wraps PYSKL ST-GCN++ pretrained on NTU-RGB+D. Not
  runnable in this environment (mmcv), so it is a documented stub that raises until weights
  and the runtime are wired on a real deployment. The window format it expects (T x 17 x 3)
  matches what RTMPose/yolo-pose emit, so swapping it in is a backend registration, not a
  rewrite.

Keypoint order is COCO-17: 0 nose, 5/6 shoulders, 11/12 hips, 13/14 knees, 15/16 ankles.
All helpers are pure and tolerant of missing/low-confidence joints, so the classifier is
unit-testable without any model.
"""

from __future__ import annotations

from dataclasses import dataclass

# COCO-17 indices we use.
L_SH, R_SH = 5, 6
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANK, R_ANK = 15, 16

KP_CONF_MIN = 0.3          # ignore joints below this confidence
# Torso angle from vertical (degrees): below = upright, above = horizontal-ish.
UPRIGHT_MAX_DEG = 35.0
LYING_MIN_DEG = 60.0
# Motion of the body centroid across the window, as a fraction of body height, above which
# an upright person is "walking" rather than "standing".
WALK_MOTION_FRAC = 0.6


@dataclass
class PoseFrame:
    """One frame of one tracked person. ``keypoints`` is a list of 17 (x, y, conf)."""
    keypoints: list[tuple[float, float, float]]
    bbox: list[float] | None
    ts: float


def _pt(kps, i):
    if i >= len(kps):
        return None
    x, y, c = kps[i]
    return (x, y) if c is not None and c >= KP_CONF_MIN else None


def _mid(a, b):
    if a and b:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    return a or b


def _angle_from_vertical_deg(top, bottom) -> float | None:
    """Angle of the top->bottom vector away from the vertical axis, 0 = straight up/down."""
    if not top or not bottom:
        return None
    import math

    dx = bottom[0] - top[0]
    dy = bottom[1] - top[1]
    if dx == 0 and dy == 0:
        return None
    # vertical reference is (0, 1) in image coords (y down). angle between.
    ang = math.degrees(math.atan2(abs(dx), abs(dy)))
    return ang


def _frame_posture(frame: PoseFrame) -> str:
    """standing | sitting | lying_down | unknown from one frame's geometry."""
    kps = frame.keypoints
    sh = _mid(_pt(kps, L_SH), _pt(kps, R_SH))
    hip = _mid(_pt(kps, L_HIP), _pt(kps, R_HIP))
    knee = _mid(_pt(kps, L_KNEE), _pt(kps, R_KNEE))
    ank = _mid(_pt(kps, L_ANK), _pt(kps, R_ANK))

    torso = _angle_from_vertical_deg(sh, hip)
    if torso is None:
        return "unknown"

    # Horizontal torso -> lying down.
    if torso >= LYING_MIN_DEG:
        return "lying_down"

    # Upright torso. Distinguish standing vs sitting by leg compression: when sitting, the
    # knees sit close to the hips in height (thigh roughly horizontal) and ankles are near
    # knee height; when standing, hips->knees->ankles descend over a long vertical span.
    if torso <= UPRIGHT_MAX_DEG and hip and knee:
        hip_knee = abs(knee[1] - hip[1])
        sh_hip = abs(hip[1] - sh[1]) if sh else None
        if sh_hip and sh_hip > 1e-3:
            ratio = hip_knee / sh_hip   # thigh length vs torso length, vertical
            if ratio < 0.6:
                return "sitting"
            if ank and abs(ank[1] - knee[1]) > 0.4 * sh_hip:
                return "standing"
            return "standing"
        return "standing"
    return "unknown"


def _centroid(frame: PoseFrame):
    pts = [(_pt(frame.keypoints, i)) for i in (L_SH, R_SH, L_HIP, R_HIP)]
    pts = [p for p in pts if p]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _body_height(frame: PoseFrame) -> float | None:
    sh = _mid(_pt(frame.keypoints, L_SH), _pt(frame.keypoints, R_SH))
    ank = _mid(_pt(frame.keypoints, L_ANK), _pt(frame.keypoints, R_ANK))
    if sh and ank:
        return abs(ank[1] - sh[1])
    if frame.bbox and len(frame.bbox) == 4:
        return abs(frame.bbox[3] - frame.bbox[1])
    return None


class GeometricActionBackend:
    """Runnable default. Posture + motion from keypoints. Honest unknowns."""

    name = "geometric"

    def classify(self, window: list[PoseFrame]) -> tuple[str, float]:
        frames = [f for f in window if f and f.keypoints]
        if not frames:
            return ("unknown", 0.0)

        postures = [_frame_posture(f) for f in frames]
        known = [p for p in postures if p != "unknown"]
        if not known:
            return ("unknown", 0.0)

        # Majority posture over the window.
        from collections import Counter

        posture, votes = Counter(known).most_common(1)[0]
        conf = votes / len(known)

        # Upright + significant centroid motion -> walking.
        if posture == "standing":
            cents = [(_centroid(f), _body_height(f)) for f in frames]
            cents = [(c, h) for c, h in cents if c and h]
            if len(cents) >= 2:
                (c0, h0), (c1, h1) = cents[0], cents[-1]
                h = max(h0 or 0, h1 or 0, 1e-6)
                dist = ((c1[0] - c0[0]) ** 2 + (c1[1] - c0[1]) ** 2) ** 0.5
                if dist / h >= WALK_MOTION_FRAC:
                    return ("walking", min(1.0, conf))
        return (posture, conf)


class STGCNActionBackend:
    """Adapter seam for PYSKL ST-GCN++ (NTU-RGB+D). Window format T x 17 x 3 matches the
    geometric backend's input. Not runnable here (mmcv); wire weights + runtime on a real
    deployment, then register this backend in place of the geometric one."""

    name = "stgcn"

    def __init__(self, model=None, label_map=None):
        self._model = model
        self._label_map = label_map or {}

    def classify(self, window: list[PoseFrame]) -> tuple[str, float]:  # pragma: no cover
        if self._model is None:
            raise NotImplementedError(
                "STGCNActionBackend needs a loaded PYSKL model + NTU->vocab label_map. "
                "Wire on a GPU/real deployment; the geometric backend is the default."
            )
        raise NotImplementedError("ST-GCN inference wiring is deployment-specific.")


def get_backend(name: str = "geometric"):
    if name == "stgcn":
        return STGCNActionBackend()
    return GeometricActionBackend()


# ── Use-case action presets (Phase 5 basics) ────────────────────────────────────────────
# Which actions a deployment surfaces/stores. An operator picks a preset per use case so a
# childcare site is not alerted on "fallen"-style eldercare logic and vice versa. "all" is the
# default. Filtering is applied to live + stored segments by the ingestion hook.
from services.perception.actions import ACTIONS as _VOCAB

ALL_ACTIONS = set(_VOCAB)
ACTION_SETS: dict[str, set[str]] = {
    "all": ALL_ACTIONS,
    "eldercare": {"fallen", "eating", "drinking", "lying_down", "sitting", "standing", "walking", "sleeping"},
    "childcare": {"playing", "interacting", "walking", "sitting", "lying_down", "eating", "sleeping"},
    "security": {"walking", "standing", "fallen", "interacting"},
}


def allowed_actions(preset: str | None) -> set[str]:
    """The action set for a preset name; falls back to all. ``unknown`` is always allowed
    through (it is filtered elsewhere) so presets only narrow the meaningful actions."""
    return ACTION_SETS.get((preset or "all").strip().lower(), ALL_ACTIONS) | {"unknown"}


def action_in_set(action: str | None, preset: str | None) -> bool:
    a = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    return a in allowed_actions(preset)
