"""
Async VLM queue with backpressure handling.

Decouples VLM calls from the main perception pipeline so detection,
face matching, and observation storage are never blocked by slow VLM
responses. Tracks latency per camera and broadcasts status via WebSocket.

Key design decisions.
- Bounded queue per camera (max 2 pending). Newer frames replace older ones.
- Single VLM worker per camera. No concurrent calls to same model for same feed.
- Latency stats tracked with exponential moving average.
- Observations stored immediately without VLM. Description patched in async.
- WebSocket broadcasts VLM status changes so frontend can show indicators.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from shared.database import async_session
from shared.models import Observation, Provider
from services.perception.vlm import VLMClient
from services.search.embeddings import generate_embedding, get_embedding_provider

logger = logging.getLogger("nurby.perception.vlm_queue")

# Module-level stats registry so API can read without direct pipeline reference
_global_stats: dict[str, "CameraVLMStats"] = {}


def get_vlm_stats() -> dict[str, dict]:
    """Get VLM stats for all cameras. Safe to call from any context."""
    return {cid: s.to_dict() for cid, s in _global_stats.items()}


@dataclass
class VLMJob:
    """A pending VLM call."""
    camera_id: str
    observation_id: uuid.UUID
    frame: np.ndarray
    detections: list[dict]
    provider: Provider
    system_prompt: str | None
    max_tokens: int
    timestamp: datetime
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class CameraVLMStats:
    """Per-camera VLM performance stats."""
    avg_latency: float = 0.0       # exponential moving average in seconds
    last_latency: float = 0.0      # most recent call duration
    total_calls: int = 0
    total_errors: int = 0
    total_dropped: int = 0         # frames dropped due to backpressure
    last_call_at: float = 0.0      # monotonic timestamp
    last_result_at: float = 0.0    # monotonic timestamp
    status: str = "idle"           # idle, processing, slow, stalled

    def record_latency(self, duration: float):
        self.last_latency = duration
        self.total_calls += 1
        self.last_result_at = time.monotonic()
        # Exponential moving average (alpha=0.3 for responsiveness)
        if self.avg_latency == 0:
            self.avg_latency = duration
        else:
            self.avg_latency = 0.3 * duration + 0.7 * self.avg_latency

    def record_error(self):
        self.total_errors += 1
        self.last_result_at = time.monotonic()

    def record_drop(self):
        self.total_dropped += 1

    def update_status(self):
        now = time.monotonic()
        if self.status == "idle":
            return
        # Stalled if no result in 5 minutes while processing
        if self.last_call_at > 0 and (now - self.last_call_at) > 300:
            self.status = "stalled"
        elif self.avg_latency > 10:
            self.status = "slow"

    def to_dict(self) -> dict:
        self.update_status()
        return {
            "avg_latency": round(self.avg_latency, 2),
            "last_latency": round(self.last_latency, 2),
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_dropped": self.total_dropped,
            "status": self.status,
        }


class VLMQueue:
    """Bounded async queue for VLM calls with per-camera workers."""

    MAX_PENDING_PER_CAMERA = 2  # keep latest N frames, drop older

    def __init__(self, vlm_client: VLMClient | None = None):
        self._vlm = vlm_client or VLMClient()
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._stats: dict[str, CameraVLMStats] = {}
        self._broadcast_fn = None  # set via set_broadcast()
        self._running = False

    def set_broadcast(self, fn):
        """Set the WebSocket broadcast function (from services.api.ws)."""
        self._broadcast_fn = fn

    def get_stats(self, camera_id: str) -> CameraVLMStats:
        if camera_id not in self._stats:
            self._stats[camera_id] = CameraVLMStats()
        return self._stats[camera_id]

    def get_all_stats(self) -> dict[str, dict]:
        return {cid: s.to_dict() for cid, s in self._stats.items()}

    async def enqueue(self, job: VLMJob):
        """Submit a VLM job. Drops oldest if queue full."""
        camera_id = job.camera_id

        if camera_id not in self._queues:
            self._queues[camera_id] = asyncio.Queue(maxsize=self.MAX_PENDING_PER_CAMERA)
            stats = CameraVLMStats()
            self._stats[camera_id] = stats
            _global_stats[camera_id] = stats  # expose to API via get_vlm_stats()

        q = self._queues[camera_id]

        # If queue full, drop oldest (backpressure)
        while q.full():
            try:
                dropped = q.get_nowait()
                self._stats[camera_id].record_drop()
                logger.info(
                    "VLM backpressure for camera %s. Dropped frame from %s (queue full)",
                    camera_id, dropped.timestamp.isoformat(),
                )
            except asyncio.QueueEmpty:
                break

        await q.put(job)

        # Start worker if not running
        if camera_id not in self._workers or self._workers[camera_id].done():
            self._workers[camera_id] = asyncio.create_task(
                self._worker(camera_id),
                name=f"vlm-worker-{camera_id[:8]}",
            )

    async def _worker(self, camera_id: str):
        """Process VLM jobs for a single camera sequentially."""
        q = self._queues[camera_id]
        stats = self._stats[camera_id]

        while True:
            try:
                job = await asyncio.wait_for(q.get(), timeout=60)
            except asyncio.TimeoutError:
                # No work for 60s, shut down worker
                stats.status = "idle"
                await self._broadcast_status(camera_id)
                logger.debug("VLM worker for camera %s idle, shutting down", camera_id)
                return

            stats.status = "processing"
            stats.last_call_at = time.monotonic()
            await self._broadcast_status(camera_id)

            start = time.monotonic()
            try:
                description = await self._vlm.describe(
                    job.frame,
                    job.detections,
                    job.provider,
                    system_prompt=job.system_prompt,
                    max_tokens=job.max_tokens,
                )
                duration = time.monotonic() - start
                stats.record_latency(duration)

                if description:
                    # Patch observation with VLM description and regenerate embedding
                    await self._patch_observation(
                        job.observation_id, description, job.provider.name,
                        job.detections,
                    )
                    logger.info(
                        "VLM for camera %s completed in %.1fs. %s",
                        camera_id, duration, description[:80],
                    )
                else:
                    logger.warning("VLM returned empty for camera %s (%.1fs)", camera_id, duration)

            except Exception:
                duration = time.monotonic() - start
                stats.record_error()
                logger.exception(
                    "VLM call failed for camera %s after %.1fs", camera_id, duration,
                )

            # Update status based on latency
            if stats.avg_latency > 10:
                stats.status = "slow"
            else:
                stats.status = "processing" if not q.empty() else "idle"

            await self._broadcast_status(camera_id)

    async def _patch_observation(
        self, observation_id: uuid.UUID, description: str, provider_name: str,
        detections: list[dict],
    ):
        """Update observation record with VLM description and regenerate embedding."""
        try:
            async with async_session() as db:
                obs = await db.get(Observation, observation_id)
                if obs:
                    obs.vlm_description = description
                    obs.vlm_provider = provider_name
                    obs.confidence = 0.8
                    await db.commit()
        except Exception:
            logger.exception("Failed to patch observation %s with VLM description", observation_id)
            return

        # Regenerate embedding now that VLM description is available
        try:
            parts = [description]
            if detections:
                labels = [d["label"] for d in detections]
                parts.append("Objects detected. " + ", ".join(labels))
            embed_text = ". ".join(parts)

            provider = await get_embedding_provider()
            embedding = await generate_embedding(embed_text, provider)

            async with async_session() as db:
                obs = await db.get(Observation, observation_id)
                if obs:
                    obs.description_embedding = embedding
                    await db.commit()
                    logger.debug("Regenerated embedding for observation %s with VLM description", observation_id)
        except Exception:
            logger.warning("Failed to regenerate embedding for observation %s", observation_id)

    async def _broadcast_status(self, camera_id: str):
        """Broadcast VLM status update via WebSocket."""
        if not self._broadcast_fn:
            return
        stats = self._stats.get(camera_id)
        if not stats:
            return
        try:
            await self._broadcast_fn({
                "type": "vlm_status",
                "camera_id": camera_id,
                "vlm": stats.to_dict(),
            })
        except Exception:
            pass  # don't let broadcast errors crash the worker

    async def shutdown(self):
        """Cancel all workers."""
        for task in self._workers.values():
            task.cancel()
        self._workers.clear()
