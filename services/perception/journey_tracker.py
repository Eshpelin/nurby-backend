"""Journey tracker. cross-camera story stitching.

Subscribes to incident events. When an incident opens or updates
for a subject (named person OR face cluster), the tracker links it
into an open journey for the same subject — across all cameras —
or opens a new one.

Journey lifecycle.
- Open. first incident for a subject with no active journey.
- Append. another incident for the same subject within
  ``journey_idle_seconds`` of last_seen_at. Adds a segment for a
  new camera, or advances the last_seen_at of an existing segment.
- Finalize. idle past the window. Background tick closes the row
  and asks the summary VLM for a 2-3 sentence narrative recap.

The tracker is called inline from IncidentTracker.assign_incident
so the journey link lands in the same DB session as the incident
row. The finalizer is a separate asyncio loop alongside the
existing IncidentFinalizer and ConversationFinalizer.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.ws import broadcast as ws_broadcast
from services.perception.text_llm import call_text
from services.perception.token_budget import resolve_output_cap
from services.perception.vlm import get_active_provider
from services.search.embeddings import generate_embedding, get_embedding_provider
from shared.app_settings import get_setting
from shared.database import async_session
from shared.models import Camera, Incident, Journey, Provider

logger = logging.getLogger("nurby.perception.journey")


# Default idle window for the journey to stay open across cameras.
# Larger than the per-camera incident idle window because a subject
# can spend minutes between cameras (walking across a property).
# User-tunable via the ``journey_idle_seconds`` AppSetting.
JOURNEY_IDLE_SECONDS_DEFAULT = 300  # 5 minutes


async def _journey_idle_seconds() -> int:
    try:
        raw = await get_setting("journey_idle_seconds", JOURNEY_IDLE_SECONDS_DEFAULT)
        return max(60, min(86400, int(raw)))
    except Exception:
        return JOURNEY_IDLE_SECONDS_DEFAULT


JOURNEY_SUMMARY_PROMPT = (
    "You are a security camera analyst. You receive a time-ordered"
    " trace of one subject moving across multiple cameras on a single"
    " property. Write a 2-3 sentence narrative recap. Be specific"
    " about which camera they appeared on, in what order, and any"
    " time gaps that suggest where they went off-camera. Use the"
    " identity / location facts as ground truth. If nothing notable"
    " happened, return SKIP."
)


# ---- assignment ----------------------------------------------------------


_TRACKABLE_KINDS = {"person", "cluster"}


async def assign_journey(
    db: AsyncSession,
    incident: Incident,
    camera: Camera,
) -> uuid.UUID | None:
    """Link an incident into a journey. Called by IncidentTracker
    after the incident row is created (or appended). Persists the
    journey membership and broadcasts WS events.

    Returns the linked journey id or None when journey tracking does
    not apply (motion / object signatures, or no compatible subject).
    """
    if incident.signature_kind not in _TRACKABLE_KINDS:
        return None
    idle_s = await _journey_idle_seconds()
    cutoff = incident.last_seen_at - timedelta(seconds=idle_s)
    existing = (
        await db.execute(
            select(Journey)
            .where(Journey.finalized.is_(False))
            .where(Journey.subject_kind == incident.signature_kind)
            .where(Journey.subject_key == incident.signature_key)
            .where(Journey.last_seen_at >= cutoff)
            .order_by(Journey.last_seen_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if existing is not None:
        _append_segment(existing, incident, camera)
        existing.last_seen_at = max(
            existing.last_seen_at, incident.last_seen_at
        )
        existing.incidents_count = (existing.incidents_count or 0) + 1
        existing.cameras_seen_count = _unique_cameras(existing.segments)
        existing.transitions = _compute_transitions(existing.segments)
        try:
            asyncio.create_task(
                _broadcast(
                    "journey_updated",
                    existing,
                )
            )
        except RuntimeError:
            pass
        return existing.id

    new_segment = _segment(incident, camera)
    new_journey = Journey(
        subject_kind=incident.signature_kind,
        subject_key=incident.signature_key,
        started_at=incident.started_at,
        last_seen_at=incident.last_seen_at,
        ended_at=None,
        finalized=False,
        segments=[new_segment],
        transitions=[],
        cameras_seen_count=1,
        incidents_count=1,
    )
    db.add(new_journey)
    await db.flush()
    try:
        asyncio.create_task(_broadcast("journey_opened", new_journey))
    except RuntimeError:
        pass
    # Guardian arrival alert. Fire-and-forget, fully isolated.
    _fire_guardian_event(
        "arrived", new_journey.subject_kind, new_journey.subject_key, camera.id
    )
    return new_journey.id


def _fire_guardian_event(kind: str, subject_kind, subject_key, camera_id) -> None:
    """Best-effort fan-out to Guardian. Never raises into the pipeline."""
    try:
        from services.guardian.lifecycle import notify_journey_event

        asyncio.create_task(
            notify_journey_event(kind, subject_kind, subject_key, camera_id)
        )
    except RuntimeError:
        pass  # no running loop (sync context); skip
    except Exception:  # noqa: BLE001
        pass


def _segment(incident: Incident, camera: Camera) -> dict:
    return {
        "camera_id": str(camera.id),
        "camera_name": camera.name,
        "location_label": camera.location_label,
        "incident_id": str(incident.id),
        "started_at": incident.started_at.isoformat(),
        "last_seen_at": incident.last_seen_at.isoformat(),
        "occurrence_count": incident.occurrence_count or 1,
        "peak_observation_id": (
            str(incident.peak_observation_id)
            if incident.peak_observation_id
            else None
        ),
    }


def _append_segment(journey: Journey, incident: Incident, camera: Camera) -> None:
    """Either extend the trailing segment in place (same camera) or
    append a new segment for a different camera."""
    segs = list(journey.segments or [])
    if segs:
        last = segs[-1]
        if last.get("camera_id") == str(camera.id):
            last["last_seen_at"] = incident.last_seen_at.isoformat()
            last["occurrence_count"] = (
                int(last.get("occurrence_count") or 0)
                + max(0, (incident.occurrence_count or 1) - int(last.get("occurrence_count") or 0))
            )
            # Always overwrite with the latest incident id since incident
            # itself may have been merged forward.
            last["incident_id"] = str(incident.id)
            journey.segments = segs
            return
    segs.append(_segment(incident, camera))
    journey.segments = segs


def _unique_cameras(segments: Any) -> int:
    if not segments:
        return 0
    return len({s.get("camera_id") for s in segments if s.get("camera_id")})


def _compute_transitions(segments: Any) -> list[dict]:
    """Compute camera-to-camera transitions from successive segments
    on different cameras."""
    out: list[dict] = []
    if not segments or len(segments) < 2:
        return out
    for i in range(1, len(segments)):
        a = segments[i - 1]
        b = segments[i]
        if not a.get("camera_id") or not b.get("camera_id"):
            continue
        if a["camera_id"] == b["camera_id"]:
            continue
        try:
            a_end = datetime.fromisoformat(a["last_seen_at"])
            b_start = datetime.fromisoformat(b["started_at"])
        except (KeyError, ValueError):
            continue
        gap = max(0, int((b_start - a_end).total_seconds()))
        out.append(
            {
                "from_camera_id": a["camera_id"],
                "from_camera_name": a.get("camera_name"),
                "to_camera_id": b["camera_id"],
                "to_camera_name": b.get("camera_name"),
                "gap_seconds": gap,
                "ts": b["started_at"],
            }
        )
    return out


# ---- finalizer -----------------------------------------------------------


class JourneyFinalizer:
    TICK_SECONDS = 15

    def __init__(self, broadcast_fn=ws_broadcast) -> None:
        self._broadcast = broadcast_fn
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("journey finalizer started")
        try:
            while not self._stopping.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("journey finalizer tick failed")
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.TICK_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("journey finalizer stopped")

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        idle_s = await _journey_idle_seconds()
        async with async_session() as db:
            open_rows = (
                await db.execute(
                    select(Journey)
                    .where(Journey.finalized.is_(False))
                    .order_by(Journey.last_seen_at.asc())
                    .limit(50)
                )
            ).scalars().all()
        for j in open_rows:
            quiet_for = (now - j.last_seen_at).total_seconds()
            if quiet_for < idle_s:
                continue
            await self._finalize(j.id)

    async def _finalize(self, jid: uuid.UUID) -> None:
        async with async_session() as db:
            j = await db.get(Journey, jid)
            if j is None or j.finalized:
                return
            ended_at = j.last_seen_at
            j.finalized = True
            j.ended_at = ended_at
            await db.commit()
            await db.refresh(j)

        # Guardian departure alert. Fire-and-forget after the row is closed.
        _dep_cam = None
        if j.segments:
            _dep_cam = j.segments[-1].get("camera_id") or j.segments[0].get("camera_id")
        _fire_guardian_event("departed", j.subject_kind, j.subject_key, _dep_cam)

        # Skip summary for short / single-camera journeys. The
        # incident summary covers those well enough.
        if (j.cameras_seen_count or 0) < 2 and (j.incidents_count or 0) < 2:
            try:
                await self._broadcast(
                    {
                        "type": "journey_finalized",
                        "journey_id": str(jid),
                        "subject_kind": j.subject_kind,
                        "subject_key": j.subject_key,
                        "summary_text": None,
                        "skipped": "single_camera",
                    }
                )
            except Exception:
                logger.debug("journey_finalized broadcast failed", exc_info=True)
            return

        provider = await self._resolve_provider()
        summary_text: str | None = None
        if provider is not None:
            try:
                summary_text = await self._build_summary(provider, j)
                if summary_text and summary_text.strip().upper().startswith("SKIP"):
                    summary_text = None
                if summary_text:
                    await self._patch_summary(jid, summary_text.strip(), provider.name)
            except Exception:
                logger.exception("journey summary failed jid=%s", jid)

        try:
            await self._broadcast(
                {
                    "type": "journey_finalized",
                    "journey_id": str(jid),
                    "subject_kind": j.subject_kind,
                    "subject_key": j.subject_key,
                    "cameras_seen_count": j.cameras_seen_count,
                    "incidents_count": j.incidents_count,
                    "summary_text": summary_text,
                }
            )
        except Exception:
            logger.debug("journey_finalized broadcast failed", exc_info=True)

    async def _resolve_provider(self) -> Provider | None:
        return await get_active_provider()

    async def _build_summary(self, provider: Provider, j: Journey) -> str | None:
        segs = j.segments or []
        subject = j.subject_key if j.subject_kind == "person" else (
            f"recurring stranger {j.subject_key[:8]}"
            if j.subject_key
            else "unknown person"
        )
        lines = [
            f"Subject. {subject}.",
            f"Window. {j.started_at.isoformat()} -> {j.last_seen_at.isoformat()}"
            f" ({int((j.last_seen_at - j.started_at).total_seconds())}s).",
            f"Cameras seen. {j.cameras_seen_count}. Incidents. {j.incidents_count}.",
            "",
            "Segments in order:",
        ]
        for i, s in enumerate(segs):
            cam = s.get("camera_name") or "unnamed"
            loc = s.get("location_label")
            cam_str = f"{cam}" + (f" ({loc})" if loc else "")
            try:
                s_start = datetime.fromisoformat(s["started_at"])
                s_end = datetime.fromisoformat(s["last_seen_at"])
                dur = max(0, int((s_end - s_start).total_seconds()))
            except (KeyError, ValueError):
                dur = 0
            lines.append(
                f"{i + 1}. {cam_str}. {s.get('started_at', '?')} for {dur}s"
                f" ({s.get('occurrence_count') or 1} observations)."
            )

        transitions = j.transitions or []
        if transitions:
            lines.append("")
            lines.append("Transitions:")
            for t in transitions:
                lines.append(
                    f"- {t.get('from_camera_name') or t.get('from_camera_id')}"
                    f" -> {t.get('to_camera_name') or t.get('to_camera_id')}"
                    f" with a {t.get('gap_seconds')}s gap."
                )

        lines.append("")
        lines.append(
            "Write a 2-3 sentence narrative recap. Use camera names"
            " literally. Call out long off-camera gaps as such."
        )
        prompt = "\n".join(lines)
        max_out = resolve_output_cap(getattr(provider, "max_output_tokens", None))
        return await call_text(
            provider=provider,
            system_prompt=JOURNEY_SUMMARY_PROMPT,
            user_prompt=prompt,
            max_tokens=max_out,
        )

    async def _patch_summary(
        self, jid: uuid.UUID, summary_text: str, provider_name: str
    ) -> None:
        try:
            embed_provider = await get_embedding_provider()
            embedding = await generate_embedding(summary_text, embed_provider)
        except Exception:
            embedding = None
        try:
            async with async_session() as db:
                row = await db.get(Journey, jid)
                if row is None:
                    return
                row.summary_text = summary_text
                row.summary_provider_name = provider_name
                if embedding is not None:
                    row.embedding = embedding
                await db.commit()
        except Exception:
            logger.exception("journey summary patch failed jid=%s", jid)


# ---- WS payload helpers --------------------------------------------------


async def _broadcast(kind: str, j: Journey) -> None:
    try:
        await ws_broadcast(
            {
                "type": kind,
                "journey_id": str(j.id),
                "subject_kind": j.subject_kind,
                "subject_key": j.subject_key,
                "started_at": j.started_at.isoformat(),
                "last_seen_at": j.last_seen_at.isoformat(),
                "cameras_seen_count": j.cameras_seen_count,
                "incidents_count": j.incidents_count,
            }
        )
    except Exception:
        logger.debug("%s broadcast failed", kind, exc_info=True)
