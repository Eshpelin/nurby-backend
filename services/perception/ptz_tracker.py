"""
Smart Track. Auto-follow detections on PTZ cameras via ONVIF.

Design overview.
- One PTZTrackerManager instance owned by the perception pipeline.
- For each camera with ptz_smart_track_enabled, the pipeline calls
  manager.on_frame(camera, detections, frame_shape) on every keyframe.
- The manager picks the best target detection, computes a ContinuousMove
  velocity that nudges the camera so the target moves toward frame
  center, and dispatches the move asynchronously (fire-and-forget).
- A background sweeper task fires Stop + GotoPreset(home) for cameras
  whose target has been lost for longer than `lost_seconds`.

Why closed-loop on bbox center.
- ONVIF ContinuousMove uses unitless velocity in [-1, 1]. There is no
  reliable way to convert "target is at bbox center 0.7,0.4" into an
  absolute pan/tilt angle without per-camera calibration. Instead we
  treat the bbox center error as a proportional control signal. As the
  camera moves, subsequent frames show a smaller error, and we slow
  down. Classic visual servoing.

Caveats.
- Profile token defaults to "Profile_1" but is now per-camera.
- Move budget caps mechanical wear. Default 30 moves/minute.
- No-go pan/tilt boxes block follow if we have a current pose. Without
  a known pose, no-go is silently ignored.
- Identity-gated tracking. When `ptz_smart_track_require_face` lists
  one or more Person UUIDs, only detections stamped with a matching
  `person_id` are eligible follow targets. The stamp comes from the
  body re-id pass, so the gate works whether the match was via face
  ArcFace or via body cluster confirmation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from services.discovery.onvif import (
    ptz_continuous_move,
    ptz_goto_preset,
    ptz_stop,
)

logger = logging.getLogger("nurby.perception.ptz_tracker")


@dataclass
class _CamState:
    last_target_at: float = 0.0
    last_move_at: float = 0.0
    last_returned_home: bool = True
    last_dx: float = 0.0  # last horizontal error sign (for reacquire pan)
    move_count_window_start: float = 0.0
    move_count: int = 0
    inflight: bool = False
    moving: bool = False  # camera currently in continuous-move state
    last_pose: dict | None = None  # last pose seen at command time


def _bbox_center(bbox: list[int], w: int, h: int) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    cx = ((x1 + x2) / 2.0) / max(1, w)
    cy = ((y1 + y2) / 2.0) / max(1, h)
    return cx, cy


def _bbox_area(bbox: list[int], w: int, h: int) -> float:
    x1, y1, x2, y2 = bbox
    a = max(0, x2 - x1) * max(0, y2 - y1)
    total = max(1, w * h)
    return a / total


def _pick_target(
    detections: list[dict],
    targets: set[str],
    ignore: set[str],
    priority: list[str],
    min_confidence: float,
    frame_w: int,
    frame_h: int,
    allowed_person_ids: set[str] | None = None,
) -> dict | None:
    """Pick the single best detection to follow.

    `allowed_person_ids` enforces identity-gated tracking. When set,
    only detections whose `person_id` is in the set are eligible. The
    `person_id` is stamped by the body re-id pass for both face matches
    and body-only confirmed matches, so the gate works whether or not
    a face is visible this frame.
    """
    candidates = []
    for d in detections:
        label = d.get("label")
        if not label or label in ignore:
            continue
        if targets and label not in targets:
            continue
        if d.get("confidence", 0.0) < min_confidence:
            continue
        if allowed_person_ids:
            pid = d.get("person_id")
            if not pid or pid not in allowed_person_ids:
                continue
        candidates.append(d)
    if not candidates:
        return None

    def rank(det: dict) -> tuple:
        label = det["label"]
        pri = priority.index(label) if label in priority else len(priority)
        area = _bbox_area(det["bbox"], frame_w, frame_h)
        conf = det.get("confidence", 0.0)
        # Lower priority index = better. Larger area = better. Higher conf = better.
        return (pri, -area, -conf)

    candidates.sort(key=rank)
    return candidates[0]


def _within_no_go(pose: dict | None, no_go: list[dict] | None) -> bool:
    if not pose or not no_go:
        return False
    pan = pose.get("pan")
    tilt = pose.get("tilt")
    if pan is None or tilt is None:
        return False
    for box in no_go:
        if (
            pan >= box.get("pan_min", -1.0)
            and pan <= box.get("pan_max", 1.0)
            and tilt >= box.get("tilt_min", -1.0)
            and tilt <= box.get("tilt_max", 1.0)
        ):
            return True
    return False


class PTZTrackerManager:
    """Coordinates Smart Track loops for every PTZ-enabled camera."""

    def __init__(self):
        self._state: dict[str, _CamState] = {}
        self._sweeper_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._cam_refs: dict[str, dict[str, Any]] = {}  # snapshot of latest config

    def start(self) -> None:
        if self._sweeper_task is None or self._sweeper_task.done():
            self._stop.clear()
            self._sweeper_task = asyncio.create_task(self._sweeper_loop())

    async def shutdown(self) -> None:
        self._stop.set()
        if self._sweeper_task:
            try:
                await asyncio.wait_for(self._sweeper_task, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

    def on_frame(
        self,
        camera,
        detections: list[dict],
        frame_shape: tuple[int, int, int],
        current_pose: dict | None = None,
    ) -> None:
        """Entry point called by the perception pipeline.

        Non-blocking. Schedules a move task on the event loop and
        returns immediately so the pipeline does not slow down.
        """
        if not getattr(camera, "ptz_smart_track_enabled", False):
            return
        if getattr(camera, "stream_type", None) != "rtsp":
            return

        cam_id = str(camera.id)
        st = self._state.setdefault(cam_id, _CamState())
        self._cam_refs[cam_id] = self._snapshot(camera)
        self.start()

        h, w = frame_shape[:2]
        targets = set(camera.ptz_smart_track_targets or []) or set()
        ignore = set(camera.ptz_smart_track_ignore or [])
        priority = list(camera.ptz_smart_track_priority or [])
        min_conf = float(camera.ptz_smart_track_min_confidence or 0.45)

        allowed = camera.ptz_smart_track_require_face or []
        allowed_person_ids = (
            {str(p) for p in allowed} if allowed else None
        )
        target = _pick_target(
            detections, targets, ignore, priority, min_conf, w, h,
            allowed_person_ids=allowed_person_ids,
        )

        now = time.monotonic()
        if target is None:
            return  # Sweeper will handle lost-return.

        if _within_no_go(current_pose, camera.ptz_smart_track_no_go or []):
            logger.debug("smart-track skip. pose in no-go camera=%s", cam_id)
            return

        # Mechanical wear budget. Reset window every 60s.
        if now - st.move_count_window_start > 60.0:
            st.move_count_window_start = now
            st.move_count = 0
        budget = int(camera.ptz_smart_track_move_budget_per_minute or 30)
        if st.move_count >= budget:
            return

        cx, cy = _bbox_center(target["bbox"], w, h)
        dx = cx - 0.5
        dy = cy - 0.5
        deadzone = float(camera.ptz_smart_track_deadzone or 0.15)
        max_speed = float(camera.ptz_smart_track_max_speed or 0.5)
        gain = float(camera.ptz_smart_track_gain or 1.5)

        st.last_target_at = now
        st.last_returned_home = False
        st.last_pose = current_pose
        st.last_dx = dx if abs(dx) > 0.05 else st.last_dx

        # Inside deadzone. If we were moving, stop. Else do nothing.
        if abs(dx) < deadzone and abs(dy) < deadzone:
            if st.moving and not st.inflight:
                asyncio.create_task(self._dispatch_stop(camera, st))
            return

        # Compute velocity. Tilt inverted because most ONVIF cameras
        # treat positive tilt as upward (negative pixel y).
        vx = max(-max_speed, min(max_speed, dx * gain))
        vy = max(-max_speed, min(max_speed, -dy * gain))
        vz = 0.0
        if camera.ptz_smart_track_zoom:
            area = _bbox_area(target["bbox"], w, h)
            if area < 0.05:
                vz = 0.2  # slow zoom in on small target
            elif area > 0.35:
                vz = -0.4  # zoom out when target fills frame

        if st.inflight:
            return  # Wait for prior command to finish before issuing another.
        asyncio.create_task(self._dispatch_move(camera, st, vx, vy, vz))

    # ------------------------------------------------------------------
    # Internals

    def _snapshot(self, camera) -> dict[str, Any]:
        """Minimal subset of camera fields needed for background sweeper."""
        return {
            "id": str(camera.id),
            "stream_url": camera.stream_url,
            "username": camera.username,
            "password": camera.password,
            "profile_token": camera.ptz_profile_token or "Profile_1",
            "home_preset": camera.ptz_smart_track_home_preset,
            "lost_seconds": int(camera.ptz_smart_track_lost_seconds or 3),
            "enabled": bool(camera.ptz_smart_track_enabled),
        }

    def _ip_port(self, stream_url: str) -> tuple[str, int]:
        parsed = urlparse(stream_url)
        return parsed.hostname or "", parsed.port or 80

    async def _dispatch_move(
        self, camera, st: _CamState, pan: float, tilt: float, zoom: float,
    ) -> None:
        st.inflight = True
        try:
            ip, port = self._ip_port(camera.stream_url)
            ok = await ptz_continuous_move(
                ip=ip, port=port,
                username=camera.username, password=camera.password,
                profile_token=camera.ptz_profile_token or "Profile_1",
                pan_speed=pan, tilt_speed=tilt, zoom_speed=zoom,
            )
            if ok:
                st.moving = True
                st.last_move_at = time.monotonic()
                st.move_count += 1
            else:
                logger.warning("smart-track move rejected camera=%s", camera.id)
        except Exception:
            logger.exception("smart-track move failed camera=%s", camera.id)
        finally:
            st.inflight = False

    async def _dispatch_stop(self, camera_or_ref, st: _CamState) -> None:
        st.inflight = True
        try:
            if isinstance(camera_or_ref, dict):
                ip, port = self._ip_port(camera_or_ref["stream_url"])
                username = camera_or_ref["username"]
                password = camera_or_ref["password"]
                profile = camera_or_ref["profile_token"]
            else:
                ip, port = self._ip_port(camera_or_ref.stream_url)
                username = camera_or_ref.username
                password = camera_or_ref.password
                profile = camera_or_ref.ptz_profile_token or "Profile_1"
            ok = await ptz_stop(
                ip=ip, port=port,
                username=username, password=password,
                profile_token=profile,
            )
            if ok:
                st.moving = False
        except Exception:
            logger.exception("smart-track stop failed")
        finally:
            st.inflight = False

    async def _dispatch_home(self, ref: dict[str, Any], st: _CamState) -> None:
        st.inflight = True
        try:
            ip, port = self._ip_port(ref["stream_url"])
            home = ref.get("home_preset")
            if home:
                await ptz_goto_preset(
                    ip=ip, port=port,
                    username=ref["username"], password=ref["password"],
                    profile_token=ref["profile_token"],
                    preset_token=home,
                )
            else:
                await ptz_stop(
                    ip=ip, port=port,
                    username=ref["username"], password=ref["password"],
                    profile_token=ref["profile_token"],
                )
            st.moving = False
            st.last_returned_home = True
        except Exception:
            logger.exception("smart-track home failed camera=%s", ref.get("id"))
        finally:
            st.inflight = False

    async def _sweeper_loop(self) -> None:
        """Background sweeper. Returns idle cameras to home preset."""
        while not self._stop.is_set():
            try:
                await asyncio.sleep(1.0)
                now = time.monotonic()
                for cam_id, st in list(self._state.items()):
                    ref = self._cam_refs.get(cam_id)
                    if not ref or not ref.get("enabled"):
                        continue
                    if st.last_returned_home:
                        continue
                    if st.inflight:
                        continue
                    if st.last_target_at == 0.0:
                        continue
                    if now - st.last_target_at < ref["lost_seconds"]:
                        continue
                    # Lost long enough. Send home.
                    asyncio.create_task(self._dispatch_home(ref, st))
            except Exception:
                logger.exception("smart-track sweeper tick failed")


# Module-level singleton for the perception pipeline to import.
manager = PTZTrackerManager()
