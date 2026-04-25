"""In-process counters and latency rings for the audio pipeline.

Plain Python. No external dependency. The shape mimics Prometheus
labels (counter[(name, label_tuple)]) so we can swap to
``prometheus_client`` later without touching call sites.

Read snapshot via :func:`snapshot`. The admin stats endpoint serves
this as JSON.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any


_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_latency_rings: dict[tuple[str, tuple[tuple[str, str], ...]], deque[float]] = {}
_RING_SIZE = 500


def _key(name: str, labels: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    items = tuple(sorted((labels or {}).items()))
    return (name, items)


def incr(name: str, labels: dict[str, str] | None = None, n: int = 1) -> None:
    k = _key(name, labels)
    with _lock:
        _counters[k] += n


def gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    k = _key(name, labels)
    with _lock:
        _gauges[k] = value


def observe_latency(name: str, seconds: float, labels: dict[str, str] | None = None) -> None:
    k = _key(name, labels)
    with _lock:
        ring = _latency_rings.get(k)
        if ring is None:
            ring = deque(maxlen=_RING_SIZE)
            _latency_rings[k] = ring
        ring.append(seconds)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return s[idx]


def snapshot() -> dict[str, Any]:
    with _lock:
        counters = [
            {"name": n, "labels": dict(lbl), "value": v}
            for (n, lbl), v in _counters.items()
        ]
        gauges = [
            {"name": n, "labels": dict(lbl), "value": v}
            for (n, lbl), v in _gauges.items()
        ]
        latencies = []
        for (n, lbl), ring in _latency_rings.items():
            vals = list(ring)
            latencies.append(
                {
                    "name": n,
                    "labels": dict(lbl),
                    "count": len(vals),
                    "p50": _percentile(vals, 50),
                    "p95": _percentile(vals, 95),
                    "p99": _percentile(vals, 99),
                }
            )
    return {"counters": counters, "gauges": gauges, "latencies": latencies}
