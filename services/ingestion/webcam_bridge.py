"""
Webcam bridge. Spawns and supervises ffmpeg processes that read from
local USB video devices and publish them to MediaMTX as RTSP streams.

This lets a USB webcam work the same as an IP camera. the frontend WebRTC
iframe can pull `webcam-{id}` from MediaMTX, and StreamWorker can pull the
same RTSP path without fighting for exclusive access to /dev/videoN.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import uuid
from typing import Optional

from shared.config import settings
from shared.models import Camera

logger = logging.getLogger("nurby.ingestion.webcam_bridge")

# Restart backoff. keep it simple. small fixed delay on crash.
_RESTART_DELAY = 3.0


def _build_input_args(device: str) -> list[str] | None:
    """Pick the right ffmpeg input format for this OS and device spec.

    device may be an integer-like string ("0") or a path ("/dev/video0",
    "video=Integrated Camera"). Returns None if we cannot derive a cmd.
    """
    system = platform.system()
    if system == "Darwin":
        # macOS. avfoundation wants "index" or "index:audio_index".
        idx = device.strip()
        # Accept "/dev/videoN" leftover and coerce to index N.
        if idx.startswith("/dev/video"):
            idx = idx.replace("/dev/video", "")
        return ["-f", "avfoundation", "-framerate", "30", "-i", idx]
    if system == "Linux":
        # v4l2. Accept "/dev/videoN" or bare "N".
        dev = device.strip()
        if dev.isdigit():
            dev = f"/dev/video{dev}"
        return ["-f", "v4l2", "-framerate", "30", "-i", dev]
    if system == "Windows":
        # dshow. device is a friendly name like "Integrated Camera".
        name = device.strip()
        return ["-f", "dshow", "-i", f"video={name}"]
    return None


def bridge_path(camera_id: uuid.UUID | str) -> str:
    """MediaMTX path segment for a camera's webcam bridge."""
    return f"webcam-{camera_id}"


def bridge_rtsp_url(camera_id: uuid.UUID | str) -> str:
    """Full RTSP url a consumer can pull to read the bridged stream."""
    base = settings.mediamtx_rtsp_url.rstrip("/")
    return f"{base}/{bridge_path(camera_id)}"


class _Bridge:
    """Supervisor for a single ffmpeg process."""

    def __init__(self, camera_id: uuid.UUID, device: str):
        self.camera_id = camera_id
        self.device = device
        self._task: Optional[asyncio.Task] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stopped = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        if not shutil.which("ffmpeg"):
            logger.error("ffmpeg not found on PATH. webcam bridge for %s cannot start", self.camera_id)
            return
        input_args = _build_input_args(self.device)
        if input_args is None:
            logger.error("No ffmpeg input mapping for platform=%s", platform.system())
            return

        target = bridge_rtsp_url(self.camera_id)
        while not self._stopped:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "warning",
                *input_args,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",
                "-g", "30",
                "-f", "rtsp",
                "-rtsp_transport", "tcp",
                target,
            ]
            logger.info("Webcam bridge for %s spawning. device=%s target=%s", self.camera_id, self.device, target)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                rc = await self._proc.wait()
                stderr = b""
                if self._proc.stderr is not None:
                    try:
                        stderr = await self._proc.stderr.read()
                    except Exception:
                        pass
                if not self._stopped:
                    logger.warning(
                        "Webcam bridge for %s exited rc=%s. last stderr=%s",
                        self.camera_id, rc, stderr.decode("utf-8", errors="replace")[-500:],
                    )
            except Exception:
                logger.exception("Webcam bridge for %s crashed", self.camera_id)

            if self._stopped:
                break
            await asyncio.sleep(_RESTART_DELAY)

    async def stop(self) -> None:
        self._stopped = True
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._proc.kill()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class WebcamBridgeManager:
    """Tracks one bridge per usb camera id."""

    def __init__(self) -> None:
        self._bridges: dict[uuid.UUID, _Bridge] = {}
        self._lock = asyncio.Lock()

    async def ensure(self, camera: Camera) -> None:
        """Ensure a bridge is running for the given camera if applicable."""
        if camera.stream_type != "usb" or not camera.webcam_device:
            return
        async with self._lock:
            existing = self._bridges.get(camera.id)
            if existing is not None and existing.device == camera.webcam_device:
                existing.start()
                return
            if existing is not None:
                await existing.stop()
            bridge = _Bridge(camera.id, camera.webcam_device)
            self._bridges[camera.id] = bridge
            bridge.start()

    async def stop(self, camera_id: uuid.UUID) -> None:
        async with self._lock:
            bridge = self._bridges.pop(camera_id, None)
        if bridge:
            await bridge.stop()

    async def stop_all(self) -> None:
        async with self._lock:
            items = list(self._bridges.items())
            self._bridges.clear()
        for _, b in items:
            await b.stop()

    async def sync(self, cameras: list[Camera]) -> None:
        """Reconcile running bridges against the desired camera list."""
        desired = {c.id: c for c in cameras if c.stream_type == "usb" and c.webcam_device}
        for cam_id, cam in desired.items():
            await self.ensure(cam)
        stale = set(self._bridges.keys()) - set(desired.keys())
        for cam_id in stale:
            await self.stop(cam_id)


bridge_manager = WebcamBridgeManager()
