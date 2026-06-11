"""Per-run WebSocket pub/sub for the agent driver.

In-process pub/sub (no Redis in v1) with an in-memory ring buffer per
run for replay-after-disconnect. The buffer retains events for 5 minutes
post-completion, then evicts.

Public API.

* ``publish_event(run_id, event)`` is awaited by the driver for every
  WS frame it wants to emit.
* ``subscribe(run_id)`` is awaited by the WS handler to attach a queue
  to a run. Returns ``(queue, replayed_events)``.
* ``replay_after(run_id, after_seq)`` returns buffered events whose
  ``seq`` > ``after_seq`` for late reconnects.
* ``mark_terminal(run_id)`` is called by the driver on done/cancel/error
  to schedule eviction.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("nurby.agent.ws")


_BUFFER_SIZE = 256
_RETAIN_SECONDS = 300  # 5 minutes post-terminal


@dataclass
class _RunBus:
    events: deque = field(default_factory=lambda: deque(maxlen=_BUFFER_SIZE))
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    terminal_at: float | None = None


_buses: dict[str, _RunBus] = {}
_lock = asyncio.Lock()


async def publish_event(run_id: str, event: dict) -> None:
    """Append ``event`` to the run's ring buffer + fan out to subscribers."""
    async with _lock:
        bus = _buses.get(run_id)
        if bus is None:
            bus = _RunBus()
            _buses[run_id] = bus
        bus.events.append(event)
        dead: list[asyncio.Queue] = []
        for q in bus.subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            bus.subscribers.discard(q)
        if event.get("type") in {"done", "cancelled", "error"}:
            bus.terminal_at = time.time()


async def subscribe(run_id: str, after_seq: int = 0) -> tuple[asyncio.Queue, list[dict]]:
    """Attach a fresh queue + return any buffered events past ``after_seq``."""
    async with _lock:
        bus = _buses.get(run_id)
        if bus is None:
            bus = _RunBus()
            _buses[run_id] = bus
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        bus.subscribers.add(q)
        backlog = [e for e in bus.events if int(e.get("seq") or 0) > after_seq]
        return q, backlog


async def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    async with _lock:
        bus = _buses.get(run_id)
        if bus is not None:
            bus.subscribers.discard(q)


async def replay_after(run_id: str, after_seq: int) -> list[dict]:
    async with _lock:
        bus = _buses.get(run_id)
        if bus is None:
            return []
        return [e for e in bus.events if int(e.get("seq") or 0) > after_seq]


async def mark_terminal(run_id: str) -> None:
    async with _lock:
        bus = _buses.get(run_id)
        if bus is not None:
            bus.terminal_at = time.time()


async def janitor_once() -> int:
    """Evict buses whose terminal_at is older than _RETAIN_SECONDS.
    Returns the number of buses dropped. Callable from a background task
    or directly from tests."""
    dropped = 0
    now = time.time()
    async with _lock:
        for rid in list(_buses.keys()):
            b = _buses[rid]
            if b.terminal_at is not None and (now - b.terminal_at) > _RETAIN_SECONDS:
                if not b.subscribers:
                    _buses.pop(rid, None)
                    dropped += 1
    return dropped


def _reset_for_tests() -> None:
    _buses.clear()


__all__ = [
    "publish_event",
    "subscribe",
    "unsubscribe",
    "replay_after",
    "mark_terminal",
    "janitor_once",
]
