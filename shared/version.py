"""Application version + simple semantic-version comparison.

The version is read from the repo-root VERSION file (bumped on release).
An optional NURBY_BUILD_SHA env var, baked at image build time, adds the
exact build for support and update diffs.
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def current_version() -> str:
    try:
        v = (_REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        return v or "0.0.0"
    except OSError:
        return "0.0.0"


def build_sha() -> str:
    return os.environ.get("NURBY_BUILD_SHA", "").strip()


def _parts(v: str) -> list[int]:
    out: list[int] = []
    for chunk in (v or "").lstrip("v").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    return out


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly higher version than
    ``current``. Tolerant of a leading 'v' and pre-release suffixes."""
    a, b = _parts(candidate), _parts(current)
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a > b
