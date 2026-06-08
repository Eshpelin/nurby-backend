"""Guardian clip privacy transform.

The image endpoint can leave the bound dependant's face sharp because it works
on a single frame. A clip is many frames, so the safe default is the same one
the image path started from: blur the whole frame so no face in the recording
is identifiable, while motion, clothing colour, and place stay legible. Audio is
dropped (it is a separate entitlement and can leak other people's voices).

Blurring video is expensive, so each source clip is transcoded once with
ffmpeg's ``gblur`` and cached by a content key (path + mtime + size + sigma).
Subsequent requests serve the cached file. A facility that explicitly accepts
raw footage can still bypass this via ``guardian_unblurred_clips_enabled``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess

from shared.config import settings

DEFAULT_SIGMA = 20
CACHE_DIR = os.path.join(settings.recordings_path, "guardian_blurred")


def _cache_key(src_path: str, sigma: int) -> str:
    try:
        st = os.stat(src_path)
        sig = f"{os.path.abspath(src_path)}|{st.st_mtime_ns}|{st.st_size}|{sigma}"
    except OSError:
        sig = f"{os.path.abspath(src_path)}|{sigma}"
    return hashlib.sha256(sig.encode()).hexdigest()[:24]


def blur_clip(src_path: str, sigma: int = DEFAULT_SIGMA) -> str:
    """Return the path to a Gaussian-blurred, audio-stripped copy of the clip,
    transcoding with ffmpeg on first request and caching the result. Raises
    ``RuntimeError`` if ffmpeg fails or produces no output."""
    sigma = max(1, int(sigma))
    os.makedirs(CACHE_DIR, exist_ok=True)
    dest = os.path.join(CACHE_DIR, f"{_cache_key(src_path, sigma)}.mp4")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    tmp = f"{dest}.{os.getpid()}.tmp.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", src_path,
        "-vf", f"gblur=sigma={sigma}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        tmp,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _unlink(tmp)
        raise RuntimeError(f"clip blur failed: {exc}") from exc
    if proc.returncode != 0 or not (os.path.exists(tmp) and os.path.getsize(tmp) > 0):
        _unlink(tmp)
        raise RuntimeError(
            f"ffmpeg gblur failed rc={proc.returncode}: {proc.stderr.decode()[:300]}"
        )
    os.replace(tmp, dest)
    return dest


async def blur_clip_async(src_path: str, sigma: int = DEFAULT_SIGMA) -> str:
    """Async wrapper. Runs the blocking transcode off the event loop."""
    return await asyncio.to_thread(blur_clip, src_path, sigma)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
