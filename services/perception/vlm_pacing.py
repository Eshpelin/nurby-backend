"""Adaptive live-VLM enqueue pacing decision.

Pure, no I/O, so it is unit-testable in isolation. The pipeline gathers the
live signals (measured latency, backlog depth, time since last enqueue) and
asks this function whether to enqueue the current frame.

Goal: keep the live VLM queue shallow so the model always works a recent
frame instead of grinding through a stale backlog, and never enqueue faster
than the model can actually process.
"""

from __future__ import annotations

# Drop a normal-priority frame once the backlog reaches this depth. keeps the
# queue shallow so the next frame the VLM processes is fresh.
NORMAL_BACKLOG_CAP = 2
# Even urgent frames (unknown face, rule trigger) never push the queue past
# this. they bypass the cadence but not this hard ceiling.
HIGH_BACKLOG_CAP = 6


def should_enqueue(
    priority: str,
    *,
    avg_latency: float,
    backlog: int,
    base_interval: float,
    seconds_since_last: float,
    normal_cap: int = NORMAL_BACKLOG_CAP,
    high_cap: int = HIGH_BACKLOG_CAP,
) -> bool:
    """Decide whether to enqueue one frame for the live VLM.

    - high priority. enqueue unless the backlog is already at the hard ceiling.
    - normal priority. skip while the backlog is non-trivial (the VLM is
      behind, so this frame would just go stale), otherwise enqueue only once
      enough time has passed. that minimum gap self-tunes to the model's
      measured throughput. ``max(base_interval, avg_latency)`` means a model
      that takes 3s per frame is fed at most once every 3s, learned
      automatically. before any latency data ``avg_latency`` is 0 and the gate
      is just the configured ``base_interval``.
    """
    if priority == "high":
        return backlog < high_cap
    if backlog >= normal_cap:
        return False
    interval = max(base_interval, avg_latency)
    if interval <= 0:
        return True
    return seconds_since_last >= interval
