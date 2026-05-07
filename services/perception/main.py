"""
Perception service entry point.

Consumes motion keyframes from Redis, runs object detection,
optionally calls a VLM for scene descriptions, and stores
observations in the database.
"""

import asyncio
import logging

from services.perception.conversation_finalizer import ConversationFinalizer
from services.perception.live_detector import LiveDetector
from services.perception.pipeline import PerceptionPipeline
from services.perception.summarizer import CameraSummarizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("nurby.perception")


async def main():
    logger.info("Starting Nurby perception service")
    pipeline = PerceptionPipeline()
    live = LiveDetector()
    # Lazy import so the perception process doesn't pull api routing
    # at import time. ws.broadcast is the same channel the rest of the
    # app uses, so summary_created lands in /ws subscribers.
    try:
        from services.api.ws import broadcast as ws_broadcast
        summarizer = CameraSummarizer(broadcast_fn=ws_broadcast)
    except Exception:
        logger.exception("ws import failed, summaries will not broadcast")
        summarizer = CameraSummarizer()
    finalizer = ConversationFinalizer()
    await asyncio.gather(
        pipeline.run(),
        live.run(),
        summarizer.run(),
        finalizer.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
