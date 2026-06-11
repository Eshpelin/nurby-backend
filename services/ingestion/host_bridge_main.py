"""
Host-side webcam bridge daemon.

Runs on the machine that physically owns the USB or built-in camera,
typically the developer laptop or the host in a self-hosted deployment.
Docker Desktop on macOS and Windows cannot forward AVFoundation or dshow
capture devices into a Linux container, so the bridge ffmpeg processes
have to run on the host and publish into MediaMTX over RTSP.

Flow. Poll the database for cameras with stream_type="usb" and a
webcam_device set. For each one, supervise an ffmpeg process that reads
the local capture device and publishes to MediaMTX at
rtsp://<mediamtx>:8554/webcam-<camera-id>. The ingestion stream worker
inside the container then pulls that RTSP path like any other camera.

Run this on the host.
    python -m services.ingestion.host_bridge_main

Required env on the host.
    DATABASE_URL=postgresql+asyncpg://nurby:nurby_dev@localhost:5433/nurby
    MEDIAMTX_RTSP_URL=rtsp://localhost:8554
"""

import asyncio
import logging
import sys

from sqlalchemy import select

from services.ingestion.webcam_bridge import bridge_manager
from shared.config import settings
from shared.database import async_session
from shared.models import Camera

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("nurby.host_bridge")

POLL_INTERVAL = 10  # seconds between DB polls


async def _sync_once() -> None:
    async with async_session() as db:
        result = await db.execute(select(Camera))
        cameras = list(result.scalars().all())
    await bridge_manager.sync(cameras)


async def main() -> None:
    logger.info(
        "Starting Nurby webcam bridge daemon. mediamtx=%s",
        settings.mediamtx_rtsp_url,
    )
    try:
        while True:
            try:
                await _sync_once()
            except Exception:
                logger.exception("bridge sync failed")
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await bridge_manager.stop_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("webcam bridge daemon shutting down")
        sys.exit(0)
