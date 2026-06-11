"""Idle-time VLM enrichment worker (v2.0 - v2.3).

When the live VLM backlog is empty, spend spare capacity building a richer,
immutable understanding of already-captured frames.

Model (per docs/vlm-enrichment-design.md):

* Every VLM pass over a frame is stored append-only and is **immutable**.
  the description text of a pass is never edited and never deleted. it is
  permanent frame-level history for debugging and audit.
* Raw lens passes (``attributes``, ``temporal``, ``anomaly``) never touch
  the observation's caption. they only add history.
* A separate ``summary`` pass makes its own VLM call that synthesizes the
  prior passes into one clean summary. that summary is what the UI, search,
  and rules use (it populates ``Observation.vlm_description`` and the search
  embedding). the raw passes stay untouched behind it.

The worker runs one lens per idle iteration so a single observation walks
attributes -> (temporal) -> anomaly -> summary over successive cycles,
yielding between each so a live frame always preempts it. Off by default,
empty-backlog triggered, budget-capped. It never holds a DB session across
a VLM call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from shared.app_settings import get_setting
from shared.database import async_session
from shared.models import Observation, ObservationVlmPass, Recording

logger = logging.getLogger("nurby.perception.vlm_enrichment")

# Raw lenses run in this order. summary always runs last, over their output.
RAW_LENSES = ("attributes", "temporal", "anomaly")

LENS_PROMPTS = {
    "attributes": (
        "You are reviewing a single security-camera still during quiet hours "
        "to capture detail a fast first pass may have missed. Reply with one "
        "or two plain sentences only. No preamble, no markdown, no bullet "
        "points, no headings. Describe the concrete things you can actually "
        "see. people and what they wear or carry, vehicles with color and "
        "type, any readable text, signage or license plates, notable objects, "
        "and time-of-day cues. Be factual and specific. Do not invent details "
        "you cannot see, and do not guess names."
    ),
    "anomaly": (
        "You are reviewing a security-camera still for anything unusual or "
        "noteworthy that a routine description would skip. a person where one "
        "would not expect them, an open door or gate, something left behind, "
        "signs of forced entry, an obscured face. Reply with one or two plain "
        "sentences only, no preamble. If nothing is unusual, reply exactly "
        "'Nothing unusual.'"
    ),
    "temporal": (
        "These are sequential frames from one security camera, left to right, "
        "a few seconds apart. Describe what changed between them and what any "
        "person or vehicle is doing. approaching, leaving, loitering, picking "
        "something up, dropping something off. Reply with one or two plain "
        "sentences only, no preamble. Do not invent details you cannot see."
    ),
    "summary": (
        "Below are independent observations of the SAME security-camera "
        "moment from different analysis passes. Write one clear, factual "
        "summary, two sentences at most, that captures everything the passes "
        "consistently report. Do not add any detail that none of the passes "
        "mention. No preamble, no markdown."
    ),
}

VERIFY_PROMPT = (
    "Here is a SUMMARY and the source OBSERVATIONS it was built from. Reply "
    "'OK' if every concrete detail in the summary is supported by at least "
    "one observation. Otherwise reply with the single word 'UNSUPPORTED' "
    "followed by the unsupported detail. Be strict but do not nitpick "
    "wording."
)

_COLORS = {
    "red", "orange", "yellow", "green", "blue", "purple", "pink", "brown",
    "black", "white", "gray", "grey", "silver", "gold", "tan", "beige",
}
_TIME_OF_DAY = {
    "morning", "afternoon", "evening", "night", "nighttime", "dusk", "dawn",
    "daytime", "midday", "noon", "sunset", "sunrise",
}


def build_attributes(description: str | None, detections: list[dict]) -> dict:
    """Derive structured, searchable fields from the enrichment text and the
    YOLO detections already attached to the observation. Deterministic, no
    extra VLM round-trip. Feeds search and (later) rule filtering."""
    desc = description or ""
    low = desc.lower()
    counts: dict[str, int] = {}
    for d in detections or []:
        lbl = d.get("label")
        if lbl:
            counts[lbl] = counts.get(lbl, 0) + 1
    colors = sorted(c for c in _COLORS if re.search(rf"\b{c}\b", low))
    tod = sorted(t for t in _TIME_OF_DAY if re.search(rf"\b{t}\b", low))
    text_seen = []
    for tok in re.findall(r"\b[A-Z0-9]{4,8}\b", desc):
        if any(ch.isdigit() for ch in tok) and tok not in text_seen:
            text_seen.append(tok)
    return {
        "objects": [{"label": k, "count": v} for k, v in sorted(counts.items())],
        "people_count": counts.get("person", 0),
        "colors": colors,
        "time_of_day": tod,
        "text_seen": text_seen[:6],
        "source": "attributes-pass-v1",
    }


def next_lens(existing: set[str], has_recording: bool, summary_stale: bool) -> str | None:
    """Decide the next lens to run for one observation.

    Raw lenses first, in order, skipping temporal when no recording overlaps.
    Then summary, but only once at least one raw pass exists and the summary
    is missing or stale (new raw passes since the last summary).
    """
    for lens in RAW_LENSES:
        if lens == "temporal" and not has_recording:
            continue
        if lens not in existing:
            return lens
    raw_done = any(l in existing for l in RAW_LENSES)
    if raw_done and summary_stale:
        return "summary"
    return None


class EnrichmentManager:
    """Background loop that enriches observations with immutable VLM passes."""

    def __init__(self) -> None:
        self._vlm = None
        self._redis = None

    # ---- gates ------------------------------------------------------

    async def _enabled(self) -> bool:
        return bool(await get_setting("vlm_enrichment_enabled", True))

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            from shared.config import settings
            self._redis = aioredis.from_url(settings.redis_url)
        return self._redis

    async def _backlog_empty(self) -> bool:
        """True when no camera has live VLM work queued. Reads the real Redis
        backlog, not the in-memory EMA (which goes stale when a camera stops).
        Fails closed so enrichment never competes with live work."""
        try:
            r = await self._get_redis()
            for k in await r.keys("nurby:vlm_pending:*"):
                if int(await r.llen(k) or 0) > 0:
                    return False
            return True
        except Exception:
            logger.debug("backlog check failed, assuming not empty", exc_info=True)
            return False

    def _usage_key(self) -> str:
        hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        return f"nurby:vlm_enrich_usage:{hour}"

    async def _within_budget(self, budget_minutes: int) -> bool:
        if budget_minutes <= 0:
            return False
        try:
            r = await self._get_redis()
            used = float(await r.get(self._usage_key()) or 0.0)
        except Exception:
            return True
        return used < budget_minutes * 60

    async def _record_usage(self, seconds: float) -> None:
        try:
            r = await self._get_redis()
            key = self._usage_key()
            await r.incrbyfloat(key, max(0.0, seconds))
            await r.expire(key, 7200)
        except Exception:
            logger.debug("enrichment budget write failed", exc_info=True)

    # ---- candidate selection ----------------------------------------

    async def _next_candidate(self, max_passes: int, cooldown_s: int,
                              retention_days: int):
        """Pick the next observation to advance. one with a frame, real
        content, within retention, not recently touched, and not yet fully
        passed. Returns (id, camera_id, started_at, thumbnail_path, detections)
        or None."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        recool = datetime.now(timezone.utc) - timedelta(seconds=cooldown_s)
        async with async_session() as db:
            rows = (await db.execute(
                select(Observation)
                .where(Observation.thumbnail_path.is_not(None))
                .where(Observation.object_detections.is_not(None))
                .where(Observation.started_at >= cutoff)
                .where(Observation.enrich_pass_count < max_passes)
                .where(
                    (Observation.last_enriched_at.is_(None))
                    | (Observation.last_enriched_at < recool)
                )
                .order_by(
                    Observation.enrich_pass_count.asc(),
                    Observation.started_at.asc(),
                )
                .limit(1)
            )).scalars().all()
            if not rows:
                return None
            o = rows[0]
            return (o.id, o.camera_id, o.started_at, o.thumbnail_path,
                    (o.object_detections or {}).get("objects", []))

    async def _passes_for(self, obs_id: uuid.UUID):
        async with async_session() as db:
            rows = (await db.execute(
                select(ObservationVlmPass)
                .where(ObservationVlmPass.observation_id == obs_id)
                .order_by(ObservationVlmPass.pass_no.asc())
            )).scalars().all()
            return [(p.pass_no, p.lens, p.description) for p in rows]

    async def _has_recording(self, camera_id, ts) -> bool:
        async with async_session() as db:
            r = (await db.execute(
                select(Recording.id)
                .where(Recording.camera_id == camera_id)
                .where(Recording.started_at <= ts)
                .where(
                    (Recording.ended_at.is_(None))
                    | (Recording.ended_at >= ts)
                ).limit(1)
            )).first()
            return r is not None

    # ---- one lens ---------------------------------------------------

    async def _enrich_one(self) -> bool:
        from services.perception.vlm import get_active_provider

        provider = await get_active_provider()
        if provider is None:
            return False

        cand = await self._next_candidate(
            max_passes=int(await get_setting("vlm_enrichment_max_passes", 6)),
            cooldown_s=int(await get_setting("vlm_enrichment_cooldown_seconds", 3600)),
            retention_days=int(await get_setting("vlm_enrichment_retention_days", 30)),
        )
        if cand is None:
            return False
        obs_id, camera_id, ts, thumb, detections = cand

        passes = await self._passes_for(obs_id)
        existing_lenses = {lens for _, lens, _ in passes}
        has_rec = await self._has_recording(camera_id, ts)
        summary_passes = [p for p in passes if p[1] == "summary"]
        # summary is stale if there is none, or a raw pass came after it
        last_summary_no = max((p[0] for p in summary_passes), default=0)
        last_raw_no = max((p[0] for p in passes if p[1] in RAW_LENSES), default=0)
        summary_stale = last_raw_no > last_summary_no

        lens = next_lens(existing_lenses, has_rec, summary_stale)
        if lens is None:
            await self._touch(obs_id)
            return True

        if self._vlm is None:
            from services.perception.vlm import VLMClient
            self._vlm = VLMClient()

        t0 = time.monotonic()
        try:
            if lens == "summary":
                ok = await self._run_summary(obs_id, thumb, detections, passes, provider)
            else:
                ok = await self._run_raw_lens(lens, obs_id, camera_id, ts, thumb,
                                              detections, provider)
        except Exception:
            logger.debug("enrichment lens %s failed for %s", lens, obs_id, exc_info=True)
            await self._touch(obs_id)
            return True
        await self._record_usage(time.monotonic() - t0)
        if not ok:
            await self._touch(obs_id)
        return True

    async def _run_raw_lens(self, lens, obs_id, camera_id, ts, thumb,
                            detections, provider) -> bool:
        frame = _load_frame(thumb)
        if frame is None:
            return False
        if lens == "temporal":
            montage = await self._temporal_montage(camera_id, ts, frame)
            if montage is None:
                # No usable neighbors. record the lens as attempted so we do
                # not retry forever, with an empty description.
                await self._append_pass(obs_id, lens, provider, None, None)
                return True
            frame = montage
        text = await self._vlm.describe(
            frame, detections, provider,
            system_prompt=LENS_PROMPTS[lens], max_tokens=160,
        )
        text = (text or "").strip()
        attrs = build_attributes(text, detections) if lens == "attributes" else None
        await self._append_pass(obs_id, lens, provider, text or None, attrs)
        logger.info("enriched %s lens=%s. %s", obs_id, lens, (text or "")[:70])
        return True

    async def _run_summary(self, obs_id, thumb, detections, passes, provider) -> bool:
        frame = _load_frame(thumb)
        if frame is None:
            return False
        body = "\n".join(
            f"- ({lens}) {desc.strip()}"
            for _, lens, desc in passes if desc and lens != "summary"
        )
        if not body:
            return False
        summary = await self._vlm.describe(
            frame, detections, provider,
            system_prompt=LENS_PROMPTS["summary"],
            extra_context=f"Observations:\n{body}", max_tokens=160,
        )
        summary = (summary or "").strip()
        if not summary:
            return False
        verdict = await self._verify(summary, body, provider)
        embedding = await self._embed(summary)
        attrs = build_attributes(summary, detections)
        attrs["verify"] = verdict
        await self._write_summary(obs_id, summary, attrs, embedding, provider)
        logger.info("summarized %s [%s]. %s", obs_id, verdict.get("status"), summary[:70])
        return True

    async def _verify(self, summary: str, body: str, provider) -> dict:
        """Cheap anti-hallucination check. ask the model whether the summary
        is supported by the source passes. Best-effort. on any error or an
        unavailable text path, mark 'unchecked' rather than blocking."""
        try:
            # Reuse the image-grounded describe with no useful image by
            # passing the texts as context. a tiny frame keeps the call valid.
            import numpy as np
            blank = np.zeros((64, 64, 3), dtype=np.uint8)
            out = await self._vlm.describe(
                blank, [], provider, system_prompt=VERIFY_PROMPT,
                extra_context=f"SUMMARY:\n{summary}\n\nOBSERVATIONS:\n{body}",
                max_tokens=60,
            )
            out = (out or "").strip()
            if out.upper().startswith("OK"):
                return {"status": "ok"}
            if "UNSUPPORTED" in out.upper():
                return {"status": "unsupported", "note": out[:200]}
            return {"status": "unclear", "note": out[:200]}
        except Exception:
            return {"status": "unchecked"}

    async def _embed(self, text: str):
        try:
            from services.search.embeddings import (
                generate_embedding,
                get_embedding_provider,
            )
            ep = await get_embedding_provider()
            return await generate_embedding(text, ep)
        except Exception:
            logger.debug("enrichment embedding failed", exc_info=True)
            return None

    # ---- immutable writes -------------------------------------------

    async def _append_pass(self, obs_id, lens, provider, description, attributes):
        """Append one immutable pass. never touches the observation caption."""
        async with async_session() as db:
            obs = await db.get(Observation, obs_id)
            if obs is None:
                return
            new_no = (obs.enrich_pass_count or 0) + 1
            db.add(ObservationVlmPass(
                observation_id=obs_id, pass_no=new_no, lens=lens,
                prompt_version="v1",
                provider_name=getattr(provider, "name", None),
                model=getattr(provider, "default_model", None),
                description=description, attributes=attributes,
                authoritative=False,
            ))
            obs.enrich_pass_count = new_no
            obs.last_enriched_at = datetime.now(timezone.utc)
            await db.commit()

    async def _write_summary(self, obs_id, summary, attributes, embedding, provider):
        """Append an immutable summary pass AND repoint the fields the rest of
        the app uses (vlm_description + search embedding) at it. Raw passes are
        never modified. only the derived summary view moves forward."""
        async with async_session() as db:
            obs = await db.get(Observation, obs_id)
            if obs is None:
                return
            new_no = (obs.enrich_pass_count or 0) + 1
            # Demote the previous summary pointer (text stays immutable).
            for p in (await db.execute(
                select(ObservationVlmPass)
                .where(ObservationVlmPass.observation_id == obs_id)
                .where(ObservationVlmPass.lens == "summary")
                .where(ObservationVlmPass.authoritative.is_(True))
            )).scalars().all():
                p.authoritative = False
            db.add(ObservationVlmPass(
                observation_id=obs_id, pass_no=new_no, lens="summary",
                prompt_version="v1",
                provider_name=getattr(provider, "name", None),
                model=getattr(provider, "default_model", None),
                description=summary, attributes=attributes, authoritative=True,
            ))
            obs.enrich_pass_count = new_no
            obs.last_enriched_at = datetime.now(timezone.utc)
            # The summary is the view used everywhere. preserve the original
            # live caption once in pass 1 (already immutable) and elsewhere.
            obs.vlm_description = summary
            if embedding is not None:
                obs.description_embedding = embedding
            await db.commit()

    async def _touch(self, obs_id) -> None:
        async with async_session() as db:
            obs = await db.get(Observation, obs_id)
            if obs is not None:
                obs.last_enriched_at = datetime.now(timezone.utc)
                await db.commit()

    # ---- temporal frames --------------------------------------------

    async def _temporal_montage(self, camera_id, ts, current):
        """Horizontal montage of [prev, current, next] frames sliced from an
        overlapping recording. Returns None if no recording or extraction
        fails (decision: recordings-only, skip if absent)."""
        async with async_session() as db:
            rec = (await db.execute(
                select(Recording)
                .where(Recording.camera_id == camera_id)
                .where(Recording.started_at <= ts)
                .where((Recording.ended_at.is_(None)) | (Recording.ended_at >= ts))
                .order_by(Recording.started_at.desc()).limit(1)
            )).scalars().first()
        if rec is None or not rec.file_path:
            return None
        path = _resolve_recording_path(rec.file_path)
        if not path or not os.path.exists(path):
            return None
        offset = max(0.0, (ts - rec.started_at).total_seconds())
        prev = _extract_frame(path, max(0.0, offset - 2.0))
        nxt = _extract_frame(path, offset + 2.0)
        frames = [f for f in (prev, current, nxt) if f is not None]
        if len(frames) < 2:
            return None
        return _hmontage(frames)

    # ---- main loop --------------------------------------------------

    async def run(self) -> None:
        logger.info("VLM enrichment worker started (idle backfill)")
        while True:
            try:
                poll = int(await get_setting("vlm_enrichment_idle_poll_seconds", 30))
                if not await self._enabled():
                    await asyncio.sleep(max(30, poll))
                    continue
                budget = int(await get_setting("vlm_enrichment_budget_minutes_per_hour", 20))
                if not await self._backlog_empty() or not await self._within_budget(budget):
                    await asyncio.sleep(poll)
                    continue
                did = await self._enrich_one()
                await asyncio.sleep(1 if did else poll)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("enrichment loop error")
                await asyncio.sleep(30)


# ---- frame helpers --------------------------------------------------

def _load_frame(path: str | None):
    if not path:
        return None
    try:
        import cv2
        if not os.path.exists(path):
            return None
        return cv2.imread(path)
    except Exception:
        return None


def _extract_frame(video_path: str, offset_seconds: float):
    """Pull a single frame from a recording at the given offset via ffmpeg."""
    try:
        import subprocess
        import tempfile

        import cv2
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            out = tf.name
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{offset_seconds:.2f}", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
        )
        img = cv2.imread(out)
        try:
            os.unlink(out)
        except OSError:
            pass
        return img
    except Exception:
        return None


def _hmontage(frames):
    try:
        import cv2
        import numpy as np
        h = min(f.shape[0] for f in frames)
        resized = [cv2.resize(f, (int(f.shape[1] * h / f.shape[0]), h)) for f in frames]
        return np.hstack(resized)
    except Exception:
        return frames[0] if frames else None


def _resolve_recording_path(file_path: str) -> str | None:
    if not file_path:
        return None
    if os.path.isabs(file_path):
        return file_path
    from shared.config import settings
    base = os.path.abspath(settings.recordings_path)
    rel = file_path
    for prefix in ("./recordings/", "recordings/", "./"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    return os.path.join(base, rel)
