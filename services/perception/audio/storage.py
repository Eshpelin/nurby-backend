"""Opt-in raw audio storage for transcripts.

Encodes a SpeechSegment's PCM as Opus on disk under
``settings.audio_storage_path / <camera_id> / <yyyy-mm-dd> / <id>.opus``.
Returns ``(file_path, size_bytes)`` for the audio_captures row.

We use ffmpeg via subprocess instead of PyAV so there is no shared
state between the encoder and the capture pump. PyAV's encode path
holds GIL longer and is fussier with mono int16 input.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime

from services.perception.audio.constants import (
    AUDIO_OPUS_BITRATE_KBPS,
    AUDIO_SAMPLE_RATE_HZ,
)
from services.perception.audio.types import SpeechSegment
from shared.config import settings

logger = logging.getLogger("nurby.perception.audio.storage")


async def write_opus(segment: SpeechSegment, capture_id: uuid.UUID) -> tuple[str, int] | None:
    """Encode a segment to Opus and persist. Returns None on failure."""
    base = settings.audio_storage_path
    day = segment.started_at.strftime("%Y-%m-%d")
    folder = os.path.join(base, str(segment.camera_id), day)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, f"{capture_id}.opus")

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-loglevel", "error",
            "-y",
            "-f", "s16le",
            "-ar", str(AUDIO_SAMPLE_RATE_HZ),
            "-ac", "1",
            "-i", "pipe:0",
            "-c:a", "libopus",
            "-b:a", f"{AUDIO_OPUS_BITRATE_KBPS}k",
            file_path,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("ffmpeg not on PATH. raw audio storage disabled")
        return None

    try:
        _, err = await asyncio.wait_for(proc.communicate(input=segment.pcm), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("ffmpeg opus encode timed out for camera %s", segment.camera_id)
        return None

    if proc.returncode != 0:
        logger.warning(
            "ffmpeg opus encode failed for camera %s rc=%s err=%s",
            segment.camera_id,
            proc.returncode,
            (err or b"").decode("utf-8", "ignore")[:200],
        )
        try:
            os.remove(file_path)
        except OSError:
            pass
        return None

    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = 0
    return file_path, size
