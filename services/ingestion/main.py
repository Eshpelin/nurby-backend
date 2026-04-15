"""
Ingestion service entry point.

Manages RTSP stream connections, frame decoding, motion detection,
and segment recording for all configured cameras.
"""

import asyncio
import logging

from services.ingestion.manager import CameraManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("nurby.ingestion")


async def main():
    logger.info("Starting Nurby ingestion service")
    manager = CameraManager()
    await manager.run()


if __name__ == "__main__":
    asyncio.run(main())
