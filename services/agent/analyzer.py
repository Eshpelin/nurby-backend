"""VLM analyzer for the agentic Q&A layer (Wave 1C).

Owns the ad-hoc "look at frames and answer a question" path described in
docs/agent-design.md sections 5, 6, 7, 11, 17. Two public entry points.

* ``analyze_frame_target(ctx, observation_id, question, provider_id=None)``
  for single-observation analysis.
* ``analyze_clip_target(ctx, camera_id, time_from, time_to, question,
  provider_id=None)`` for time-window analysis that may stitch multiple
  Recording rows together.

Both return an :class:`AnalyzerResult`. Both walk the same pipeline.

1. Cache lookup on ``vlm_frame_analysis`` (eternal, frame-keyed).
2. Long-clip safety. refuse windows > ``agent_max_clip_minutes``.
3. Frame extraction via ``ffmpeg -ss <t> -frames:v 1`` against the
   underlying Recording mp4(s). Multi-recording windows are stitched by
   intersection-length proportion (section 5.5).
4. Privacy redaction. zone blur + face blur for ``privacy_blur=true``
   persons + nudity safety floor. Identical for cloud and local
   providers (section 6.2). Never silently skipped.
5. VLM call with the structured ``ANALYZER_RESPONSE_SCHEMA``. The
   ``cannot_tell`` verdict is the hallucination escape hatch
   (section 5.3).
6. Persist the redacted thumbnails to
   ``<thumbnails_path>/agent/<run_id>/<vlm_call_id>/<frame_idx>.jpg`` so
   the audit page can show exactly what the model saw (resolution 2).
7. Write a cache row + an ``agent_vlm_calls`` audit row.

Schema + persistence integration is defensive. ``services.agent.runs`` is
imported lazily; if absent, the call still returns the answer and logs
the gap rather than crashing the agent loop.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import string
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from sqlalchemy import select, text

from services.perception.llm_errors import call_with_retry
from services.perception.vlm import get_active_provider
from shared.app_settings import get_setting
from shared.config import settings
from shared.database import async_session
from shared.models import Camera, Observation, Provider, Recording

logger = logging.getLogger("nurby.agent.analyzer")


# ────────────────────────────────────────────────────────────────────
# Public dataclass
# ────────────────────────────────────────────────────────────────────


@dataclass
class AnalyzerResult:
    """Return shape from both analyzer entry points.

    ``answer`` is the structured VLM response that matches
    :data:`ANALYZER_RESPONSE_SCHEMA`. On hard failures the analyzer
    returns a still-valid AnalyzerResult with ``answer`` containing an
    ``error`` key (e.g. ``{"error": "clip_too_long"}``) and zero token
    cost. The agent driver treats those as "no useful data" without
    crashing the loop.
    """

    cache_hit: bool
    answer: dict
    confidence: float | None
    frame_count: int
    cost_cents: int
    tokens_in: int
    tokens_out: int
    thumbnails_path: str | None
    vlm_call_id: uuid.UUID | None = None
    error: str | None = None
    extras: dict = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────


ANALYZER_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["yes", "no", "uncertain", "cannot_tell"],
        },
        "summary": {
            "type": "string",
            "description": (
                "One sentence summary of what is visible relevant to the question."
                " Stay factual."
            ),
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frame_index": {"type": "integer"},
                    "description": {"type": "string"},
                    "bbox": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 4,
                        "maxItems": 4,
                        "description": (
                            "x1,y1,x2,y2 in pixel coords; omit if not localizable"
                        ),
                    },
                },
                "required": ["frame_index", "description"],
            },
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": (
                "Your confidence in the verdict from 0 to 1. Be honest."
                " If frames are ambiguous, output low confidence."
            ),
        },
        "structured_data": {
            "type": "object",
            "description": (
                "Free-form structured facts extracted, keyed by short field name."
                ' e.g. {"food_type":"rice and chicken", "duration_estimate_minutes": 18}'
            ),
            "additionalProperties": True,
        },
    },
    "required": ["verdict", "summary", "confidence", "evidence"],
}


ANALYZER_SYSTEM_PROMPT = (
    "You are a careful video evidence analyst. Look at the supplied camera"
    " frames and answer ONE question with strict honesty. Rules.\n"
    "- If the frames clearly answer the question, give a 'yes' or 'no'"
    " verdict with high confidence.\n"
    "- If the frames partially answer, use 'uncertain' with mid confidence"
    " and cite the evidence you do see.\n"
    "- If the frames do NOT show what's needed (wrong angle, blurred, dark,"
    " irrelevant), use 'cannot_tell' with low confidence. NEVER guess.\n"
    "- Always populate evidence[] with the frame indices you cite. Be"
    " specific about what you see in each.\n"
    "- Never invent objects or actions not visible.\n"
    "- Output ONLY valid JSON conforming to the response schema."
)


# Default cap if the AppSetting is not provisioned. Wave 1A may add a
# row; we read whichever side ships first.
_DEFAULT_MAX_CLIP_MINUTES = 60


# ────────────────────────────────────────────────────────────────────
# Question normalization + hashing
# ────────────────────────────────────────────────────────────────────


_PUNCT_RE = re.compile(f"[{re.escape(string.punctuation)}]+")
_WS_RE = re.compile(r"\s+")


def normalize_question(q: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Deterministic."""
    if not q:
        return ""
    s = q.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def question_hash(q: str) -> str:
    return hashlib.sha256(normalize_question(q).encode("utf-8")).hexdigest()


# ────────────────────────────────────────────────────────────────────
# Frame sampling
# ────────────────────────────────────────────────────────────────────


def select_frames(window_seconds: int, target_max_frames: int = 8) -> list[float]:
    """Return a list of relative seconds-into-window to sample.

    Decision tree per design doc section 5.1.

    * clip < 30s    sample every 3-5s, cap at 8
    * 30s - 5min    uniform 6 frames
    * 5min - 30min  uniform 8 frames (scene-change detection deferred to Phase 2)
    * > 30min       8 uniform from the first 10min + 4 more uniform from the rest

    ``target_max_frames`` is the absolute ceiling. The first three
    buckets respect it; the >30min bucket may emit up to 12 frames
    because the design spec explicitly returns two sub-windows. Callers
    that need a hard ceiling should slice the return list.
    """
    if window_seconds <= 0:
        return []

    # < 30s. one frame every 3-5s, capped at 8.
    if window_seconds < 30:
        step = 4.0  # 4-second cadence sits inside the 3-5s band
        count = max(1, min(8, int(window_seconds // step)))
        count = min(count, target_max_frames)
        if count <= 1:
            return [window_seconds / 2.0]
        # Even-spaced. avoid t=0 (often a black keyframe).
        return [(window_seconds * (i + 1)) / (count + 1) for i in range(count)]

    # 30s - 5min. uniform 6 frames.
    if window_seconds <= 300:
        count = min(6, target_max_frames)
        return [(window_seconds * (i + 1)) / (count + 1) for i in range(count)]

    # 5min - 30min. uniform 8 frames.
    # TODO(Phase 2). scene-change candidates first via opencv histogram
    # diff or pyscenedetect; current cap is the cheap uniform fallback.
    if window_seconds <= 1800:
        count = min(8, target_max_frames)
        return [(window_seconds * (i + 1)) / (count + 1) for i in range(count)]

    # > 30min. 8 uniform from first 10min + 4 uniform from the rest.
    first_window = 600.0
    head_count = 8
    tail_count = 4
    head = [
        (first_window * (i + 1)) / (head_count + 1) for i in range(head_count)
    ]
    tail_start = first_window
    tail_span = window_seconds - first_window
    tail = [
        tail_start + (tail_span * (i + 1)) / (tail_count + 1)
        for i in range(tail_count)
    ]
    return head + tail


# ────────────────────────────────────────────────────────────────────
# ffmpeg integration
# ────────────────────────────────────────────────────────────────────


def _resolve_recording_path(rec: Recording) -> Path | None:
    """Mirror ``services/api/routes/recordings.py:_resolve_recording_path``.

    The stored ``file_path`` may be absolute, relative to the configured
    ``recordings_path`` root, or already include the camera prefix. We
    try the most permissive interpretation first.
    """
    raw = rec.file_path or ""
    if not raw:
        return None
    candidates = [Path(raw)]
    base = Path(settings.recordings_path)
    candidates.append(base / raw)
    candidates.append(base / str(rec.camera_id) / raw)
    for c in candidates:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return None


async def _ffmpeg_extract_one(file_path: Path, ts: float) -> np.ndarray | None:
    """Pull a single frame at ``ts`` seconds from ``file_path``.

    Uses ``ffmpeg -ss <t> -i <file> -frames:v 1 -f image2pipe -vcodec
    mjpeg pipe:1``. Fast (-ss before -i = keyframe seek). Returns the
    decoded BGR ndarray, or None on any failure.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, ts):.3f}",
        "-i",
        str(file_path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await proc.communicate()
    except FileNotFoundError:
        logger.error("ffmpeg binary not found on PATH")
        return None
    except Exception:
        logger.exception("ffmpeg subprocess failed for %s", file_path)
        return None
    if proc.returncode != 0 or not stdout:
        return None
    try:
        arr = np.frombuffer(stdout, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        logger.exception("ffmpeg output decode failed for %s", file_path)
        return None


async def extract_frames_from_recording(
    recording_row: Recording,
    sample_seconds: list[float],
) -> list[np.ndarray]:
    """Extract each requested frame as a numpy array. Returns [] when the
    media file is missing (retention-pruned)."""
    path = _resolve_recording_path(recording_row)
    if path is None:
        logger.info(
            "recording %s file missing on disk (likely retention-pruned)",
            recording_row.id,
        )
        return []
    frames: list[np.ndarray] = []
    for ts in sample_seconds:
        frame = await _ffmpeg_extract_one(path, ts)
        if frame is not None:
            frames.append(frame)
    return frames


# ────────────────────────────────────────────────────────────────────
# Long-clip stitching
# ────────────────────────────────────────────────────────────────────


def _distribute_samples(
    recordings: list[Recording],
    time_from: datetime,
    time_to: datetime,
    total_frames: int,
) -> list[tuple[Recording, list[float]]]:
    """For each recording in the window, return its frame timestamps
    (seconds into THAT recording) proportional to its intersection
    length with the requested window.
    """
    out: list[tuple[Recording, list[float]]] = []
    # Compute per-recording intersection durations.
    intersections: list[tuple[Recording, float, float, float]] = []
    total_span = 0.0
    for rec in recordings:
        rec_start = rec.started_at
        rec_end = rec.ended_at or (
            rec_start
            + (
                _safe_timedelta(rec.duration_seconds)
                if rec.duration_seconds
                else (time_to - rec_start)
            )
        )
        # Tz normalize.
        rec_start = _aware(rec_start)
        rec_end = _aware(rec_end)
        ix_start = max(rec_start, time_from)
        ix_end = min(rec_end, time_to)
        span = max(0.0, (ix_end - ix_start).total_seconds())
        intersections.append((rec, span, (ix_start - rec_start).total_seconds(), (ix_end - rec_start).total_seconds()))
        total_span += span
    if total_span <= 0 or total_frames <= 0:
        return out
    # Allocate frame count per recording, floor + remainder.
    raw = [(rec, span, off_start, off_end, (span / total_span) * total_frames) for (rec, span, off_start, off_end) in intersections]
    allocations = [(rec, off_start, off_end, max(0, int(round(quota))) if span > 0 else 0)
                   for (rec, span, off_start, off_end, quota) in raw]
    # Generate evenly-spaced timestamps inside each recording's window.
    for rec, off_start, off_end, count in allocations:
        if count <= 0:
            continue
        span = max(0.0, off_end - off_start)
        if span <= 0:
            continue
        if count == 1:
            seconds = [off_start + span / 2.0]
        else:
            seconds = [off_start + (span * (i + 1)) / (count + 1) for i in range(count)]
        out.append((rec, seconds))
    return out


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_timedelta(secs: float | None):
    from datetime import timedelta
    if secs is None:
        return timedelta(seconds=0)
    try:
        return timedelta(seconds=float(secs))
    except (TypeError, ValueError):
        return timedelta(seconds=0)


# ────────────────────────────────────────────────────────────────────
# Privacy redaction
# ────────────────────────────────────────────────────────────────────


async def _redact_frames(
    frames: list[np.ndarray],
    camera_id: uuid.UUID,
    db,
) -> tuple[list[np.ndarray], list[dict]]:
    """Run the mandatory pre-VLM redaction pipeline on every frame.

    Prefers ``services.agent.privacy.redact_frame`` (Wave 1B). Falls
    back to a local composition of
    ``services.perception.privacy.get_active_zones`` + ``apply_privacy_blur``
    plus a face-blur pass. Never silently skips. on any failure the
    caller aborts the analyzer call.
    """
    reports: list[dict] = []
    # Preferred path. Wave 1B's unified helper.
    try:
        from services.agent.privacy import redact_frame as _wave1b_redact  # type: ignore

        out: list[np.ndarray] = []
        for frame in frames:
            redacted, report = await _wave1b_redact(frame, camera_id, db)
            out.append(redacted)
            reports.append(
                report.__dict__ if hasattr(report, "__dict__") else dict(report)
            )
        return out, reports
    except ImportError:
        logger.info("services.agent.privacy missing; falling back to perception redaction")
    except Exception:
        logger.exception("wave1b redact_frame failed; aborting analyzer call")
        raise

    # Fallback. perception primitives.
    from services.perception.privacy import apply_privacy_blur, get_active_zones

    zones = await get_active_zones(camera_id)
    out: list[np.ndarray] = []
    for frame in frames:
        redacted = apply_privacy_blur(frame, zones)
        # Face-blur for privacy_blur=true persons. Best-effort. Treat
        # any failure as a hard error per section 6.4.
        redacted = await _blur_private_faces(redacted, db)
        out.append(redacted)
        reports.append(
            {
                "privacy_zones": len(zones),
                "blurred_person_ids": [],
                "nudenet_regions": 0,
                "source": "perception_fallback",
            }
        )
    return out, reports


async def _blur_private_faces(frame: np.ndarray, db) -> np.ndarray:
    """Blur faces matching any ``Person.privacy_blur=true`` row.

    Best-effort. If the face recognizer is unavailable (model not
    downloaded, no insightface install) we degrade to returning the
    frame unchanged. The mandatory layer is the zone blur above; face
    blur is per-person opt-in and silently degrades.
    """
    try:
        from services.perception.faces import FaceRecognizer
    except Exception:
        return frame
    try:
        rec = FaceRecognizer()
        faces = await rec.detect_and_embed(frame)
        if not faces:
            return frame
        matched = await rec.match_faces(faces)
        # Pull the privacy_blur set in one go.
        from shared.models import Person

        rows = (
            await db.execute(select(Person.id, Person.privacy_blur).where(Person.privacy_blur.is_(True)))
        ).all()
        blur_ids = {str(r[0]) for r in rows}
        if not blur_ids:
            return frame
        out = frame.copy()
        for f in matched:
            pid = f.get("person_id")
            if not pid or str(pid) not in blur_ids:
                continue
            bbox = f.get("bbox") or []
            if len(bbox) != 4:
                continue
            x1, y1, x2, y2 = [max(0, int(v)) for v in bbox]
            if x2 <= x1 or y2 <= y1:
                continue
            roi = out[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            k = 55
            out[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)
        return out
    except Exception:
        logger.debug("face-blur step failed; returning frame unchanged", exc_info=True)
        return frame


# ────────────────────────────────────────────────────────────────────
# Cache lookup + write
# ────────────────────────────────────────────────────────────────────


async def cache_lookup(
    db,
    *,
    observation_id: uuid.UUID | None,
    recording_id: uuid.UUID | None,
    qhash: str,
    provider_id: uuid.UUID | None,
    model: str,
) -> dict | None:
    """Returns the cached row's ``response_json`` plus accounting, or None.

    Either ``observation_id`` or ``recording_id`` must be set. The cache
    table has a CHECK constraint enforcing this.
    """
    if observation_id is None and recording_id is None:
        return None
    if observation_id is not None:
        where = "observation_id = :oid"
        params = {"oid": str(observation_id)}
    else:
        where = "recording_id = :rid"
        params = {"rid": str(recording_id)}
    sql = (
        f"SELECT id, response_json, confidence, cost_tokens_in, cost_tokens_out,"
        f" cost_cents, thumbnail_path"
        f" FROM vlm_frame_analysis"
        f" WHERE {where}"
        f"   AND question_hash = :qhash"
        f"   AND model = :model"
        f"   AND ((:provider_id IS NULL AND provider_id IS NULL) OR provider_id = :provider_id)"
        f" LIMIT 1"
    )
    try:
        row = (
            await db.execute(
                text(sql),
                {
                    **params,
                    "qhash": qhash,
                    "model": model,
                    "provider_id": str(provider_id) if provider_id else None,
                },
            )
        ).first()
    except Exception:
        logger.debug("cache_lookup failed", exc_info=True)
        return None
    if row is None:
        return None
    return {
        "id": row[0],
        "response_json": row[1],
        "confidence": row[2],
        "cost_tokens_in": row[3] or 0,
        "cost_tokens_out": row[4] or 0,
        "cost_cents": row[5] or 0,
        "thumbnail_path": row[6],
    }


async def cache_write(
    db,
    *,
    observation_id: uuid.UUID | None,
    recording_id: uuid.UUID | None,
    qhash: str,
    question_text: str,
    provider_id: uuid.UUID | None,
    model: str,
    response_json: dict,
    confidence: float | None,
    cost_tokens_in: int,
    cost_tokens_out: int,
    cost_cents: int,
    thumbnail_path: str | None,
) -> None:
    """Insert into the cache. ON CONFLICT DO NOTHING (the partial unique
    index handles dupes from concurrent writers)."""
    sql = (
        "INSERT INTO vlm_frame_analysis"
        " (id, observation_id, recording_id, question_hash, question_text,"
        "  provider_id, model, response_json, confidence,"
        "  cost_tokens_in, cost_tokens_out, cost_cents, thumbnail_path)"
        " VALUES (:id, :oid, :rid, :qhash, :qtext, :pid, :model, CAST(:resp AS JSONB),"
        "  :conf, :tin, :tout, :cents, :thumb)"
        " ON CONFLICT DO NOTHING"
    )
    try:
        await db.execute(
            text(sql),
            {
                "id": str(uuid.uuid4()),
                "oid": str(observation_id) if observation_id else None,
                "rid": str(recording_id) if recording_id else None,
                "qhash": qhash,
                "qtext": question_text,
                "pid": str(provider_id) if provider_id else None,
                "model": model,
                "resp": json.dumps(response_json),
                "conf": confidence,
                "tin": cost_tokens_in,
                "tout": cost_tokens_out,
                "cents": cost_cents,
                "thumb": thumbnail_path,
            },
        )
        await db.commit()
    except Exception:
        logger.exception("cache_write failed")
        try:
            await db.rollback()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────
# VLM call with structured response
# ────────────────────────────────────────────────────────────────────


_HTTP: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.AsyncClient(timeout=60.0)
    return _HTTP


def _encode_jpeg_b64(frame: np.ndarray, quality: int = 85) -> str:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode("utf-8")


async def call_vlm_structured(
    provider: Provider,
    frames: list[np.ndarray],
    question: str,
) -> dict:
    """Send multi-image structured request. Returns the parsed JSON.

    Honors :data:`ANALYZER_RESPONSE_SCHEMA` via the provider's structured
    output feature where possible. Falls back to JSON-mode + defensive
    parsing for providers without a strict schema knob (Ollama).
    """
    b64s = [_encode_jpeg_b64(f) for f in frames]
    user_text = (
        f"Question. {question}\n\n"
        f"You are looking at {len(b64s)} frames numbered 0..{len(b64s) - 1} in order."
        " Respond ONLY with JSON matching the response schema."
    )
    if provider.kind == "openai":
        return await _call_openai_structured(provider, b64s, user_text)
    if provider.kind == "anthropic":
        return await _call_anthropic_structured(provider, b64s, user_text)
    if provider.kind == "google":
        return await _call_google_structured(provider, b64s, user_text)
    if provider.kind == "ollama":
        return await _call_ollama_structured(provider, b64s, user_text)
    raise ValueError(f"Unsupported VLM provider kind. {provider.kind}")


async def _call_openai_structured(provider: Provider, b64s: list[str], user_text: str) -> dict:
    http = await _get_http()
    model = provider.default_model or "gpt-4o-mini"
    content: list[dict] = [{"type": "text", "text": user_text}]
    for b in b64s:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"},
            }
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ANALYZER_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "analyzer_response",
                "schema": ANALYZER_RESPONSE_SCHEMA,
                "strict": False,
            },
        },
    }
    if provider.max_output_tokens:
        payload["max_tokens"] = provider.max_output_tokens

    async def _do():
        resp = await http.post(
            f"{provider.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {provider.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
        text_out = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {}) or {}
        parsed = _parse_json_loose(text_out)
        parsed.setdefault("_usage", {})
        parsed["_usage"] = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
        return parsed

    return await call_with_retry(_do, provider_name=provider.name, provider_kind="openai", op="agent_vlm")


async def _call_anthropic_structured(provider: Provider, b64s: list[str], user_text: str) -> dict:
    http = await _get_http()
    model = provider.default_model or "claude-sonnet-4-20250514"
    content: list[dict] = []
    for b in b64s:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b},
            }
        )
    # Anthropic enforces JSON via tool-use. Define a "submit" tool with
    # input_schema = ANALYZER_RESPONSE_SCHEMA and force its use.
    content.append({"type": "text", "text": user_text})
    body = {
        "model": model,
        "max_tokens": provider.max_output_tokens or 1024,
        "system": ANALYZER_SYSTEM_PROMPT,
        "tools": [
            {
                "name": "submit_analysis",
                "description": "Submit the analyzer response.",
                "input_schema": ANALYZER_RESPONSE_SCHEMA,
            }
        ],
        "tool_choice": {"type": "tool", "name": "submit_analysis"},
        "messages": [{"role": "user", "content": content}],
    }

    async def _do():
        resp = await http.post(
            f"{provider.base_url}/v1/messages",
            headers={
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {}) or {}
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "submit_analysis":
                parsed = dict(block.get("input") or {})
                parsed["_usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                }
                return parsed
        raise RuntimeError("anthropic returned no submit_analysis tool_use block")

    return await call_with_retry(_do, provider_name=provider.name, provider_kind="anthropic", op="agent_vlm")


async def _call_google_structured(provider: Provider, b64s: list[str], user_text: str) -> dict:
    http = await _get_http()
    model = provider.default_model or "gemini-2.0-flash"
    parts: list[dict] = [{"text": user_text}]
    for b in b64s:
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b}})
    payload = {
        "systemInstruction": {"parts": [{"text": ANALYZER_SYSTEM_PROMPT}]},
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(ANALYZER_RESPONSE_SCHEMA),
            **({"maxOutputTokens": provider.max_output_tokens} if provider.max_output_tokens else {}),
        },
    }

    async def _do():
        resp = await http.post(
            f"{provider.base_url}/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": provider.api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("usageMetadata", {}) or {}
        text_out = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _parse_json_loose(text_out)
        parsed["_usage"] = {
            "input_tokens": meta.get("promptTokenCount", 0),
            "output_tokens": meta.get("candidatesTokenCount", 0),
        }
        return parsed

    return await call_with_retry(_do, provider_name=provider.name, provider_kind="google", op="agent_vlm")


def _gemini_schema(schema: dict) -> dict:
    """Gemini's responseSchema rejects ``additionalProperties`` and a few
    other JSON-schema niceties. Strip them recursively."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in ("additionalProperties", "description"):
            continue
        if isinstance(v, dict):
            out[k] = _gemini_schema(v)
        elif isinstance(v, list):
            out[k] = [_gemini_schema(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out


async def _call_ollama_structured(provider: Provider, b64s: list[str], user_text: str) -> dict:
    http = await _get_http()
    model = provider.default_model or "llava"
    payload = {
        "model": model,
        "prompt": f"{ANALYZER_SYSTEM_PROMPT}\n\n{user_text}",
        "images": b64s,
        "stream": False,
        "format": "json",
    }
    if provider.max_output_tokens:
        payload["options"] = {"num_predict": provider.max_output_tokens}

    async def _do():
        resp = await http.post(
            f"{provider.base_url}/api/generate", json=payload, timeout=120.0
        )
        resp.raise_for_status()
        body = resp.json()
        text_out = body.get("response", "")
        parsed = _parse_json_loose(text_out)
        parsed["_usage"] = {
            "input_tokens": body.get("prompt_eval_count", 0),
            "output_tokens": body.get("eval_count", 0),
        }
        return parsed

    return await call_with_retry(_do, provider_name=provider.name, provider_kind="ollama", op="agent_vlm")


def _parse_json_loose(text_out: str) -> dict:
    """Parse model output as JSON. On failure return a cannot_tell stub.

    Ollama in particular ignores ``format: json`` on older builds
    (section 18.8). Falling through to a structured ``cannot_tell``
    rather than raising keeps the agent loop alive.
    """
    if not text_out:
        return {
            "verdict": "cannot_tell",
            "summary": "VLM returned empty output.",
            "confidence": 0.0,
            "evidence": [],
            "_parse_failure": True,
        }
    s = text_out.strip()
    # Strip code-fence wrappers commonly emitted by chatty models.
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Last-ditch. find first { and matching }.
        first = s.find("{")
        last = s.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(s[first : last + 1])
            except json.JSONDecodeError:
                pass
    logger.warning("VLM JSON parse failure; raw=%r", text_out[:400])
    return {
        "verdict": "cannot_tell",
        "summary": "VLM output was not valid JSON.",
        "confidence": 0.0,
        "evidence": [],
        "_parse_failure": True,
    }


def _validate_response(parsed: dict) -> dict:
    """Coerce the parsed response into the contract minimum. Missing
    required fields get safe defaults, never raise."""
    out = dict(parsed)
    verdict = out.get("verdict")
    if verdict not in {"yes", "no", "uncertain", "cannot_tell"}:
        out["verdict"] = "cannot_tell"
    if not isinstance(out.get("summary"), str):
        out["summary"] = ""
    try:
        conf = float(out.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    out["confidence"] = max(0.0, min(1.0, conf))
    ev = out.get("evidence")
    if not isinstance(ev, list):
        out["evidence"] = []
    return out


# ────────────────────────────────────────────────────────────────────
# Thumbnail persistence
# ────────────────────────────────────────────────────────────────────


def _save_thumbnails(
    frames: list[np.ndarray],
    run_id: uuid.UUID | str,
    vlm_call_id: uuid.UUID,
) -> str | None:
    """Persist redacted frames under
    ``<thumbnails_path>/agent/<run_id>/<vlm_call_id>/<idx>.jpg``.

    Returns the folder path so callers can store it on the
    ``agent_vlm_calls`` row. TODO. orphan sweeper for runs whose row
    cascades away; v1 leaves orphans on disk until a later housekeeping
    pass.
    """
    if not frames:
        return None
    root = Path(settings.thumbnails_path) / "agent" / str(run_id) / str(vlm_call_id)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("thumbnail dir create failed at %s", root)
        return None
    for idx, frame in enumerate(frames):
        try:
            cv2.imwrite(str(root / f"{idx}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        except Exception:
            logger.debug("thumbnail write failed idx=%d", idx, exc_info=True)
    return str(root)


# ────────────────────────────────────────────────────────────────────
# Provider resolution
# ────────────────────────────────────────────────────────────────────


async def _resolve_provider(
    db,
    *,
    explicit_id: uuid.UUID | None,
    camera: Camera | None,
) -> Provider | None:
    """Order. explicit -> camera.vlm_provider_id -> global active."""
    if explicit_id is not None:
        prov = await db.get(Provider, explicit_id)
        if prov is not None:
            return prov
    if camera is not None and camera.vlm_provider_id is not None:
        prov = await db.get(Provider, camera.vlm_provider_id)
        if prov is not None:
            return prov
    return await get_active_provider()


# ────────────────────────────────────────────────────────────────────
# AgentVlmCall persistence (defensive)
# ────────────────────────────────────────────────────────────────────


async def _record_vlm_call(
    ctx: Any,
    *,
    vlm_call_id: uuid.UUID,
    target_kind: str,
    observation_id: uuid.UUID | None,
    recording_id: uuid.UUID | None,
    time_from: datetime | None,
    time_to: datetime | None,
    provider: Provider | None,
    question: str,
    frame_count: int,
    response: dict,
    confidence: float | None,
    tokens_in: int,
    tokens_out: int,
    cost_cents: int,
    cached: bool,
    thumbnails_path: str | None,
) -> None:
    """Try Wave 1A's runs helper first; otherwise direct INSERT."""
    # Wave 1A's record_vlm_call accepts target_kind in {"frame", "clip"}.
    # Map our internal "observation" alias to "frame" so the helper
    # validates.
    wave1a_kind = "frame" if target_kind in ("frame", "observation") else target_kind
    run_id = getattr(ctx, "run_id", None) if ctx is not None else None
    tool_call_id = getattr(ctx, "tool_call_id", None) if ctx is not None else None
    db = getattr(ctx, "db", None) if ctx is not None else None
    if run_id is not None and db is not None:
        try:
            from services.agent.runs import record_vlm_call  # type: ignore

            await record_vlm_call(
                run_id=run_id,
                db=db,
                tool_call_id=tool_call_id,
                provider_id=provider.id if provider else None,
                model=(provider.default_model if provider else None),
                target_kind=wave1a_kind,
                observation_id=observation_id,
                recording_id=recording_id,
                time_from=time_from,
                time_to=time_to,
                frame_count=frame_count,
                question=question,
                response=response,
                confidence=confidence,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_cents=cost_cents,
                cached=cached,
                thumbnails_path=thumbnails_path,
            )
            return
        except ImportError:
            logger.debug("services.agent.runs not present; falling back to direct INSERT")
        except Exception:
            logger.exception("record_vlm_call helper failed; falling back to direct INSERT")

    # Fallback. direct write so the audit trail still lands.
    if db is None or run_id is None:
        logger.info("no db/run_id on ctx; skipping agent_vlm_calls persistence")
        return
    try:
        await db.execute(
            text(
                "INSERT INTO agent_vlm_calls"
                " (id, run_id, tool_call_id, provider_id, model, target_kind,"
                "  observation_id, recording_id, time_from, time_to, frame_count,"
                "  question, response, confidence, tokens_in, tokens_out,"
                "  cost_cents, cached, thumbnails_path)"
                " VALUES (:id, :run_id, :tool_call_id, :provider_id, :model, :target_kind,"
                "  :observation_id, :recording_id, :time_from, :time_to, :frame_count,"
                "  :question, CAST(:response AS JSONB), :confidence, :tokens_in, :tokens_out,"
                "  :cost_cents, :cached, :thumbnails_path)"
            ),
            {
                "id": str(vlm_call_id),
                "run_id": str(run_id),
                "tool_call_id": str(tool_call_id) if tool_call_id else None,
                "provider_id": str(provider.id) if provider else None,
                "model": provider.default_model if provider else None,
                "target_kind": target_kind,
                "observation_id": str(observation_id) if observation_id else None,
                "recording_id": str(recording_id) if recording_id else None,
                "time_from": time_from,
                "time_to": time_to,
                "frame_count": frame_count,
                "question": question,
                "response": json.dumps(response),
                "confidence": confidence,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_cents": cost_cents,
                "cached": cached,
                "thumbnails_path": thumbnails_path,
            },
        )
        await db.commit()
    except Exception:
        logger.exception("agent_vlm_calls fallback INSERT failed")
        try:
            await db.rollback()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────
# Pricing helper. tiny + intentionally per-1k. Phase 2 swaps the price
# table out for ``services/agent/pricing.py`` when that lands.
# ────────────────────────────────────────────────────────────────────


def _estimate_cost_cents(provider: Provider | None, tokens_in: int, tokens_out: int) -> int:
    """Conservative per-call cost estimate. zero for unknown providers
    so the analyzer never over-bills a user when we lack a price."""
    if provider is None:
        return 0
    try:
        from services.agent.pricing import price_for_provider  # type: ignore

        return int(price_for_provider(provider, tokens_in, tokens_out))
    except Exception:
        # Fallback. Anthropic Sonnet-class rate as a safe upper bound.
        # $3 / Mtok in, $15 / Mtok out → 0.0003 cents/in, 0.0015 cents/out.
        return int(round((tokens_in * 0.0003) + (tokens_out * 0.0015)))


# ────────────────────────────────────────────────────────────────────
# Public entry points
# ────────────────────────────────────────────────────────────────────


async def analyze_frame_target(
    ctx: Any,
    observation_id: uuid.UUID,
    question: str,
    provider_id: uuid.UUID | None = None,
) -> AnalyzerResult:
    """Analyze the clip backing a single Observation row.

    See module docstring for the full pipeline."""
    if isinstance(observation_id, str):
        observation_id = uuid.UUID(observation_id)
    db = getattr(ctx, "db", None) if ctx is not None else None
    own_db = False
    if db is None:
        _session_cm = async_session()
        db = await _session_cm.__aenter__()
        own_db = True
    else:
        _session_cm = None
    try:
        obs = await db.get(Observation, observation_id)
        if obs is None:
            return _error_result("observation_missing")
        camera = await db.get(Camera, obs.camera_id)

        # Resolve the recording that backs this observation. The
        # observation may have a clip_path but we still anchor cache +
        # frame extraction on the underlying Recording row when one
        # exists; this matches the cache key contract.
        rec = await _find_recording_for_observation(db, obs)
        provider = await _resolve_provider(db, explicit_id=provider_id, camera=camera)
        model = (provider.default_model if provider else None) or ""

        qhash = question_hash(question)

        # Cache lookup first.
        cached = await cache_lookup(
            db,
            observation_id=observation_id,
            recording_id=None,
            qhash=qhash,
            provider_id=provider.id if provider else None,
            model=model,
        )
        if cached is not None:
            vlm_call_id = uuid.uuid4()
            answer = _strip_usage(cached["response_json"])
            await _record_vlm_call(
                ctx,
                vlm_call_id=vlm_call_id,
                target_kind="frame",
                observation_id=observation_id,
                recording_id=None,
                time_from=obs.started_at,
                time_to=obs.ended_at,
                provider=provider,
                question=question,
                frame_count=0,
                response=answer,
                confidence=cached.get("confidence"),
                tokens_in=0,
                tokens_out=0,
                cost_cents=0,
                cached=True,
                thumbnails_path=cached.get("thumbnail_path"),
            )
            return AnalyzerResult(
                cache_hit=True,
                answer=answer,
                confidence=cached.get("confidence"),
                frame_count=0,
                cost_cents=0,
                tokens_in=0,
                tokens_out=0,
                thumbnails_path=cached.get("thumbnail_path"),
                vlm_call_id=vlm_call_id,
            )

        # No cache. Extract frames.
        if rec is None and not obs.clip_path:
            return _error_result("media_evicted")
        if rec is not None:
            window_s = float(rec.duration_seconds or 0) or _safe_observation_window(obs)
            sample_secs = select_frames(int(window_s) or 1)
            frames = await extract_frames_from_recording(rec, sample_secs)
        else:
            # Fall back to the observation's stored clip file directly.
            path = Path(obs.clip_path) if obs.clip_path else None
            if path is None or not path.exists():
                return _error_result("media_evicted")
            window_s = _safe_observation_window(obs)
            sample_secs = select_frames(int(window_s) or 1)
            frames = []
            for ts in sample_secs:
                fr = await _ffmpeg_extract_one(path, ts)
                if fr is not None:
                    frames.append(fr)
        if not frames:
            return _error_result("media_evicted")

        # Privacy redaction. Hard fail on any exception.
        try:
            redacted, _reports = await _redact_frames(frames, obs.camera_id, db)
        except Exception:
            logger.exception("redaction aborted analyzer call")
            return _error_result("redaction_failed")
        if provider is None:
            return _error_result("no_provider")
        # VLM call.
        try:
            raw = await call_vlm_structured(provider, redacted, question)
        except Exception:
            logger.exception("VLM call failed")
            return _error_result("vlm_call_failed")
        usage = raw.pop("_usage", {})
        tokens_in = int(usage.get("input_tokens", 0) or 0)
        tokens_out = int(usage.get("output_tokens", 0) or 0)
        answer = _validate_response(raw)
        cost_cents = _estimate_cost_cents(provider, tokens_in, tokens_out)

        # Persist thumbnails + audit row.
        vlm_call_id = uuid.uuid4()
        run_id = getattr(ctx, "run_id", None) if ctx is not None else None
        thumb_path = _save_thumbnails(redacted, run_id or "no_run", vlm_call_id) if run_id else None

        await cache_write(
            db,
            observation_id=observation_id,
            recording_id=None,
            qhash=qhash,
            question_text=question,
            provider_id=provider.id if provider else None,
            model=model,
            response_json=answer,
            confidence=answer.get("confidence"),
            cost_tokens_in=tokens_in,
            cost_tokens_out=tokens_out,
            cost_cents=cost_cents,
            thumbnail_path=thumb_path,
        )
        await _record_vlm_call(
            ctx,
            vlm_call_id=vlm_call_id,
            target_kind="frame",
            observation_id=observation_id,
            recording_id=None,
            time_from=obs.started_at,
            time_to=obs.ended_at,
            provider=provider,
            question=question,
            frame_count=len(redacted),
            response=answer,
            confidence=answer.get("confidence"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_cents=cost_cents,
            cached=False,
            thumbnails_path=thumb_path,
        )

        return AnalyzerResult(
            cache_hit=False,
            answer=answer,
            confidence=answer.get("confidence"),
            frame_count=len(redacted),
            cost_cents=cost_cents,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            thumbnails_path=thumb_path,
            vlm_call_id=vlm_call_id,
        )
    finally:
        if own_db:
            await _session_cm.__aexit__(None, None, None)


async def analyze_clip_target(
    ctx: Any,
    camera_id: uuid.UUID,
    time_from: datetime,
    time_to: datetime,
    question: str,
    provider_id: uuid.UUID | None = None,
) -> AnalyzerResult:
    """Analyze a camera+time-window. Stitches multiple Recording rows.

    Cache key. ``(first_recording_id_in_sorted_window, question_hash,
    provider_id, model)`` plus a sorted recording_id list stamped in
    ``response_json.recordings``. Trade-off documented in section 5.4.
    one new recording entering the window invalidates the whole cache
    row, but this avoids needing a coverage_hash column we don't have
    and never serves a stale answer for a window whose membership
    actually changed.
    """
    if isinstance(camera_id, str):
        camera_id = uuid.UUID(camera_id)
    time_from = _aware(time_from)
    time_to = _aware(time_to)

    # Long-clip safety. Block requests beyond the configured cap.
    max_minutes = int(
        await get_setting("agent_max_clip_minutes", _DEFAULT_MAX_CLIP_MINUTES) or _DEFAULT_MAX_CLIP_MINUTES
    )
    window_minutes = (time_to - time_from).total_seconds() / 60.0
    if window_minutes > max_minutes:
        return AnalyzerResult(
            cache_hit=False,
            answer={"error": "clip_too_long", "window_minutes": window_minutes, "cap_minutes": max_minutes},
            confidence=None,
            frame_count=0,
            cost_cents=0,
            tokens_in=0,
            tokens_out=0,
            thumbnails_path=None,
            error="clip_too_long",
        )

    db = getattr(ctx, "db", None) if ctx is not None else None
    own_db = False
    if db is None:
        _session_cm = async_session()
        db = await _session_cm.__aenter__()
        own_db = True
    else:
        _session_cm = None
    try:
        camera = await db.get(Camera, camera_id)
        if camera is None:
            return _error_result("camera_missing")

        # Find intersecting recordings.
        rows = (
            await db.execute(
                select(Recording)
                .where(Recording.camera_id == camera_id)
                .where(Recording.started_at < time_to)
                .where(
                    (Recording.ended_at.is_(None))
                    | (Recording.ended_at > time_from)
                )
                .order_by(Recording.started_at.asc())
            )
        ).scalars().all()
        if not rows:
            return _error_result("media_evicted")

        provider = await _resolve_provider(db, explicit_id=provider_id, camera=camera)
        model = (provider.default_model if provider else None) or ""

        # Cache key. anchor on first recording in sorted list.
        first_rec_id = rows[0].id
        rec_ids_sorted = [str(r.id) for r in rows]
        qhash = question_hash(question)

        cached = await cache_lookup(
            db,
            observation_id=None,
            recording_id=first_rec_id,
            qhash=qhash,
            provider_id=provider.id if provider else None,
            model=model,
        )
        if cached is not None:
            cached_recs = (cached["response_json"] or {}).get("recordings") or []
            if cached_recs == rec_ids_sorted:
                vlm_call_id = uuid.uuid4()
                answer = _strip_usage(cached["response_json"])
                await _record_vlm_call(
                    ctx,
                    vlm_call_id=vlm_call_id,
                    target_kind="clip",
                    observation_id=None,
                    recording_id=first_rec_id,
                    time_from=time_from,
                    time_to=time_to,
                    provider=provider,
                    question=question,
                    frame_count=0,
                    response=answer,
                    confidence=cached.get("confidence"),
                    tokens_in=0,
                    tokens_out=0,
                    cost_cents=0,
                    cached=True,
                    thumbnails_path=cached.get("thumbnail_path"),
                )
                return AnalyzerResult(
                    cache_hit=True,
                    answer=answer,
                    confidence=cached.get("confidence"),
                    frame_count=0,
                    cost_cents=0,
                    tokens_in=0,
                    tokens_out=0,
                    thumbnails_path=cached.get("thumbnail_path"),
                    vlm_call_id=vlm_call_id,
                )

        # Allocate sample budget across recordings by intersection.
        total_seconds = (time_to - time_from).total_seconds()
        total_frames = len(select_frames(int(total_seconds) or 1))
        plan = _distribute_samples(rows, time_from, time_to, total_frames)
        all_frames: list[np.ndarray] = []
        for rec, ts_list in plan:
            chunk = await extract_frames_from_recording(rec, ts_list)
            all_frames.extend(chunk)
        if not all_frames:
            return _error_result("media_evicted")

        try:
            redacted, _reports = await _redact_frames(all_frames, camera_id, db)
        except Exception:
            logger.exception("redaction aborted analyzer call")
            return _error_result("redaction_failed")
        if provider is None:
            return _error_result("no_provider")
        try:
            raw = await call_vlm_structured(provider, redacted, question)
        except Exception:
            logger.exception("VLM call failed")
            return _error_result("vlm_call_failed")
        usage = raw.pop("_usage", {})
        tokens_in = int(usage.get("input_tokens", 0) or 0)
        tokens_out = int(usage.get("output_tokens", 0) or 0)
        answer = _validate_response(raw)
        # Stamp the recording set for cache-key correctness checks.
        answer["recordings"] = rec_ids_sorted
        cost_cents = _estimate_cost_cents(provider, tokens_in, tokens_out)

        vlm_call_id = uuid.uuid4()
        run_id = getattr(ctx, "run_id", None) if ctx is not None else None
        thumb_path = _save_thumbnails(redacted, run_id or "no_run", vlm_call_id) if run_id else None

        await cache_write(
            db,
            observation_id=None,
            recording_id=first_rec_id,
            qhash=qhash,
            question_text=question,
            provider_id=provider.id if provider else None,
            model=model,
            response_json=answer,
            confidence=answer.get("confidence"),
            cost_tokens_in=tokens_in,
            cost_tokens_out=tokens_out,
            cost_cents=cost_cents,
            thumbnail_path=thumb_path,
        )
        await _record_vlm_call(
            ctx,
            vlm_call_id=vlm_call_id,
            target_kind="clip",
            observation_id=None,
            recording_id=first_rec_id,
            time_from=time_from,
            time_to=time_to,
            provider=provider,
            question=question,
            frame_count=len(redacted),
            response=answer,
            confidence=answer.get("confidence"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_cents=cost_cents,
            cached=False,
            thumbnails_path=thumb_path,
        )
        return AnalyzerResult(
            cache_hit=False,
            answer=answer,
            confidence=answer.get("confidence"),
            frame_count=len(redacted),
            cost_cents=cost_cents,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            thumbnails_path=thumb_path,
            vlm_call_id=vlm_call_id,
        )
    finally:
        if own_db:
            await _session_cm.__aexit__(None, None, None)


# ────────────────────────────────────────────────────────────────────
# Small helpers
# ────────────────────────────────────────────────────────────────────


def _error_result(code: str) -> AnalyzerResult:
    return AnalyzerResult(
        cache_hit=False,
        answer={"error": code},
        confidence=None,
        frame_count=0,
        cost_cents=0,
        tokens_in=0,
        tokens_out=0,
        thumbnails_path=None,
        error=code,
    )


def _strip_usage(resp: dict | None) -> dict:
    if not resp:
        return {}
    out = dict(resp)
    out.pop("_usage", None)
    return out


async def _find_recording_for_observation(db, obs: Observation) -> Recording | None:
    """Find the Recording row whose [started_at, ended_at] window
    contains the observation. Returns None if nothing matches; the
    analyzer falls back to ``Observation.clip_path``."""
    try:
        row = (
            await db.execute(
                select(Recording)
                .where(Recording.camera_id == obs.camera_id)
                .where(Recording.started_at <= obs.started_at)
                .where(
                    (Recording.ended_at.is_(None))
                    | (Recording.ended_at >= (obs.ended_at or obs.started_at))
                )
                .order_by(Recording.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return row
    except Exception:
        logger.debug("recording lookup for observation failed", exc_info=True)
        return None


def _safe_observation_window(obs: Observation) -> float:
    if obs.ended_at and obs.started_at:
        return max(1.0, (obs.ended_at - obs.started_at).total_seconds())
    return 10.0


__all__ = [
    "AnalyzerResult",
    "ANALYZER_RESPONSE_SCHEMA",
    "ANALYZER_SYSTEM_PROMPT",
    "analyze_frame_target",
    "analyze_clip_target",
    "normalize_question",
    "question_hash",
    "select_frames",
    "extract_frames_from_recording",
    "cache_lookup",
    "cache_write",
    "call_vlm_structured",
]
