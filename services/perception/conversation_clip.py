"""Build a single mp4 clip covering a conversation window.

Called from the conversation finalizer after the summary VLM call
succeeds. Best-effort. when no overlapping recordings exist, or when
ffmpeg fails, the conversation row simply gets no clip_path and the
UI hides the video player.

Strategy.
- Find the Recording rows on the same camera that overlap
  [started_at, ended_at].
- For each overlapping recording, ffmpeg-trim its overlap slice into
  a per-segment intermediate. When only one segment exists we skip
  the concat step.
- ffmpeg concat-demuxer joins the segment intermediates into a
  single mp4 covering the full conversation window. If the
  conversation spans a recording rotation (e.g. on_motion clips that
  end mid-conversation, or hourly rotation), the final clip stitches
  the pieces together.
- Stream-copy where possible, fall back to libx264/aac re-encode
  when stream-copy produces an unplayable cut.
- Output to ``recordings/clips/<camera>/<conv_id>.mp4``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from shared.config import settings
from shared.database import async_session
from shared.models import Conversation, Recording

logger = logging.getLogger("nurby.perception.conversation_clip")


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


async def build_clip_for_conversation(
    conversation_id: uuid.UUID,
    camera_id: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
) -> tuple[str, int] | None:
    """Build the conversation clip and return (path, duration_ms) on
    success. Returns None when no source recordings are available, the
    binary is missing, or ffmpeg returns non-zero.

    Handles the multi-segment case. when the conversation spans a
    recording rotation, each overlapping Recording row contributes a
    trimmed segment and the segments are concat-demuxed into a single
    mp4.
    """
    if not _has_ffmpeg():
        logger.debug("ffmpeg not on PATH, skipping clip build")
        return None
    if ended_at <= started_at:
        return None

    overlaps = await _all_overlapping_recordings(camera_id, started_at, ended_at)
    if not overlaps:
        return None

    out_dir = os.path.join(
        os.path.abspath(settings.recordings_path),
        "clips",
        str(camera_id),
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{conversation_id}.mp4")

    if len(overlaps) == 1:
        rec, ss, dur = overlaps[0]
        src = _resolve_path(rec.file_path)
        if not src or not os.path.exists(src):
            return None
        ok = await _trim_segment(src, ss, dur, out_path)
        if not ok:
            _silent_remove(out_path)
            return None
    else:
        # Multi-segment concat. Trim each piece into a temp file, then
        # ffmpeg concat demuxer.
        tmp_dir = os.path.join(out_dir, f".{conversation_id}.parts")
        os.makedirs(tmp_dir, exist_ok=True)
        part_paths: list[str] = []
        try:
            for i, (rec, ss, dur) in enumerate(overlaps):
                src = _resolve_path(rec.file_path)
                if not src or not os.path.exists(src):
                    continue
                part_path = os.path.join(tmp_dir, f"{i:03d}.mp4")
                ok = await _trim_segment(src, ss, dur, part_path)
                if ok:
                    part_paths.append(part_path)
            if not part_paths:
                return None
            if len(part_paths) == 1:
                # Only one part survived. Move it instead of concat.
                os.replace(part_paths[0], out_path)
            else:
                concat_list = os.path.join(tmp_dir, "concat.txt")
                with open(concat_list, "w") as f:
                    for p in part_paths:
                        # Quote per ffmpeg concat demuxer rules. Escape
                        # single quotes by closing+escape+reopen.
                        safe = p.replace("'", "'\\''")
                        f.write(f"file '{safe}'\n")
                rc = await _run_ffmpeg(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
                        "-f", "concat", "-safe", "0",
                        "-i", concat_list,
                        "-c", "copy",
                        "-movflags", "+faststart",
                        out_path,
                    ]
                )
                if rc != 0 or not _file_ok(out_path):
                    # Re-encode the concat. Sometimes parts have
                    # different SPS/PPS and stream-copy concat fails.
                    rc = await _run_ffmpeg(
                        [
                            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
                            "-f", "concat", "-safe", "0",
                            "-i", concat_list,
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                            "-c:a", "aac",
                            "-movflags", "+faststart",
                            out_path,
                        ]
                    )
                    if rc != 0 or not _file_ok(out_path):
                        _silent_remove(out_path)
                        return None
        finally:
            # Clean up part files regardless of outcome.
            for p in part_paths:
                _silent_remove(p)
            _silent_remove(os.path.join(tmp_dir, "concat.txt"))
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    duration_s = (ended_at - started_at).total_seconds()
    duration_ms = int(duration_s * 1000)
    logger.info(
        "conversation clip built. conv=%s file=%s segs=%d dur_ms=%d size=%dB",
        conversation_id, out_path, len(overlaps), duration_ms,
        os.path.getsize(out_path) if os.path.exists(out_path) else 0,
    )
    return out_path, duration_ms


async def _trim_segment(src: str, ss: float, duration: float, dst: str) -> bool:
    """Trim ``src`` from offset ``ss`` for ``duration`` seconds into
    ``dst``. Stream-copy first, libx264/aac fallback. Returns True on
    success.
    """
    if duration <= 0:
        return False
    rc = await _run_ffmpeg(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-ss", f"{ss:.3f}",
            "-i", src,
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-movflags", "+faststart",
            dst,
        ]
    )
    if rc == 0 and _file_ok(dst):
        return True
    rc = await _run_ffmpeg(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-ss", f"{ss:.3f}",
            "-i", src,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac",
            "-movflags", "+faststart",
            dst,
        ]
    )
    if rc == 0 and _file_ok(dst):
        return True
    _silent_remove(dst)
    return False


async def _all_overlapping_recordings(
    camera_id: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
) -> list[tuple[Recording, float, float]]:
    """Return every recording overlapping the window, paired with the
    (offset_into_source_seconds, duration_seconds) tuple for the
    overlap. Sorted by recording start so concat order matches wall
    clock order.
    """
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Recording)
                .where(Recording.camera_id == camera_id)
                .where(Recording.started_at <= ended_at)
                .order_by(Recording.started_at.asc())
                .limit(50)
            )
        ).scalars().all()
    out: list[tuple[Recording, float, float]] = []
    for r in rows:
        rec_start = r.started_at
        if rec_start.tzinfo is None:
            rec_start = rec_start.replace(tzinfo=timezone.utc)
        rec_end = r.ended_at or datetime.now(timezone.utc)
        if rec_end.tzinfo is None:
            rec_end = rec_end.replace(tzinfo=timezone.utc)
        if rec_end <= started_at:
            continue
        clip_start = max(rec_start, started_at)
        clip_end = min(rec_end, ended_at)
        overlap = (clip_end - clip_start).total_seconds()
        if overlap <= 0:
            continue
        ss = max(0.0, (clip_start - rec_start).total_seconds())
        out.append((r, ss, overlap))
    return out


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _resolve_path(stored: str | None) -> str | None:
    if not stored:
        return None
    if os.path.isabs(stored):
        return stored
    rel = stored
    for prefix in ("./recordings/", "recordings/", "./"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    return os.path.join(os.path.abspath(settings.recordings_path), rel)


def _file_ok(path: str) -> bool:
    try:
        return os.path.exists(path) and os.path.getsize(path) > 1024
    except OSError:
        return False


async def _run_ffmpeg(cmd: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 and stderr:
        logger.debug("ffmpeg stderr: %s", stderr.decode("utf-8", errors="ignore")[:500])
    return proc.returncode or 0


async def patch_conversation_clip(
    conversation_id: uuid.UUID,
    clip_path: str,
    duration_ms: int,
) -> None:
    try:
        async with async_session() as db:
            row = await db.get(Conversation, conversation_id)
            if row is None:
                return
            row.clip_path = clip_path
            row.clip_duration_ms = duration_ms
            await db.commit()
    except Exception:
        logger.exception("clip patch failed conv=%s", conversation_id)
