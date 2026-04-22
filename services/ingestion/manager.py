"""
Camera manager. Watches the database for camera configs and spawns/stops
stream workers accordingly. Detects stream config changes and restarts
workers when connection parameters change.
"""

import asyncio
import hashlib
import logging
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select

from shared.config import settings
from shared.database import async_session
from shared.models import Camera
from services.ingestion.audio_worker import AudioWorker, set_main_loop as set_audio_main_loop
from services.ingestion.stream import StreamWorker
from services.ingestion.webcam_bridge import bridge_manager

logger = logging.getLogger("nurby.ingestion.manager")

POLL_INTERVAL = 10  # seconds between DB polls for camera changes
RESTART_KEY_PREFIX = "nurby:stream_restart:"


def _stream_config_hash(cam: Camera) -> str:
    """Hash stream-affecting fields to detect config changes."""
    parts = [
        cam.stream_url or "",
        cam.stream_type or "rtsp",
        cam.username or "",
        cam.password or "",
        cam.auth_token or "",
        str(cam.snapshot_interval or 2.0),
        getattr(cam, "webcam_device", "") or "",
    ]
    return hashlib.md5("|".join(parts).encode()).hexdigest()


class CameraManager:
    def __init__(self):
        self._workers: dict[uuid.UUID, StreamWorker] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._audio_workers: dict[uuid.UUID, AudioWorker] = {}
        self._audio_tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._config_hashes: dict[uuid.UUID, str] = {}
        self._redis = None
        # Audio worker threads use run_coroutine_threadsafe. register the loop.
        try:
            set_audio_main_loop(asyncio.get_event_loop())
        except RuntimeError:
            pass

    async def _get_redis(self):
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def run(self):
        """Main loop. Polls DB for cameras and manages workers."""
        while True:
            try:
                await self._sync_cameras()
            except Exception:
                logger.exception("Error syncing cameras")
            await asyncio.sleep(POLL_INTERVAL)

    def _create_worker(self, cam_id: uuid.UUID, cam: Camera):
        """Create and start a StreamWorker for a camera."""
        worker = StreamWorker(
            camera_id=cam_id,
            stream_url=cam.stream_url,
            recording_enabled=cam.recording_enabled,
            recording_mode=getattr(cam, "recording_mode", "always"),
            recording_trigger_objects=getattr(cam, "recording_trigger_objects", None),
            recording_clip_pre=getattr(cam, "recording_clip_pre", 5),
            recording_clip_post=getattr(cam, "recording_clip_post", 10),
            stream_type=getattr(cam, "stream_type", "rtsp"),
            username=getattr(cam, "username", None),
            password=getattr(cam, "password", None),
            auth_token=getattr(cam, "auth_token", None),
            snapshot_interval=getattr(cam, "snapshot_interval", 2.0),
            webcam_device=getattr(cam, "webcam_device", None),
        )
        self._workers[cam_id] = worker
        self._tasks[cam_id] = asyncio.create_task(worker.run())
        self._config_hashes[cam_id] = _stream_config_hash(cam)

        # Audio listener. Only RTSP/HLS streams are likely to carry audio.
        if cam.stream_type in ("rtsp", "hls") and cam.stream_url:
            from services.ingestion.stream import build_auth_url
            authed = build_auth_url(cam.stream_url, cam.username, cam.password)
            aw = AudioWorker(cam_id, authed)
            self._audio_workers[cam_id] = aw
            self._audio_tasks[cam_id] = asyncio.create_task(aw.run())

    def _stop_worker(self, cam_id: uuid.UUID):
        """Stop and clean up a stream worker."""
        if cam_id in self._workers:
            self._workers[cam_id].stop()
        if cam_id in self._tasks:
            self._tasks[cam_id].cancel()
        self._workers.pop(cam_id, None)
        self._tasks.pop(cam_id, None)
        self._config_hashes.pop(cam_id, None)
        if cam_id in self._audio_workers:
            self._audio_workers[cam_id].stop()
        if cam_id in self._audio_tasks:
            self._audio_tasks[cam_id].cancel()
        self._audio_workers.pop(cam_id, None)
        self._audio_tasks.pop(cam_id, None)

    async def _check_restart_signal(self, cam_id: uuid.UUID) -> bool:
        """Check if a restart was signaled via Redis (from PATCH endpoint)."""
        try:
            r = await self._get_redis()
            key = f"{RESTART_KEY_PREFIX}{cam_id}"
            if await r.exists(key):
                await r.delete(key)
                return True
        except Exception:
            pass
        return False

    async def _sync_cameras(self):
        async with async_session() as db:
            result = await db.execute(select(Camera))
            cameras = {c.id: c for c in result.scalars().all()}

        # Keep webcam bridges aligned with DB state before starting workers
        # so stream workers can pull the bridged RTSP copy.
        try:
            await bridge_manager.sync(list(cameras.values()))
        except Exception:
            logger.exception("webcam bridge sync failed")

        # Start workers for new cameras, restart changed ones
        for cam_id, cam in cameras.items():
            if cam_id not in self._workers:
                logger.info("Starting stream worker for camera %s (%s)", cam.name, cam_id)
                self._create_worker(cam_id, cam)
                continue

            # Check for explicit restart signal (from camera edit)
            restart_signaled = await self._check_restart_signal(cam_id)

            # Check if stream config changed
            new_hash = _stream_config_hash(cam)
            config_changed = new_hash != self._config_hashes.get(cam_id)

            if restart_signaled or config_changed:
                reason = "restart signal" if restart_signaled else "config change"
                logger.info("Restarting stream worker for camera %s (%s). %s", cam.name, cam_id, reason)
                self._stop_worker(cam_id)
                self._create_worker(cam_id, cam)

        # Stop workers for removed cameras
        removed = set(self._workers.keys()) - set(cameras.keys())
        for cam_id in removed:
            logger.info("Stopping stream worker for camera %s", cam_id)
            self._stop_worker(cam_id)
