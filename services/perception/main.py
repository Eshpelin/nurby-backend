"""
Perception service entry point.

Consumes motion keyframes from Redis, runs object detection,
optionally calls a VLM for scene descriptions, and stores
observations in the database.
"""

import asyncio
import logging

from services.perception.live_detector import LiveDetector
from services.perception.pipeline import PerceptionPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("nurby.perception")


async def main():
    logger.info("Starting Nurby perception service")
    pipeline = PerceptionPipeline()
    live = LiveDetector()
    await asyncio.gather(pipeline.run(), live.run())


if __name__ == "__main__":
    asyncio.run(main())
