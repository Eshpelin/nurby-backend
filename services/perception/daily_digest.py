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
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

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
    Vehicle,
)

logger = logging.getLogger("nurby.perception.daily_digest")


DAILY_SYSTEM_PROMPT = (
    "You are a trusted housemate giving someone a quick, warm recap of "
    "what happened while they were away or asleep, based on home camera "
    "activity. Write 2 to 5 short natural sentences, like you are filling "
    "them in over coffee. Lead with the single most notable thing. Name "
    "people when known and say what they did and roughly when, using "
    "friendly clock times like '3:10 AM', never dates or ISO timestamps. "
    "Group repetitive or trivial motion into a phrase ('quiet otherwise', "
    "'normal street traffic out front'). Do NOT list statistics, counts, "
    "or bullet points. Do NOT invent anything not in the events. If little "
    "or nothing notable happened, just say it was a quiet night in one "
    "sentence. Be concise and human, not a report."
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


async def narrate_window(window_start: datetime, window_end: datetime) -> dict:
    """On-demand narrative recap for an arbitrary window, NOT persisted.

    Reuses the morning-brief pipeline (notable events + narrative prompt)
    so a per-hour 'summarize this' reads like the daily brief. Returns
    {summary, notable_events}. summary is None when no VLM provider exists.
    """
    facts = await _collect_facts(window_start, window_end)
    provider = await _resolve_provider()
    summary: str | None = None
    if provider is not None:
        prompt = _build_prompt(facts, window_start, window_end)
        max_out = resolve_output_cap(getattr(provider, "max_output_tokens", None)) or 400
        try:
            summary = await call_text(
                provider=provider,
                system_prompt=DAILY_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_tokens=max_out,
            )
            if summary:
                summary = summary.strip()
        except Exception:
            logger.exception("window narration VLM call failed")
    return {
        "summary": summary,
        "notable_events": facts.get("notable_events") or [],
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


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

        # Chronological list of NOTABLE events for the narrative recap. this
        # is what the VLM reads. people doing things, described incidents,
        # sounds, conversations, with friendly local clock times. Raw counts
        # are deliberately NOT the input. they produce a stats dump, not a
        # recap.
        tz = await _get_local_tz()
        events: list[dict] = []

        for n, c in named.items():
            cams = ", ".join(cam_name_by_id.get(cid, "?") for cid in named_cams.get(n, set())) or "a camera"
            first = named_first.get(n)
            events.append({
                "ts": first,
                "when": _fmt_clock(first, tz),
                "text": f"{n} seen on {cams}" + (f" ({c} times)" if c > 2 else ""),
            })

        for inc in inc_rows:
            if inc.signature_kind == "motion":
                continue  # ambient motion is not a notable event
            cam = cam_name_by_id.get(str(inc.camera_id), "a camera")
            iso = inc.started_at.isoformat() if inc.started_at else None
            if inc.summary_text:
                text = inc.summary_text.strip()
            else:
                subj = _incident_phrase(inc.signature_kind, inc.signature_key)
                text = f"{subj} at {cam}"
                if (inc.occurrence_count or 0) > 3:
                    text += f", repeatedly"
            events.append({"ts": iso, "when": _fmt_clock(iso, tz), "text": text})

        for lbl, ts, cam_id in ad_rows:
            iso = ts.isoformat() if ts else None
            cam = cam_name_by_id.get(str(cam_id), "a camera")
            events.append({
                "ts": iso, "when": _fmt_clock(iso, tz),
                "text": f"{lbl.replace('_', ' ')} heard at {cam}",
            })

        for c in conv_rows:
            if c.summary_text:
                events.append({"ts": None, "when": "", "text": f"Conversation. {c.summary_text.strip()}"})

        # Vehicles identified in the window (by plate). describe them by
        # what they are plus the plate, e.g. "Red Nissan (ABC123) seen".
        veh_rows = (
            await db.execute(
                select(Vehicle).where(Vehicle.last_seen_at >= window_start)
            )
        ).scalars().all()
        for v in veh_rows:
            first = v.first_seen_at
            if first and first < window_start:
                first = window_start
            iso = first.isoformat() if first else None
            label = (v.description or "").strip() or v.display_name
            plate = f" ({v.license_plate})" if v.license_plate and v.license_plate not in label else ""
            events.append({
                "ts": iso, "when": _fmt_clock(iso, tz),
                "text": f"{label}{plate} seen",
            })

        events.sort(key=lambda e: e.get("ts") or "")
        facts["notable_events"] = events[:25]
        facts["notable_count"] = len(events)

    return facts


def _incident_phrase(kind: str | None, key: str | None) -> str:
    k = (kind or "").lower()
    if k == "person":
        return key or "someone"
    if k in ("cluster", "unknown"):
        return "an unrecognized person"
    if k == "object":
        return (key or "activity").replace(",", " and ")
    return key or "activity"


async def _get_local_tz():
    try:
        from zoneinfo import ZoneInfo
        name = await get_setting("system_timezone", None)
        if name:
            return ZoneInfo(str(name))
    except Exception:
        pass
    return None


def _fmt_clock(iso: str | None, tz) -> str:
    """ISO -> friendly local clock like '3:10 AM'. Empty on failure."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        if tz is not None:
            dt = dt.astimezone(tz)
        else:
            dt = dt.astimezone()
        return dt.strftime("%-I:%M %p")
    except Exception:
        return ""


def _window_phrase(window_start: datetime, window_end: datetime) -> str:
    """Human phrasing for the window based on its length + end hour."""
    hours = max(1, round((window_end - window_start).total_seconds() / 3600))
    end_local = window_end.astimezone()
    if hours <= 14 and end_local.hour <= 11:
        return "overnight"
    if hours <= 30:
        return "since this time yesterday"
    return f"over the last {hours} hours"


def _build_prompt(
    facts: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> str:
    when = _window_phrase(window_start, window_end)
    events = facts.get("notable_events") or []

    if not events:
        return (
            f"There were no notable events {when}. Reply with a single short,"
            " friendly sentence saying it was a quiet night with nothing of"
            " note. Do not invent anything."
        )

    lines = [f"Here are the notable events {when}, earliest first:", ""]
    for e in events:
        clock = e.get("when")
        prefix = f"{clock} - " if clock else "- "
        lines.append(f"{prefix}{e.get('text')}")
    lines.append("")
    lines.append(
        f"Write a brief, friendly recap of what happened {when} for the person"
        " who lives here. Lead with the most notable thing, name people and"
        " say what they did with natural clock times, and fold repetitive"
        " activity into a short phrase. No counts, no bullet points, no dates."
    )
    return "\n".join(lines)
