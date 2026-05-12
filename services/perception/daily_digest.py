"""Daily household digest builder.

Aggregates the last 24h across all sources into one morning
summary. Inputs.

- Observations (counts + named persons + clusters)
- Incidents (open + finalized)
- Journeys (cross-camera narratives)
- Audio detections (clap, baby_cry, glass, alarm, bark, gunshot)
- Conversations (where audio is on)

Output. one DailyDigest row with a free-form ``summary_text``
narrative plus a structured ``facts`` dict the UI uses to render
deterministic bullet lists.

Scheduler. fires hourly from the perception process; only writes
a row when the current local hour matches the configured
``daily_digest_hour`` AppSetting and no row exists for today's
window already.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
from shared.models import (
    AudioDetection,
    Camera,
    Conversation,
    DailyDigest,
    Incident,
    Journey,
    Observation,
    Provider,
)

logger = logging.getLogger("nurby.perception.daily_digest")


DAILY_SYSTEM_PROMPT = (
    "You are a household security camera analyst. You receive a"
    " 24-hour roll-up of activity across multiple cameras. Output a"
    " short morning briefing as a bullet list. Each bullet is one"
    " line, factual, with a count or timestamp when relevant. Mention"
    " visitors by name when known, plate by string, unknowns as"
    ' "unknown person at <camera> <time>". If nothing notable, return'
    ' the single bullet "Quiet night." Never speculate beyond the'
    " evidence."
)


class DailyDigestScheduler:
    """Hourly tick. fires the digest builder when the local hour
    matches the configured target and we haven't already generated
    one today.

    Defaults. enabled=true, hour=7 (7am local time).
    """

    TICK_SECONDS = 600  # 10 min. fine resolution for hour-changes.

    def __init__(self, broadcast_fn=ws_broadcast) -> None:
        self._broadcast = broadcast_fn
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("daily digest scheduler started")
        try:
            while not self._stopping.is_set():
                try:
                    await self._maybe_run()
                except Exception:
                    logger.exception("daily digest tick failed")
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.TICK_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("daily digest scheduler stopped")

    async def _maybe_run(self) -> None:
        enabled = bool(await get_setting("daily_digest_enabled", True))
        if not enabled:
            return
        hour = int(await get_setting("daily_digest_hour", 7))
        hour = max(0, min(23, hour))
        # Pull the system timezone setting, falling back to the
        # process locale. Per-camera timezones still drive timestamp
        # rendering everywhere else; this one anchors the household
        # digest hour.
        tz_name = await get_setting("system_timezone", None)
        tz = None
        if tz_name:
            try:
                tz = ZoneInfo(str(tz_name))
            except ZoneInfoNotFoundError:
                tz = None
        now_local = datetime.now(tz).astimezone() if tz else datetime.now().astimezone()
        if now_local.hour != hour:
            return
        # Window. previous day same-hour through now.
        window_end = now_local
        window_start = window_end - timedelta(hours=24)
        if await self._already_generated_for(window_end):
            return
        await build_daily_digest(
            window_start=window_start.astimezone(timezone.utc),
            window_end=window_end.astimezone(timezone.utc),
            broadcast_fn=self._broadcast,
        )

    async def _already_generated_for(self, anchor_local: datetime) -> bool:
        # Have we written a digest with generated_at within the last
        # 23h? Keeps the once-per-day promise even if the scheduler
        # restarts.
        cutoff = anchor_local.astimezone(timezone.utc) - timedelta(hours=23)
        async with async_session() as db:
            row = (
                await db.execute(
                    select(DailyDigest)
                    .where(DailyDigest.generated_at >= cutoff)
                    .order_by(DailyDigest.generated_at.desc())
                    .limit(1)
                )
            ).scalars().first()
        return row is not None


# ---- build pipeline ------------------------------------------------------


async def build_daily_digest(
    window_start: datetime,
    window_end: datetime,
    broadcast_fn=ws_broadcast,
) -> DailyDigest | None:
    """Pull all sources for the window, build prompt, call VLM,
    insert row. Returns the persisted row on success.
    """
    facts = await _collect_facts(window_start, window_end)
    provider = await _resolve_provider()

    summary_text: str | None = None
    if provider is not None:
        prompt = _build_prompt(facts, window_start, window_end)
        max_out = resolve_output_cap(
            getattr(provider, "max_output_tokens", None)
        ) or 600
        try:
            summary_text = await call_text(
                provider=provider,
                system_prompt=DAILY_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=max_out,
            )
            if summary_text:
                summary_text = summary_text.strip()
        except Exception:
            logger.exception("daily digest VLM call failed")

    embedding = None
    if summary_text:
        try:
            ep = await get_embedding_provider()
            embedding = await generate_embedding(summary_text, ep)
        except Exception:
            embedding = None

    row = DailyDigest(
        window_start=window_start,
        window_end=window_end,
        provider_name=provider.name if provider else None,
        summary_text=summary_text,
        facts=facts,
        embedding=embedding,
    )
    try:
        async with async_session() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
    except Exception:
        logger.exception("daily digest insert failed")
        return None

    try:
        await broadcast_fn(
            {
                "type": "daily_digest_ready",
                "id": str(row.id),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "summary_text": summary_text,
                "facts": facts,
            }
        )
    except Exception:
        logger.debug("daily_digest_ready broadcast failed", exc_info=True)
    return row


async def _resolve_provider() -> Provider | None:
    # Optional override. fall back to the system default.
    pid = await get_setting("daily_digest_provider_id", None)
    if pid:
        import uuid as _uuid

        try:
            async with async_session() as db:
                p = await db.get(Provider, _uuid.UUID(str(pid)))
                if p:
                    db.expunge(p)
                    return p
        except Exception:
            logger.debug("daily digest provider lookup failed", exc_info=True)
    return await get_active_provider()


async def _collect_facts(
    window_start: datetime, window_end: datetime
) -> dict[str, Any]:
    """Walk every signal table in the window and assemble the
    structured fact dict. All counts + lists. The LLM uses these as
    ground truth; the UI can also render them deterministically when
    the LLM call fails or is offline."""
    facts: dict[str, Any] = {
        "visitors": [],
        "unknown_visitors": 0,
        "incidents_count": 0,
        "journeys_count": 0,
        "conversations_count": 0,
        "cameras_active": [],
        "audio_events": {},
        "audio_event_samples": {},
        "packages": 0,
        "vehicles": 0,
        "rule_fires": [],
    }
    async with async_session() as db:
        # Cameras for name lookup.
        cams = (await db.execute(select(Camera))).scalars().all()
        cam_name_by_id = {str(c.id): c.name or "unnamed" for c in cams}

        # Observations. count named persons + clusters; track top
        # objects (packages, vehicles).
        obs_rows = (
            await db.execute(
                select(Observation.camera_id, Observation.started_at,
                       Observation.person_detections, Observation.object_detections)
                .where(Observation.started_at >= window_start)
                .where(Observation.started_at <= window_end)
            )
        ).all()
        named: dict[str, int] = {}
        named_first: dict[str, str] = {}
        named_last: dict[str, str] = {}
        named_cams: dict[str, set[str]] = {}
        unknown_count = 0
        active_cams: dict[str, int] = {}
        package_n = 0
        vehicle_n = 0
        VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle", "van"}
        for cam_id, ts, pd, od in obs_rows:
            cid_s = str(cam_id)
            active_cams[cid_s] = active_cams.get(cid_s, 0) + 1
            ts_s = ts.isoformat() if ts else None
            for f in (pd or {}).get("faces") or []:
                name = f.get("person_name")
                if name:
                    named[name] = named.get(name, 0) + 1
                    if name not in named_first and ts_s:
                        named_first[name] = ts_s
                    if ts_s:
                        named_last[name] = ts_s
                    named_cams.setdefault(name, set()).add(cid_s)
                else:
                    unknown_count += 1
            for d in (od or {}).get("objects") or []:
                lbl = (d.get("label") or "").lower()
                if lbl == "package" or "package" in lbl:
                    package_n += 1
                elif lbl in VEHICLE_LABELS:
                    vehicle_n += 1
        facts["visitors"] = [
            {
                "name": n,
                "sightings": c,
                "first_seen": named_first.get(n),
                "last_seen": named_last.get(n),
                "cameras": sorted(named_cams.get(n, set())),
            }
            for n, c in sorted(named.items(), key=lambda kv: -kv[1])
        ]
        facts["unknown_visitors"] = unknown_count
        facts["packages"] = package_n
        facts["vehicles"] = vehicle_n
        facts["cameras_active"] = [
            {"id": cid, "name": cam_name_by_id.get(cid, "?"), "observations": n}
            for cid, n in sorted(active_cams.items(), key=lambda kv: -kv[1])
        ]

        # Incidents in window.
        inc_rows = (
            await db.execute(
                select(Incident)
                .where(Incident.started_at >= window_start)
                .where(Incident.started_at <= window_end)
            )
        ).scalars().all()
        facts["incidents_count"] = len(inc_rows)

        # Journeys in window.
        jour_rows = (
            await db.execute(
                select(Journey)
                .where(Journey.started_at >= window_start)
                .where(Journey.started_at <= window_end)
            )
        ).scalars().all()
        facts["journeys_count"] = len(jour_rows)
        facts["journeys"] = [
            {
                "id": str(j.id),
                "subject_kind": j.subject_kind,
                "subject_key": j.subject_key,
                "cameras_seen_count": j.cameras_seen_count,
                "incidents_count": j.incidents_count,
                "started_at": j.started_at.isoformat(),
                "summary_text": j.summary_text,
            }
            for j in jour_rows[:20]
        ]

        # Conversations.
        conv_rows = (
            await db.execute(
                select(Conversation)
                .where(Conversation.started_at >= window_start)
                .where(Conversation.started_at <= window_end)
            )
        ).scalars().all()
        facts["conversations_count"] = len(conv_rows)
        facts["conversations"] = [
            {
                "id": str(c.id),
                "summary_text": c.summary_text,
                "transcript_count": c.transcript_count,
            }
            for c in conv_rows
            if c.summary_text
        ][:10]

        # Audio detections by normalized label.
        ad_rows = (
            await db.execute(
                select(AudioDetection.label, AudioDetection.detected_at, AudioDetection.camera_id)
                .where(AudioDetection.detected_at >= window_start)
                .where(AudioDetection.detected_at <= window_end)
            )
        ).all()
        counts: Counter[str] = Counter()
        samples: dict[str, list[str]] = {}
        for lbl, ts, cam_id in ad_rows:
            counts[lbl] += 1
            samples.setdefault(lbl, []).append(
                f"{ts.isoformat()}@{cam_name_by_id.get(str(cam_id), '?')}"
            )
        facts["audio_events"] = dict(counts)
        facts["audio_event_samples"] = {
            k: v[:5] for k, v in samples.items()
        }
    return facts


def _build_prompt(
    facts: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> str:
    lines = [
        f"Window. {window_start.isoformat()} to {window_end.isoformat()}.",
        "",
    ]
    if facts.get("visitors"):
        lines.append("Named visitors.")
        for v in facts["visitors"]:
            cams = ", ".join(v.get("cameras") or []) or "?"
            lines.append(
                f"- {v['name']}. {v['sightings']} sightings"
                f" on {cams} from {v.get('first_seen')} to {v.get('last_seen')}"
            )
    if facts.get("unknown_visitors"):
        lines.append(f"Unknown face sightings. {facts['unknown_visitors']}.")
    if facts.get("packages"):
        lines.append(f"Package detections. {facts['packages']}.")
    if facts.get("vehicles"):
        lines.append(f"Vehicle detections. {facts['vehicles']}.")
    if facts.get("incidents_count"):
        lines.append(f"Incidents. {facts['incidents_count']}.")
    if facts.get("journeys") and facts["journeys"]:
        lines.append("Cross-camera journeys.")
        for j in facts["journeys"]:
            lines.append(
                f"- {j.get('subject_key', '?')} "
                f"({j.get('subject_kind')}). {j.get('cameras_seen_count')} cameras."
                f" started {j.get('started_at')}"
                + (f". recap. {j.get('summary_text')}" if j.get("summary_text") else "")
            )
    if facts.get("audio_events"):
        lines.append("Audio detections.")
        for lbl, n in facts["audio_events"].items():
            samples = facts.get("audio_event_samples", {}).get(lbl) or []
            sample_str = "; ".join(samples[:3])
            lines.append(f"- {lbl}. {n} events. samples. {sample_str}")
    if facts.get("conversations") and facts["conversations"]:
        lines.append("Notable conversations.")
        for c in facts["conversations"]:
            lines.append(f'- {c.get("summary_text")}')
    if facts.get("cameras_active"):
        cams = ", ".join(
            f"{c['name']} ({c['observations']})"
            for c in facts["cameras_active"][:5]
        )
        lines.append(f"Most active cameras. {cams}.")
    lines.append("")
    lines.append(
        "Write a short bullet list summarizing the last 24h for the"
        " household. One bullet per topic. Be concrete with counts"
        " and times. Skip topics with no signal."
    )
    return "\n".join(lines)
