"""HAR orchestration runner (Phase 2-4 glue), per camera.

Ties the pieces together on the dense ingestion stream:
  pose inference -> tracker -> per-track pose window -> action backend -> state machine
  -> identity attribution -> persist segments + broadcast live actions.

INTEGRATION-PENDING. The live model, Redis binding lookup, DB persistence, and WS broadcast
are injected as callables so the orchestration logic is fully unit-testable here, but the
real wiring (a pose model, the ingestion StreamWorker calling this under an executor, the
shared identity map, the DB) is only exercised on a real deployment. Defaults wire the real
implementations lazily; tests inject fakes.

Design notes grounded in the code that was read:
- The tracker is the existing greedy-IoU ``ObjectTracker`` (tracker.py), which adds a
  ``tracker_id`` to each detection in place. We reuse it so HAR tracks align with the
  loitering/zone tracks rather than inventing a second notion.
- Identity comes from the binding map written by perception (identity_binding), looked up by
  ``(camera_id, tracker_id)``. A track with no binding is ``unknown`` and, for guardian-facing
  cameras, its actions are not broadcast with a name. We never invent identity.
- The action backend defaults to the runnable geometric classifier; ST-GCN drops in later.
"""

from __future__ import annotations

import logging
from collections import deque

from services.perception.har_actions import PoseFrame, get_backend
from services.perception.har_state import ActionStateMachine

logger = logging.getLogger("nurby.perception.har_runner")

DEFAULT_WINDOW = 16        # pose frames per classification window
DEFAULT_MIN_FRAMES = 8     # classify once we have at least this many


class HARRunner:
    """Per-camera HAR orchestrator. Call ``process_poses`` with this frame's detected
    person poses; it returns the action segments finalised this tick and the current
    per-person live-action snapshot. Pure given its injected hooks."""

    def __init__(
        self,
        camera_id,
        *,
        backend_name: str = "geometric",
        window: int = DEFAULT_WINDOW,
        min_frames: int = DEFAULT_MIN_FRAMES,
        identity_fn=None,          # (camera_id, tracker_id) -> {person_id, person_name} | None
        tracker=None,              # ObjectTracker-like; default created lazily
    ):
        self.camera_id = str(camera_id)
        self.backend = get_backend(backend_name)
        self.window = window
        self.min_frames = min_frames
        self._identity_fn = identity_fn or (lambda c, t: None)
        self._buffers: dict[int, deque] = {}
        self._sm = ActionStateMachine(source=self.backend.name)
        if tracker is None:
            from services.perception.tracker import ObjectTracker

            tracker = ObjectTracker()
        self._tracker = tracker

    def process_poses(self, poses: list[dict], now: float) -> tuple[list[dict], list[dict]]:
        """``poses``: list of ``{bbox, keypoints}`` (keypoints = 17 (x,y,conf)).

        Returns ``(segments, live)`` where ``segments`` are action runs finalised this tick
        (each enriched with identity) and ``live`` is the current per-person action snapshot
        for the WS overlay."""
        # 1. Track. Reuse ObjectTracker; it stamps tracker_id on each detection in order.
        dets = [{"label": "person", "bbox": list(p.get("bbox") or [])} for p in poses]
        self._tracker.update(dets)

        # 2. Append this frame's pose to each track's window.
        live: list[dict] = []
        seen_tracks: set[int] = set()
        segments: list[dict] = []
        for pose, det in zip(poses, dets):
            tid = det.get("tracker_id")
            if tid is None:
                continue
            tid = int(tid)
            seen_tracks.add(tid)
            buf = self._buffers.get(tid)
            if buf is None:
                buf = deque(maxlen=self.window)
                self._buffers[tid] = buf
            buf.append(PoseFrame(keypoints=pose.get("keypoints") or [], bbox=pose.get("bbox"), ts=now))

            # 3. Classify once the window is deep enough.
            if len(buf) >= self.min_frames:
                action, conf = self.backend.classify(list(buf))
            else:
                action, conf = ("unknown", 0.0)

            # 4. Feed the state machine; collect any finalised segment.
            new_segs = self._sm.observe(self.camera_id, tid, action, conf, now)
            ident = self._identity_fn(self.camera_id, tid) or {}
            for s in new_segs:
                s["person_id"] = ident.get("person_id")
                s["person_name"] = ident.get("person_name")
            segments.extend(new_segs)

            # 5. Live snapshot. Only attach a name when the track is bound to a person.
            live.append({
                "track_id": tid,
                "person_id": ident.get("person_id"),
                "person_name": ident.get("person_name"),
                "action": action,
                "confidence": conf,
            })

        # 6. Flush tracks that disappeared (close their open segment).
        for tid in [t for t in self._buffers if t not in seen_tracks]:
            closed = self._sm.flush(self.camera_id, tid, now)
            ident = self._identity_fn(self.camera_id, tid) or {}
            for s in closed:
                s["person_id"] = ident.get("person_id")
                s["person_name"] = ident.get("person_name")
            segments.extend(closed)
            self._buffers.pop(tid, None)

        return segments, live


# ── integration hooks (real implementations, used by the ingestion StreamWorker) ─────────
# These are intentionally thin and untested-here; they wire the runner to the live stack.

async def persist_segments(segments: list[dict]) -> None:  # pragma: no cover - integration
    """Write finalised segments to person_action_segments. Drops segments whose action is
    unknown or that have no usable timestamp. Called by the ingestion runner."""
    if not segments:
        return
    import uuid as _uuid
    from datetime import datetime, timezone

    from shared.database import async_session
    from shared.models import PersonActionSegment

    def _as_uuid(v):
        if v is None or isinstance(v, _uuid.UUID):
            return v
        try:
            return _uuid.UUID(str(v))
        except (ValueError, TypeError):
            return None

    def _as_dt(v):
        # Runner timestamps are monotonic floats per camera; the caller passes wall-clock
        # datetimes when persisting. Accept datetime; skip otherwise.
        return v if isinstance(v, datetime) else None

    try:
        async with async_session() as db:
            for s in segments:
                if s.get("action") in (None, "unknown"):
                    continue
                started = _as_dt(s.get("started_at"))
                if started is None:
                    continue
                db.add(PersonActionSegment(
                    camera_id=_as_uuid(s.get("camera_id")),
                    person_id=_as_uuid(s.get("person_id")),
                    person_name=s.get("person_name"),
                    track_id=s.get("track_id"),
                    action=s["action"],
                    confidence_avg=s.get("confidence_avg"),
                    started_at=started,
                    ended_at=_as_dt(s.get("ended_at")) or datetime.now(timezone.utc),
                    source=s.get("source"),
                ))
            await db.commit()
    except Exception:
        logger.debug("HAR segment persist failed", exc_info=True)
