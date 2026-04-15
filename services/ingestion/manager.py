"""
Camera manager. Watches the database for camera configs and spawns/stops
stream workers accordingly.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select

from shared.database import async_session
from shared.models import Camera
from services.ingestion.stream import StreamWorker

logger = logging.getLogger("nurby.ingestion.manager")

POLL_INTERVAL = 10  # seconds between DB polls for camera changes


class CameraManager:
    def __init__(self):
        self._workers: dict[uuid.UUID, StreamWorker] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}

    async def run(self):
        """Main loop. Polls DB for cameras and manages workers."""
        while True:
            try:
                await self._sync_cameras()
            except Exception:
                logger.exception("Error syncing cameras")
            await asyncio.sleep(POLL_INTERVAL)

    async def _sync_cameras(self):
        async with async_session() as db:
            result = await db.execute(select(Camera))
            cameras = {c.id: c for c in result.scalars().all()}

        # Start workers for new cameras
        for cam_id, cam in cameras.items():
            if cam_id not in self._workers:
                logger.info("Starting stream worker for camera %s (%s)", cam.name, cam_id)
                worker = StreamWorker(cam_id, cam.stream_url, cam.recording_enabled)
                self._workers[cam_id] = worker
                self._tasks[cam_id] = asyncio.create_task(worker.run())

        # Stop workers for removed cameras
        removed = set(self._workers.keys()) - set(cameras.keys())
        for cam_id in removed:
            logger.info("Stopping stream worker for camera %s", cam_id)
            self._workers[cam_id].stop()
            self._tasks[cam_id].cancel()
            del self._workers[cam_id]
            del self._tasks[cam_id]
