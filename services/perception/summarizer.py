"""Window-level VLM summarizer.

Generates narrative recaps over a time window per camera. Two modes.

- ``periodic``. Fires every ``summary_period_seconds``. Pulls all
  observations and transcripts in the window, hands them to a VLM as
  text-only context, stores a Summary row.
- ``event``. Tracks "activity" (configurable YOLO labels, default
  ``person``). Opens an event when a matching detection lands, closes
  it after ``summary_event_quiet_seconds`` of no matching activity,
  then summarizes. Skips events shorter than
  ``summary_event_min_duration_seconds`` to ignore false flickers.
- ``both``. Run periodic and event independently.

The VLM here only sees text. No image. The point of the summary stage
is to fuse the per-frame descriptions, transcripts, and identity
facts the original VLM did NOT have at first-pass time. By the time we
summarize, face recognition has settled on names, plate OCR has
landed, and audio has finalized.

This module is intentionally decoupled from the perception pipeline.
It reads its inputs from the DB only, so it can run in the same
process or split out into its own worker later without code changes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from shared.database import async_session
from shared.models import (
    Camera,
    Observation,
    Provider,
    Summary,
    Transcript,
)
from services.perception.text_llm import call_text
from services.perception.vlm import VLMClient, get_active_provider
from services.search.embeddings import generate_embedding, get_embedding_provider

logger = logging.getLogger("nurby.perception.summarizer")


SUMMARY_SYSTEM_PROMPT = (
    "You are a security camera analyst. You are given a window of"
    " observations, transcripts, and identity facts captured by one"
    " camera. Write a 2 to 4 sentence narrative recap of what happened"
    " in the window. Use the identity, plate, and camera location"
    " facts as ground truth. Do not invent people or events not in the"
    " evidence. If nothing notable happened, say so briefly."
)


@dataclass
class _EventState:
    """Per-camera in-memory state for event mode."""

    open_started_at: datetime | None = None
    last_active_at: datetime | None = None
    last_periodic_at: datetime | None = None
    seen_observation_ids: set[str] = field(default_factory=set)


class CameraSummarizer:
    """Reconciles per-camera summarization state on a tick."""

    # Tick frequency. Coarse compared to perception. 5s gives event
    # close detection sub-quiet-window granularity at low cost.
    TICK_SECONDS = 5

    def __init__(self, broadcast_fn=None) -> None:
        self._vlm = VLMClient()
        self._states: dict[uuid.UUID, _EventState] = {}
        self._broadcast = broadcast_fn
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("summarizer started")
        try:
            while not self._stopping.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("summarizer tick failed")
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.TICK_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("summarizer stopped")

    async def _tick(self) -> None:
        async with async_session() as db:
            cams = (await db.execute(select(Camera))).scalars().all()
        now = datetime.now(timezone.utc)
        for cam in cams:
            mode = (cam.summary_mode or "off").lower()
            if mode == "off":
                continue
            state = self._states.setdefault(cam.id, _EventState())
            if mode in ("periodic", "both"):
                await self._maybe_periodic(cam, state, now)
            if mode in ("event", "both"):
                await self._maybe_event(cam, state, now)

    # ---- periodic ----------------------------------------------------

    async def _maybe_periodic(
        self, cam: Camera, state: _EventState, now: datetime
    ) -> None:
        period = max(60, int(cam.summary_period_seconds or 1800))
        if state.last_periodic_at is None:
            # Anchor on first sight so we don't dump a giant historical
            # recap the first time a camera flips on.
            state.last_periodic_at = now
            return
        if (now - state.last_periodic_at).total_seconds() < period:
            return
        window_start = state.last_periodic_at
        window_end = now
        state.last_periodic_at = now
        await self.summarize_window(
            cam=cam,
            kind="periodic",
            window_start=window_start,
            window_end=window_end,
            trigger_reason="timer",
        )

    # ---- event -------------------------------------------------------

    async def _maybe_event(
        self, cam: Camera, state: _EventState, now: datetime
    ) -> None:
        trigger_labels = self._resolve_trigger_labels(cam)
        # Look for matching activity since the last tick.
        last_seen = await self._latest_trigger_activity(
            cam.id, trigger_labels, since=now - timedelta(seconds=300)
        )
        if last_seen is not None:
            if state.open_started_at is None:
                state.open_started_at = last_seen
                logger.info(
                    "event opened camera=%s started=%s labels=%s",
                    cam.id, last_seen, trigger_labels,
                )
            state.last_active_at = last_seen
            return

        # No fresh activity. Decide whether the open event has gone
        # quiet long enough to close.
        if state.open_started_at is None or state.last_active_at is None:
            return
        quiet = max(5, int(cam.summary_event_quiet_seconds or 60))
        if (now - state.last_active_at).total_seconds() < quiet:
            return

        min_dur = max(1, int(cam.summary_event_min_duration_seconds or 5))
        duration = (state.last_active_at - state.open_started_at).total_seconds()
        started = state.open_started_at
        ended = state.last_active_at
        state.open_started_at = None
        state.last_active_at = None
        if duration < min_dur:
            logger.info(
                "event dropped camera=%s duration=%.1fs below min=%ds",
                cam.id, duration, min_dur,
            )
            return

        await self.summarize_window(
            cam=cam,
            kind="event",
            window_start=started,
            window_end=ended,
            trigger_reason="event_close",
        )

    @staticmethod
    def _resolve_trigger_labels(cam: Camera) -> list[str]:
        raw = cam.summary_event_trigger_objects
        if isinstance(raw, list) and raw:
            return [str(x) for x in raw]
        return ["person"]

    async def _latest_trigger_activity(
        self,
        camera_id: uuid.UUID,
        labels: list[str],
        since: datetime,
    ) -> datetime | None:
        """Return the most recent observation timestamp for this camera
        where the YOLO detections include any of the trigger labels.

        We scan the recent observation slice (capped at 500) and filter
        in Python because object_detections is JSONB. For a tight tick
        window this is cheap. Tighten with a GIN index later if needed.
        """
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(Observation.started_at, Observation.object_detections)
                    .where(Observation.camera_id == camera_id)
                    .where(Observation.started_at >= since)
                    .order_by(Observation.started_at.desc())
                    .limit(500)
                )
            ).all()
        label_set = set(labels)
        for started_at, det in rows:
            if not det:
                continue
            objs = det.get("objects") if isinstance(det, dict) else None
            if not objs:
                continue
            for o in objs:
                if (o or {}).get("label") in label_set:
                    return started_at
        return None

    # ---- summarize ---------------------------------------------------

    async def summarize_window(
        self,
        cam: Camera,
        kind: str,
        window_start: datetime,
        window_end: datetime,
        trigger_reason: str,
    ) -> None:
        async with async_session() as db:
            obs_rows = (
                await db.execute(
                    select(Observation)
                    .where(Observation.camera_id == cam.id)
                    .where(Observation.started_at >= window_start)
                    .where(Observation.started_at <= window_end)
                    .order_by(Observation.started_at.asc())
                    .limit(200)
                )
            ).scalars().all()
            tx_rows = (
                await db.execute(
                    select(Transcript)
                    .where(Transcript.camera_id == cam.id)
                    .where(Transcript.filtered.is_(False))
                    .where(Transcript.started_at >= window_start)
                    .where(Transcript.started_at <= window_end)
                    .order_by(Transcript.started_at.asc())
                    .limit(200)
                )
            ).scalars().all()

        if not obs_rows and not tx_rows:
            logger.info(
                "skip empty %s window camera=%s start=%s",
                kind, cam.id, window_start,
            )
            return

        provider = await self._resolve_summary_provider(cam)
        if provider is None:
            logger.warning(
                "no summary provider available camera=%s, skipping", cam.id
            )
            return

        prompt = self._build_prompt(
            cam=cam,
            kind=kind,
            window_start=window_start,
            window_end=window_end,
            obs_rows=obs_rows,
            tx_rows=tx_rows,
        )

        text = await call_text(
            provider=provider,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=int(cam.summary_max_tokens or 400),
        )
        if not text:
            logger.warning(
                "summary VLM returned empty camera=%s kind=%s", cam.id, kind
            )
            return

        people, plates, obj_counts = self._aggregate_facts(obs_rows)
        summary_id = await self._store_summary(
            cam=cam,
            kind=kind,
            window_start=window_start,
            window_end=window_end,
            provider_name=provider.name,
            trigger_reason=trigger_reason,
            text=text.strip(),
            obs_rows=obs_rows,
            tx_rows=tx_rows,
            people=people,
            plates=plates,
            obj_counts=obj_counts,
        )
        if summary_id and self._broadcast:
            try:
                await self._broadcast(
                    {
                        "type": "summary_created",
                        "id": str(summary_id),
                        "camera_id": str(cam.id),
                        "kind": kind,
                        "started_at": window_start.isoformat(),
                        "ended_at": window_end.isoformat(),
                        "text": text.strip(),
                    }
                )
            except Exception:
                logger.exception("summary WS broadcast failed")

    async def _resolve_summary_provider(self, cam: Camera) -> Provider | None:
        # Per-camera summary provider, then per-camera VLM provider, then
        # the system default. Matches the precedence the user asked for.
        for pid in (cam.summary_provider_id, cam.vlm_provider_id):
            if not pid:
                continue
            try:
                async with async_session() as db:
                    p = await db.get(Provider, pid)
                    if p:
                        db.expunge(p)
                        return p
            except Exception:
                logger.exception("provider lookup failed")
        return await get_active_provider()

    def _build_prompt(
        self,
        cam: Camera,
        kind: str,
        window_start: datetime,
        window_end: datetime,
        obs_rows: list[Observation],
        tx_rows: list[Transcript],
    ) -> str:
        lines: list[str] = []
        cam_bits = [b for b in (cam.name, cam.location_label) if b]
        lines.append(
            f"Camera: {' / '.join(cam_bits) if cam_bits else 'unnamed'}."
        )
        lines.append(
            f"Window: {window_start.isoformat()} -> {window_end.isoformat()}"
            f" ({int((window_end - window_start).total_seconds())}s, kind={kind})."
        )

        if obs_rows:
            lines.append(f"\nObservations ({len(obs_rows)}):")
            for o in obs_rows[:60]:
                t = o.started_at.strftime("%H:%M:%S")
                desc = (o.vlm_description or "").strip().replace("\n", " ")
                if not desc and o.object_detections:
                    objs = (o.object_detections or {}).get("objects", []) or []
                    desc = ", ".join(
                        f"{d.get('label')}({d.get('confidence', 0):.0%})"
                        for d in objs[:5]
                    )
                if desc:
                    lines.append(f"- {t} {desc[:200]}")
            if len(obs_rows) > 60:
                lines.append(f"- (+{len(obs_rows) - 60} more)")

        if tx_rows:
            lines.append(f"\nTranscripts ({len(tx_rows)}):")
            for t in tx_rows[:60]:
                ts = t.started_at.strftime("%H:%M:%S")
                lines.append(f'- {ts} "{(t.text or "").strip()[:200]}"')
            if len(tx_rows) > 60:
                lines.append(f"- (+{len(tx_rows) - 60} more)")

        people, plates, _ = self._aggregate_facts(obs_rows)
        if people:
            lines.append("\nPeople seen:")
            for p in people:
                lines.append(
                    f"- {p['name']}: {p['sightings']}x"
                    f" ({p['first_seen']} -> {p['last_seen']})"
                )
        if plates:
            lines.append(f"\nPlates: {', '.join(plates)}.")

        lines.append(
            "\nWrite a 2-4 sentence narrative recap. Use the people and"
            " plate facts as ground truth. Be specific about times."
        )
        return "\n".join(lines)

    @staticmethod
    def _aggregate_facts(obs_rows: list[Observation]):
        people_acc: dict[str, dict[str, Any]] = {}
        plates: set[str] = set()
        obj_counts: Counter[str] = Counter()

        for o in obs_rows:
            if o.person_detections:
                faces = (o.person_detections or {}).get("faces", []) or []
                for f in faces:
                    name = f.get("person_name") or (
                        f"unknown_{(f.get('cluster_id') or '')[:8]}"
                        if f.get("cluster_id")
                        else "unknown"
                    )
                    p = people_acc.setdefault(
                        name,
                        {
                            "name": name,
                            "sightings": 0,
                            "first_seen": o.started_at.strftime("%H:%M:%S"),
                            "last_seen": o.started_at.strftime("%H:%M:%S"),
                        },
                    )
                    p["sightings"] += 1
                    p["last_seen"] = o.started_at.strftime("%H:%M:%S")
            if o.object_detections:
                objs = (o.object_detections or {}).get("objects", []) or []
                for d in objs:
                    label = d.get("label")
                    if label == "license_plate" and d.get("plate_text"):
                        plates.add(d["plate_text"])
                    elif label:
                        obj_counts[label] += 1

        people = sorted(people_acc.values(), key=lambda p: -p["sightings"])
        return people, sorted(plates), dict(obj_counts)

    async def _store_summary(
        self,
        cam: Camera,
        kind: str,
        window_start: datetime,
        window_end: datetime,
        provider_name: str,
        trigger_reason: str,
        text: str,
        obs_rows: list[Observation],
        tx_rows: list[Transcript],
        people: list[dict],
        plates: list[str],
        obj_counts: dict[str, int],
    ) -> uuid.UUID | None:
        try:
            embed_provider = await get_embedding_provider()
            embedding = await generate_embedding(text, embed_provider)
        except Exception:
            logger.debug("summary embedding failed", exc_info=True)
            embedding = None

        row = Summary(
            camera_id=cam.id,
            kind=kind,
            started_at=window_start,
            ended_at=window_end,
            provider_name=provider_name,
            trigger_reason=trigger_reason,
            summary_text=text,
            source_observation_ids=[str(o.id) for o in obs_rows],
            source_transcript_ids=[str(t.id) for t in tx_rows],
            people_seen=people or None,
            plates_seen=plates or None,
            object_counts=obj_counts or None,
            embedding=embedding,
        )
        try:
            async with async_session() as db:
                db.add(row)
                await db.commit()
                await db.refresh(row)
            logger.info(
                "summary stored camera=%s kind=%s id=%s len=%d",
                cam.id, kind, row.id, len(text),
            )
            return row.id
        except Exception:
            logger.exception("summary insert failed camera=%s", cam.id)
            return None
