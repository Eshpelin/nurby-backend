"""
Redis-backed VLM job backlog.

Persistent per-camera buffer that survives perception restarts. Replaces
the old in-memory ``asyncio.Queue(maxsize=2)`` so we can actually catch
up on backlog instead of silently dropping evidence.

Wire format.

  Key                                            Value                       Notes
  ─────────────────────────────────────────────  ──────────────────────────  ────────────────────────────
  ``nurby:vlm_pending:<camera_id>``              Redis LIST of metadata json LPUSH new, RPOP oldest first.
  ``nurby:vlm_frame:<job_id>``                   JPEG bytes                  Per-job blob, TTL 1800s.

The list is the queue. Metadata sits in the list itself, so the worker
can deserialize without a separate fetch. The frame blob lives on its
own key with a TTL so we don't pin large bytes if the worker never gets
to it.

Drop-oldest semantics. Each enqueue runs ``LTRIM`` to bound the list to
``capacity`` (default 50). Trimmed entries leak their frame blobs but
those blobs self-expire via the TTL.

Restart resilience. Pop is ``BRPOP`` with a timeout, so the worker
restarts and immediately drains anything that survived the outage. Frame
blob TTL means truly stale jobs (older than 30 min) are skipped with a
visible warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
import redis.asyncio as aioredis

logger = logging.getLogger("nurby.perception.vlm_backlog")


# Defaults. The actual capacity per camera is read from
# ``AppSetting.vlm_backlog_capacity_per_camera`` at runtime so an
# operator can tune without code change.
DEFAULT_BACKLOG_CAPACITY = 50
DEFAULT_FRAME_TTL_SECONDS = 1800  # 30 min
JPEG_QUALITY = 85

PENDING_KEY = "nurby:vlm_pending"
FRAME_KEY = "nurby:vlm_frame"


@dataclass
class BacklogEnvelope:
    """A pop'd backlog entry.

    Carries the metadata the worker needs to rehydrate a VLMJob plus the
    decoded frame. ``stale`` is True when the metadata survived but the
    frame blob expired — the worker should skip such envelopes.
    """

    job_id: str
    camera_id: str
    observation_id: uuid.UUID
    detections: list[dict]
    provider_id: uuid.UUID
    system_prompt: str | None
    max_tokens: int | None
    max_input_tokens: int | None
    heard_text: str | None
    extra_context: str | None
    timestamp: datetime
    enqueued_at: float
    refiner_provider_id: uuid.UUID | None
    refiner_trigger_objects: list[str] | None
    refiner_keywords: list[str] | None
    refiner_max_tokens: int | None
    refiner_max_input_tokens: int | None
    frame: np.ndarray | None
    stale: bool = False
    priority: str = "normal"
    raw: dict = field(default_factory=dict)


class VLMBacklog:
    """Per-camera persistent backlog backed by Redis."""

    def __init__(
        self,
        redis: aioredis.Redis,
        capacity: int = DEFAULT_BACKLOG_CAPACITY,
        frame_ttl_seconds: int = DEFAULT_FRAME_TTL_SECONDS,
    ):
        self._r = redis
        self._capacity = max(2, int(capacity))
        self._frame_ttl = max(60, int(frame_ttl_seconds))

    # ── public API ────────────────────────────────────────────────────

    def list_key(self, camera_id: str, priority: str = "normal") -> str:
        # Two physical lists per camera. The worker drains :high
        # before :normal so urgent frames (unknown face, rule-trigger
        # match, first-of-burst) skip ahead of routine motion.
        suffix = ":high" if priority == "high" else ""
        return f"{PENDING_KEY}:{camera_id}{suffix}"

    def frame_key(self, job_id: str) -> str:
        return f"{FRAME_KEY}:{job_id}"

    async def enqueue(
        self,
        *,
        camera_id: str,
        observation_id: uuid.UUID,
        frame: np.ndarray,
        detections: list[dict],
        provider_id: uuid.UUID,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        max_input_tokens: int | None = None,
        heard_text: str | None = None,
        extra_context: str | None = None,
        timestamp: datetime | None = None,
        refiner_provider_id: uuid.UUID | None = None,
        refiner_trigger_objects: list[str] | None = None,
        refiner_keywords: list[str] | None = None,
        refiner_max_tokens: int | None = None,
        refiner_max_input_tokens: int | None = None,
        priority: str = "normal",
    ) -> str:
        """Push a job onto the backlog. Returns the job_id."""
        if priority not in ("normal", "high"):
            priority = "normal"
        job_id = uuid.uuid4().hex
        # Encode the frame as JPEG bytes once. Stays out of the
        # metadata so the list entry remains small and Redis-friendly.
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("cv2.imencode failed for VLM backlog frame")
        await self._r.setex(self.frame_key(job_id), self._frame_ttl, bytes(buf))

        meta = {
            "v": 1,
            "job_id": job_id,
            "camera_id": camera_id,
            "observation_id": str(observation_id),
            "detections": detections or [],
            "provider_id": str(provider_id),
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
            "max_input_tokens": max_input_tokens,
            "heard_text": heard_text,
            "extra_context": extra_context,
            "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
            "enqueued_at": time.time(),
            "refiner_provider_id": str(refiner_provider_id) if refiner_provider_id else None,
            "refiner_trigger_objects": refiner_trigger_objects,
            "refiner_keywords": refiner_keywords,
            "refiner_max_tokens": refiner_max_tokens,
            "refiner_max_input_tokens": refiner_max_input_tokens,
            "priority": priority,
        }
        payload = json.dumps(meta, default=str).encode("utf-8")
        target_key = self.list_key(camera_id, priority)
        # LPUSH new, RPOP oldest first. Newest sits at head; oldest
        # gets drained first.
        pipe = self._r.pipeline()
        pipe.lpush(target_key, payload)
        # Bound the list. LTRIM keeps indices 0..(capacity-1) which
        # are the newest `capacity` entries — older ones get dropped.
        pipe.ltrim(target_key, 0, self._capacity - 1)
        await pipe.execute()
        return job_id

    async def pop(self, camera_id: str, timeout_seconds: float = 10.0) -> BacklogEnvelope | None:
        """Block up to ``timeout_seconds`` for the next oldest job.

        Returns None on timeout. Returns an envelope with ``stale=True``
        when the metadata survived but the frame blob already expired.

        Drains the per-camera high-priority list first; the timeout
        applies only when both queues are empty.
        """
        timeout = max(1, int(round(timeout_seconds)))
        # BRPOP across multiple keys: Redis returns from the first
        # non-empty key in the supplied order. With :high listed
        # first, high-priority entries always win the race when both
        # are non-empty.
        res = await self._r.brpop(
            [self.list_key(camera_id, "high"), self.list_key(camera_id, "normal")],
            timeout=timeout,
        )
        if not res:
            return None
        _, payload = res
        try:
            meta = json.loads(payload)
        except Exception:
            logger.exception("backlog pop. failed to decode metadata for camera=%s", camera_id)
            return None

        job_id = meta.get("job_id", "")
        # Fetch + delete the frame blob in one round-trip. GETDEL not
        # available on all redis builds; fall back to GET + DEL.
        try:
            frame_bytes = await self._r.getdel(self.frame_key(job_id))  # type: ignore[attr-defined]
        except AttributeError:
            frame_bytes = await self._r.get(self.frame_key(job_id))
            if frame_bytes is not None:
                await self._r.delete(self.frame_key(job_id))

        env_kwargs: dict[str, Any] = {
            "job_id": job_id,
            "camera_id": meta.get("camera_id", camera_id),
            "observation_id": uuid.UUID(meta["observation_id"]),
            "detections": meta.get("detections") or [],
            "provider_id": uuid.UUID(meta["provider_id"]),
            "system_prompt": meta.get("system_prompt"),
            "max_tokens": meta.get("max_tokens"),
            "max_input_tokens": meta.get("max_input_tokens"),
            "heard_text": meta.get("heard_text"),
            "extra_context": meta.get("extra_context"),
            "timestamp": datetime.fromisoformat(meta["timestamp"]),
            "enqueued_at": float(meta.get("enqueued_at") or time.time()),
            "refiner_provider_id": uuid.UUID(meta["refiner_provider_id"])
            if meta.get("refiner_provider_id")
            else None,
            "refiner_trigger_objects": meta.get("refiner_trigger_objects"),
            "refiner_keywords": meta.get("refiner_keywords"),
            "refiner_max_tokens": meta.get("refiner_max_tokens"),
            "refiner_max_input_tokens": meta.get("refiner_max_input_tokens"),
            "priority": meta.get("priority", "normal"),
            "raw": meta,
        }

        if frame_bytes is None:
            # The job survived a restart that took longer than the
            # frame TTL. Return a stale envelope so the worker can log
            # + skip.
            logger.warning(
                "backlog pop. stale entry camera=%s job=%s (frame ttl expired)",
                camera_id, job_id,
            )
            return BacklogEnvelope(frame=None, stale=True, **env_kwargs)

        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning(
                "backlog pop. JPEG decode failed camera=%s job=%s",
                camera_id, job_id,
            )
            return BacklogEnvelope(frame=None, stale=True, **env_kwargs)
        return BacklogEnvelope(frame=frame, stale=False, **env_kwargs)

    async def size(self, camera_id: str) -> int:
        normal = int(await self._r.llen(self.list_key(camera_id, "normal")) or 0)
        high = int(await self._r.llen(self.list_key(camera_id, "high")) or 0)
        return normal + high

    async def size_by_priority(self, camera_id: str) -> dict:
        """Per-priority counts so the telemetry can show 'high=N normal=M'."""
        normal = int(await self._r.llen(self.list_key(camera_id, "normal")) or 0)
        high = int(await self._r.llen(self.list_key(camera_id, "high")) or 0)
        return {"high": high, "normal": normal, "total": high + normal}

    @property
    def capacity(self) -> int:
        return self._capacity


__all__ = [
    "VLMBacklog",
    "BacklogEnvelope",
    "DEFAULT_BACKLOG_CAPACITY",
    "DEFAULT_FRAME_TTL_SECONDS",
    "PENDING_KEY",
    "FRAME_KEY",
]
