"""Ingestion-side HAR hook (Phase 2-4 integration glue). INTEGRATION-PENDING.

Bridges the live ingestion frame loop to the (tested) HAR core. Per camera it samples the
dense stream at ``har_cadence_fps``, runs pose in the StreamWorker's executor (so it never
blocks the event loop, matching how ``cap.read`` is offloaded), feeds the HARRunner, then
persists finalised segments and broadcasts the live current-activity snapshot.

Hard-gated by ``guardian_har_enabled`` (default OFF) and fully wrapped so that, when off or
on error, it is a pure no-op and cannot affect the existing ingestion path. None of this is
exercised in unit tests; it runs only on a real deployment with a camera. The HAR *logic*
(binding, classifier, state machine, runner) is unit-tested separately.

THE ONE PIECE DELIBERATELY NOT FABRICATED: identity attribution across the ingestion ->
perception boundary. Ingestion's tracker_id space is not the same as perception's, and faces
(person_id) resolve only in perception. Wiring a real ``identity_fn`` requires reconciling
those id spaces via the shared Redis map (perception binds faces to the ingestion track boxes
it receives on the keyframe, then writes ``(camera, ingestion_track_id) -> person_id``). Until
that reconciliation lands, ``identity_fn`` returns None and segments are stored
track-anchored WITHOUT a person, never with a guessed identity. This is the careful part; it
is left honest rather than hallucinated.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("nurby.ingestion.har_hook")

# Per-camera runner/pose/last-run state.
_runners: dict[str, object] = {}
_pose: dict[str, object] = {}
_last_run: dict[str, float] = {}

# Cheap cache of the enabled flag + cadence so we don't hit settings every frame.
_cfg: dict = {"at": 0.0, "enabled": False, "cadence": 8}
_CFG_TTL = 30.0


async def _config() -> tuple[bool, int]:
    now = time.monotonic()
    if now - _cfg["at"] < _CFG_TTL:
        return _cfg["enabled"], _cfg["cadence"]
    try:
        from shared.app_settings import get_setting

        _cfg["enabled"] = bool(await get_setting("guardian_har_enabled", False))
        _cfg["cadence"] = int(await get_setting("har_cadence_fps", 8) or 8)
    except Exception:
        _cfg["enabled"] = False
    _cfg["at"] = now
    return _cfg["enabled"], _cfg["cadence"]


def _identity_fn(camera_id, tracker_id):
    """Stub. Returns None until the ingestion<->perception tracker-id reconciliation lands.
    We never invent identity. See module docstring."""
    return None


async def run_har(camera_id, frame, loop, executor) -> None:
    """Called from the ingestion StreamWorker per decoded frame. No-op unless HAR is enabled
    and the per-camera cadence allows this frame. Safe on any error."""
    try:
        enabled, cadence = await _config()
        if not enabled:
            return
        cam = str(camera_id)
        now = time.monotonic()
        min_gap = 1.0 / max(1, cadence)
        if now - _last_run.get(cam, 0.0) < min_gap:
            return
        _last_run[cam] = now

        pose_est = _pose.get(cam)
        if pose_est is None:
            from services.perception.pose import PoseEstimator

            pose_est = PoseEstimator()
            _pose[cam] = pose_est
        runner = _runners.get(cam)
        if runner is None:
            from services.perception.har_runner import HARRunner

            runner = HARRunner(cam, identity_fn=_identity_fn)
            _runners[cam] = runner

        # Pose off the event loop (GIL released by the model), same pattern as cap.read.
        poses = await loop.run_in_executor(executor, pose_est.infer, frame)
        if poses is None:
            return

        ts = time.time()  # wall-clock epoch so segment bounds convert to datetimes
        segments, live = runner.process_poses(poses, now=ts)

        # Broadcast live current activity (client filters by camera).
        try:
            from services.api.ws import broadcast_person_actions

            await broadcast_person_actions(cam, live)
        except Exception:
            logger.debug("HAR live broadcast failed", exc_info=True)

        # Persist finalised segments, converting epoch bounds to datetimes.
        if segments:
            for s in segments:
                s["started_at"] = _epoch_to_dt(s.get("started_at"))
                s["ended_at"] = _epoch_to_dt(s.get("ended_at"))
            from services.perception.har_runner import persist_segments

            await persist_segments(segments)
    except Exception:
        logger.debug("HAR hook failed (no-op)", exc_info=True)


def _epoch_to_dt(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    return v
