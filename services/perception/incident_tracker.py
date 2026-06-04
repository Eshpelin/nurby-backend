"""Incident tracking. assignment + finalizer.

Mirrors the frontend coalescer's grouping logic but persists rows
so the dashboard can show a stable id, push WS events for live
append, and run a final summary VLM call when an incident closes.

Pipeline calls :func:`assign_incident` synchronously inside the
observation insert path. The :class:`IncidentFinalizer` worker
runs alongside the perception pipeline and closes incidents that
have been quiet beyond their camera's ``incident_idle_seconds``.
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
from shared.database import async_session
from shared.models import Camera, Incident, Observation, Provider

logger = logging.getLogger("nurby.perception.incident")


INCIDENT_SUMMARY_PROMPT = (
    "You are a security camera analyst. You are given a series of"
    " observations of the same person or object on one camera that"
    " happened over a short window. Write a single concise sentence"
    " summarizing what happened across the occurrences. Use identity,"
    " plate, and location facts as ground truth. If nothing notable"
    " happened, return SKIP."
)


# ---- signature -----------------------------------------------------------


# Subjects worth tracking as discrete incidents. everything else (furniture,
# appliances, tableware, plants) is ambient and rolls into motion instead of
# becoming a "Clock seen 4x" card. Mirrors the frontend INTERESTING_OBJECTS.
INTERESTING_INCIDENT_LABELS = {
    "person",
    "car", "truck", "bus", "motorcycle", "bicycle", "van",
    "dog", "cat", "bird", "horse",
    "backpack", "handbag", "suitcase", "package", "box",
    "knife", "gun", "fire",
}


def compute_signature(
    person_detections: dict | None,
    object_detections: dict | None,
) -> tuple[str, str]:
    """Return (signature_kind, signature_key) for an observation.

    Priority. named persons > recurring unknown clusters > unknown
    faces > top YOLO labels > motion. Mirrors the frontend coalescer
    so the two layers agree on what counts as 'the same thing'.
    """
    faces = (person_detections or {}).get("faces") or []
    named = sorted(
        {f.get("person_name") for f in faces if f.get("person_name")}
    )
    if named:
        return "person", ",".join(named)
    clusters = sorted(
        {f.get("cluster_id") for f in faces if f.get("cluster_id")}
    )
    if clusters:
        return "cluster", ",".join(clusters)
    if faces:
        return "unknown", "unknown"
    objs = (object_detections or {}).get("objects") or []
    # Only meaningful subjects form an "object" incident. a clock or couch
    # seen N times is noise, not an event.
    labels = sorted(
        {
            d.get("label")
            for d in objs
            if d.get("label") in INTERESTING_INCIDENT_LABELS
        }
    )
    if labels:
        return "object", ",".join(labels[:3])
    # Inert-only or empty scene. group as ambient motion, not a subject.
    return "motion", "motion"


# ---- assignment ----------------------------------------------------------


async def assign_incident(
    db: AsyncSession,
    cam: Camera,
    observation: Observation,
) -> uuid.UUID | None:
    """Find an open incident for this signature on the camera within
    the camera's idle window, and either append or open a new one.
    Returns the linked incident id (or None when tracking is off).

    Runs inside the same session as the observation insert so the
    observation.incident_id assignment lands atomically with the
    incident's occurrence_count bump.
    """
    if not getattr(cam, "incident_tracking_enabled", False):
        return None
    kind, key = compute_signature(
        observation.person_detections, observation.object_detections
    )
    idle_s = max(30, int(getattr(cam, "incident_idle_seconds", 600) or 600))
    cutoff = observation.started_at - timedelta(seconds=idle_s)

    existing = (
        await db.execute(
            select(Incident)
            .where(Incident.camera_id == cam.id)
            .where(Incident.finalized.is_(False))
            .where(Incident.signature_kind == kind)
            .where(Incident.signature_key == key)
            .where(Incident.last_seen_at >= cutoff)
            .order_by(Incident.last_seen_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if existing is not None:
        existing.last_seen_at = observation.started_at
        existing.occurrence_count = (existing.occurrence_count or 0) + 1
        ids = list(existing.observation_ids or [])
        ids.append(str(observation.id))
        existing.observation_ids = ids
        thumbs = list(existing.thumbnails or [])
        if observation.thumbnail_path:
            thumbs.append(
                {
                    "obs_id": str(observation.id),
                    "path": observation.thumbnail_path,
                    "ts": observation.started_at.isoformat(),
                }
            )
            # Cap denormalized thumbnails so the JSON column stays sane
            # even on a long-running incident.
            if len(thumbs) > 24:
                thumbs = thumbs[-24:]
        existing.thumbnails = thumbs
        # Update the journey link for this incident. If a journey is
        # already attached the call advances the segment for this
        # camera; if not, it stitches into a cross-camera journey
        # when a sibling exists for the same subject.
        try:
            from services.perception.journey_tracker import assign_journey

            jid = await assign_journey(db, existing, cam)
            if jid is not None and existing.journey_id != jid:
                existing.journey_id = jid
        except Exception:
            logger.exception("journey assignment failed inc=%s", existing.id)
        # Fire-and-forget WS append. The dashboard refetches on this
        # event to splice the new occurrence into the live card.
        try:
            asyncio.create_task(_broadcast_updated(existing))
        except RuntimeError:
            pass
        return existing.id

    new_inc = Incident(
        camera_id=cam.id,
        signature_kind=kind,
        signature_key=key,
        started_at=observation.started_at,
        last_seen_at=observation.started_at,
        ended_at=None,
        finalized=False,
        occurrence_count=1,
        peak_observation_id=observation.id,
        observation_ids=[str(observation.id)],
        thumbnails=(
            [
                {
                    "obs_id": str(observation.id),
                    "path": observation.thumbnail_path,
                    "ts": observation.started_at.isoformat(),
                }
            ]
            if observation.thumbnail_path
            else None
        ),
    )
    db.add(new_inc)
    await db.flush()
    # Stitch into a journey when one is already open for the same
    # subject across any camera. Otherwise opens a fresh journey row.
    try:
        from services.perception.journey_tracker import assign_journey

        jid = await assign_journey(db, new_inc, cam)
        if jid is not None:
            new_inc.journey_id = jid
    except Exception:
        logger.exception("journey assignment failed new inc=%s", new_inc.id)
    try:
        asyncio.create_task(_broadcast_opened(new_inc))
    except RuntimeError:
        pass
    return new_inc.id


# ---- finalizer worker ----------------------------------------------------


class IncidentFinalizer:
    """Closes idle incidents and writes a final VLM summary.

    Tick cadence is coarse (10s). Each tick scans up to 50 open
    incidents whose last_seen_at is past the camera's idle window.
    Closes them, optionally calls call_text for a summary, broadcasts
    incident_finalized.
    """

    TICK_SECONDS = 10

    def __init__(self, broadcast_fn=ws_broadcast) -> None:
        self._broadcast = broadcast_fn
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("incident finalizer started")
        try:
            while not self._stopping.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("incident finalizer tick failed")
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.TICK_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("incident finalizer stopped")

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with async_session() as db:
            cams = (await db.execute(select(Camera))).scalars().all()
            cam_by_id = {c.id: c for c in cams}
            open_rows = (
                await db.execute(
                    select(Incident)
                    .where(Incident.finalized.is_(False))
                    .order_by(Incident.last_seen_at.asc())
                    .limit(50)
                )
            ).scalars().all()

        for inc in open_rows:
            cam = cam_by_id.get(inc.camera_id)
            if cam is None:
                await self._mark_finalized(inc.id, inc.last_seen_at)
                continue
            idle_s = max(30, int(cam.incident_idle_seconds or 600))
            quiet_for = (now - inc.last_seen_at).total_seconds()
            if quiet_for < idle_s:
                continue
            await self._finalize(cam, inc.id)

    async def _mark_finalized(self, inc_id: uuid.UUID, ended_at: datetime) -> None:
        try:
            async with async_session() as db:
                row = await db.get(Incident, inc_id)
                if row is None:
                    return
                row.finalized = True
                row.ended_at = ended_at
                await db.commit()
        except Exception:
            logger.exception("incident finalize failed inc=%s", inc_id)

    async def _finalize(self, cam: Camera, inc_id: uuid.UUID) -> None:
        async with async_session() as db:
            inc = await db.get(Incident, inc_id)
            if inc is None or inc.finalized:
                return
            obs_ids = inc.observation_ids or []
            obs_uuids = []
            for x in obs_ids:
                try:
                    obs_uuids.append(uuid.UUID(str(x)))
                except (TypeError, ValueError):
                    continue
            obs_rows = []
            if obs_uuids:
                obs_rows = list(
                    (
                        await db.execute(
                            select(Observation).where(Observation.id.in_(obs_uuids))
                        )
                    ).scalars().all()
                )
                obs_rows.sort(key=lambda r: r.started_at)

        # Mark closed first so a slow VLM call doesn't keep the row open.
        ended_at = inc.last_seen_at
        await self._mark_finalized(inc_id, ended_at)

        summary_text: str | None = None
        if obs_rows:
            provider = await self._resolve_provider(cam)
            if provider is not None:
                summary_text = await self._build_summary(provider, cam, inc, obs_rows)
                if summary_text and summary_text.strip().upper().startswith("SKIP"):
                    summary_text = None
                if summary_text:
                    await self._patch_summary(
                        inc_id=inc_id,
                        summary_text=summary_text.strip(),
                        provider_name=provider.name,
                    )

        try:
            await self._broadcast(
                {
                    "type": "incident_finalized",
                    "incident_id": str(inc_id),
                    "camera_id": str(cam.id),
                    "ended_at": ended_at.isoformat(),
                    "occurrence_count": inc.occurrence_count,
                    "summary_text": summary_text,
                }
            )
        except Exception:
            logger.debug("incident_finalized broadcast failed", exc_info=True)

    async def _resolve_provider(self, cam: Camera) -> Provider | None:
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
                logger.exception("provider lookup failed for incident summary")
        return await get_active_provider()

    async def _build_summary(
        self,
        provider: Provider,
        cam: Camera,
        inc: Incident,
        obs_rows: list[Observation],
    ) -> str | None:
        cam_bits = [b for b in (cam.name, cam.location_label) if b]
        lines = [
            f"Camera: {' / '.join(cam_bits) if cam_bits else 'unnamed'}.",
            f"Incident kind: {inc.signature_kind} ({inc.signature_key}).",
            f"Window: {inc.started_at.isoformat()} -> {inc.last_seen_at.isoformat()}"
            f" ({int((inc.last_seen_at - inc.started_at).total_seconds())}s,"
            f" {inc.occurrence_count} occurrences).",
            "",
            "Occurrences:",
        ]
        for o in obs_rows[:30]:
            t = o.started_at.strftime("%H:%M:%S")
            desc = (o.vlm_description or "").strip().replace("\n", " ")[:200]
            if desc:
                lines.append(f"- {t} {desc}")
        if len(obs_rows) > 30:
            lines.append(f"- (+{len(obs_rows) - 30} more)")
        lines.append("")
        lines.append(
            "Write a single concise sentence summarizing what happened"
            " across these occurrences. If nothing notable, return SKIP."
        )
        prompt = "\n".join(lines)
        max_out = resolve_output_cap(
            cam.summary_max_tokens,
            getattr(provider, "max_output_tokens", None),
        )
        return await call_text(
            provider=provider,
            system_prompt=INCIDENT_SUMMARY_PROMPT,
            user_prompt=prompt,
            max_tokens=max_out,
            camera_id=str(cam.id),
        )

    async def _patch_summary(
        self,
        inc_id: uuid.UUID,
        summary_text: str,
        provider_name: str,
    ) -> None:
        try:
            embed_provider = await get_embedding_provider()
            embedding = await generate_embedding(summary_text, embed_provider)
        except Exception:
            embedding = None
        try:
            async with async_session() as db:
                row = await db.get(Incident, inc_id)
                if row is None:
                    return
                row.summary_text = summary_text
                row.summary_provider_name = provider_name
                if embedding is not None:
                    row.embedding = embedding
                await db.commit()
        except Exception:
            logger.exception("incident summary patch failed inc=%s", inc_id)


# ---- WS broadcasts -------------------------------------------------------


async def _broadcast_opened(inc: Incident) -> None:
    payload = _ws_payload("incident_opened", inc)
    try:
        await ws_broadcast(payload)
    except Exception:
        logger.debug("incident_opened broadcast failed", exc_info=True)


async def _broadcast_updated(inc: Incident) -> None:
    payload = _ws_payload("incident_updated", inc)
    try:
        await ws_broadcast(payload)
    except Exception:
        logger.debug("incident_updated broadcast failed", exc_info=True)


def _ws_payload(kind: str, inc: Incident) -> dict[str, Any]:
    return {
        "type": kind,
        "incident_id": str(inc.id),
        "camera_id": str(inc.camera_id),
        "signature_kind": inc.signature_kind,
        "signature_key": inc.signature_key,
        "started_at": inc.started_at.isoformat(),
        "last_seen_at": inc.last_seen_at.isoformat(),
        "occurrence_count": inc.occurrence_count,
    }
