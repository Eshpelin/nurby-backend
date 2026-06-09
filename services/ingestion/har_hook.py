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

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger("nurby.ingestion.har_hook")

# Per-camera runner/pose/last-run state.
_runners: dict[str, object] = {}
_pose: dict[str, object] = {}
_last_run: dict[str, float] = {}

# Process-wide HAR concurrency cap (design 3.1). Pose inference offloads to per-camera
# executors, but without a global bound, N cameras would thundering-herd the CPU. This
# semaphore is what the deployment tiers effectively configure. Sized like the VLM workers:
# min(4, cpu-2), at least 1.
_HAR_MAX = max(1, min(4, (os.cpu_count() or 4) - 2))
_HAR_SEM = asyncio.Semaphore(_HAR_MAX)


def current_tracks(camera_id) -> list[dict]:
    """The HAR runner's current track boxes for a camera, ``[{tracker_id, bbox}]``. Published
    on the keyframe so perception can bind faces to them and write the identity map. Empty
    when HAR has not run for this camera."""
    runner = _runners.get(str(camera_id))
    if runner is None:
        return []
    try:
        tracks = getattr(runner, "_tracker").tracks
        return [{"tracker_id": tr.track_id, "bbox": list(tr.bbox)} for tr in tracks.values()]
    except Exception:
        return []

# Cheap cache of the HAR settings so we don't hit the store every frame.
_cfg: dict = {"at": 0.0, "enabled": False, "cadence": 8, "test_mode": False, "action_set": "all"}
_CFG_TTL = 30.0


async def _config() -> dict:
    now = time.monotonic()
    if now - _cfg["at"] < _CFG_TTL:
        return _cfg
    try:
        from shared.app_settings import get_setting

        _cfg["enabled"] = bool(await get_setting("guardian_har_enabled", False))
        _cfg["cadence"] = int(await get_setting("har_cadence_fps", 8) or 8)
        _cfg["test_mode"] = bool(await get_setting("guardian_har_test_mode", False))
        _cfg["action_set"] = str(await get_setting("har_action_set", "all") or "all")
    except Exception:
        _cfg["enabled"] = False
    _cfg["at"] = now
    return _cfg


async def _write_live_snapshot(get_redis, cam, live) -> None:
    """Publish the current live actions to Redis (short TTL) so perception can ground its VLM
    caption on them (HAR -> VLM fusion). Best-effort."""
    if get_redis is None or not live:
        return
    try:
        import json as _json

        redis = await get_redis()
        if redis is None:
            return
        await redis.set(f"har:live:{cam}", _json.dumps(live), ex=10)
    except Exception:
        logger.debug("HAR live snapshot write failed", exc_info=True)


async def _enrich_identity(get_redis, cam, segments, live) -> None:
    """Attach person identity to segments + live snapshot from the cross-service Redis id map
    (written by perception). Tracks with no binding stay person-less; never guessed."""
    if get_redis is None:
        return
    try:
        from services.perception import har_idmap

        redis = await get_redis()
        if redis is None:
            return
        track_ids = {e.get("track_id") for e in (live or [])} | {
            s.get("track_id") for s in (segments or [])
        }
        track_ids.discard(None)
        idmap = await har_idmap.lookup_many(redis, cam, list(track_ids))
        for e in live or []:
            ident = idmap.get(e.get("track_id"))
            if ident:
                e["person_id"] = ident.get("person_id")
                e["person_name"] = ident.get("person_name")
        for s in segments or []:
            ident = idmap.get(s.get("track_id"))
            if ident:
                s["person_id"] = ident.get("person_id")
                s["person_name"] = ident.get("person_name")
    except Exception:
        logger.debug("HAR identity enrich failed", exc_info=True)


async def run_har(camera_id, frame, loop, executor, get_redis=None) -> None:
    """Called from the ingestion StreamWorker per decoded frame. No-op unless HAR is enabled
    and the per-camera cadence allows this frame. Bounded by a process-wide semaphore. Safe on
    any error. ``get_redis`` is the StreamWorker's async redis getter, used to read the
    cross-service identity map."""
    try:
        cfg = await _config()
        if not cfg["enabled"]:
            return
        cam = str(camera_id)
        now = time.monotonic()
        min_gap = 1.0 / max(1, cfg["cadence"])
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

            runner = HARRunner(cam)  # identity attached post-hoc from the Redis map
            _runners[cam] = runner

        # Global concurrency cap so N cameras cannot thundering-herd the CPU.
        async with _HAR_SEM:
            poses = await loop.run_in_executor(executor, pose_est.infer, frame)
        if poses is None:
            return

        ts = time.time()  # wall-clock epoch so segment bounds convert to datetimes
        segments, live = runner.process_poses(poses, now=ts)

        # Narrow to the deployment's use-case action set (Phase 5 preset).
        from services.perception.har_actions import action_in_set

        aset = cfg.get("action_set", "all")
        live = [e for e in live if action_in_set(e.get("action"), aset)]
        segments = [s for s in segments if action_in_set(s.get("action"), aset)]

        # Attribute identity from the cross-service map.
        await _enrich_identity(get_redis, cam, segments, live)

        # Publish live snapshot (for VLM fusion) + broadcast to the dashboard.
        await _write_live_snapshot(get_redis, cam, live)
        try:
            from services.api.ws import broadcast_person_actions

            await broadcast_person_actions(cam, live)
        except Exception:
            logger.debug("HAR live broadcast failed", exc_info=True)

        # Persist unless in test/dry-run mode (operator validates live before trusting).
        if segments and not cfg.get("test_mode"):
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
