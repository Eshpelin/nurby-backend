"""Per-track action state machine (Phase 4 brain).

Turns a noisy per-frame action stream into clean, debounced **segments** (action runs with a
start and end), which is what the timeline and wellbeing rollups read. Two layers of
stability:

1. Smoothing: the live action for a track is the majority vote over a short ring buffer, so a
   single bad frame does not flip the action.
2. Minimum dwell: a segment is only finalised if it lasted at least ``min_dwell`` seconds, so
   a brief blip never becomes a one-second "segment".

This is what answers "standing then suddenly eating": a transition in the smoothed stream
closes the previous segment and opens the next. Pure and side-effect free (state is an
in-memory dict keyed by (camera, track)) so it is unit-testable without a pipeline. Identity
(person_id) is attached by the caller from the binding map, not invented here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

DEFAULT_WINDOW = 5          # frames in the smoothing buffer
DEFAULT_MIN_DWELL = 1.0     # seconds a segment must last to be emitted


@dataclass
class _TrackState:
    buffer: list[str] = field(default_factory=list)
    current: str | None = None
    started_at: float | None = None
    confs: list[float] = field(default_factory=list)
    last_ts: float | None = None


def _segment(cam, track, action, started, ended, confs, source) -> dict:
    avg = sum(confs) / len(confs) if confs else None
    return {
        "camera_id": str(cam),
        "track_id": int(track),
        "action": action,
        "started_at": started,
        "ended_at": ended,
        "confidence_avg": avg,
        "source": source,
    }


class ActionStateMachine:
    def __init__(self, window: int = DEFAULT_WINDOW, min_dwell: float = DEFAULT_MIN_DWELL,
                 source: str = "skeleton"):
        self.window = max(1, window)
        self.min_dwell = min_dwell
        self.source = source
        self._state: dict[tuple, _TrackState] = {}

    def _smoothed(self, st: _TrackState) -> str:
        # Majority over the buffer, ignoring unknown unless that is all we have.
        known = [a for a in st.buffer if a != "unknown"]
        pool = known or st.buffer
        if not pool:
            return "unknown"
        return Counter(pool).most_common(1)[0][0]

    def observe(self, camera_id, track_id, action: str, confidence: float, ts: float) -> list[dict]:
        """Fold one per-frame action for a track. Returns any segment finalised by a
        transition (usually empty)."""
        key = (str(camera_id), int(track_id))
        st = self._state.get(key)
        if st is None:
            st = _TrackState()
            self._state[key] = st

        st.buffer.append(action)
        if len(st.buffer) > self.window:
            st.buffer.pop(0)
        st.last_ts = ts
        smoothed = self._smoothed(st)

        emitted: list[dict] = []
        if st.current is None:
            st.current = smoothed
            st.started_at = ts
            st.confs = [confidence]
        elif smoothed != st.current:
            duration = ts - (st.started_at if st.started_at is not None else ts)
            if duration >= self.min_dwell and st.current != "unknown":
                emitted.append(
                    _segment(key[0], key[1], st.current, st.started_at, ts, st.confs, self.source)
                )
            # Open the new run (replacing a too-short or unknown run without emitting).
            st.current = smoothed
            st.started_at = ts
            st.confs = [confidence]
        else:
            st.confs.append(confidence)
        return emitted

    def flush(self, camera_id, track_id, now: float) -> list[dict]:
        """Close a track's open segment (call on track loss). Returns it if it qualifies."""
        key = (str(camera_id), int(track_id))
        st = self._state.pop(key, None)
        if st is None or st.current is None or st.started_at is None:
            return []
        if st.current == "unknown" or (now - st.started_at) < self.min_dwell:
            return []
        return [_segment(key[0], key[1], st.current, st.started_at, now, st.confs, self.source)]

    def flush_stale(self, now: float, max_idle: float) -> list[dict]:
        """Close segments for tracks not updated within ``max_idle`` seconds."""
        out: list[dict] = []
        for key in list(self._state):
            st = self._state[key]
            if st.last_ts is None or (now - st.last_ts) > max_idle:
                out.extend(self.flush(key[0], key[1], st.last_ts if st.last_ts is not None else now))
        return out
