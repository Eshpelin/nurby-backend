"""Tests for services.guardian.presence.

Pure state logic is tested directly. The DB query path is exercised with an
AsyncMock-shaped session so the suite stays DB-free.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.guardian import presence

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


# ── pure derive_state ────────────────────────────────────────────────

def test_unknown_when_never_seen():
    s = presence.derive_state(None, cutoff=NOW, now=NOW)
    assert s["state"] == "unknown"
    assert s["last_seen_at"] is None
    assert s["seconds_ago"] is None


def test_present_when_recent():
    seen = NOW - timedelta(minutes=2)
    s = presence.derive_state(seen, cutoff=NOW, now=NOW)
    assert s["state"] == "present"
    assert s["seconds_ago"] == 120
    assert s["delayed"] is False


def test_away_when_old():
    seen = NOW - timedelta(hours=3)
    s = presence.derive_state(seen, cutoff=NOW, now=NOW)
    assert s["state"] == "away"


def test_delayed_flag_for_free_cutoff():
    cutoff = NOW - timedelta(seconds=1800)
    seen = cutoff - timedelta(minutes=2)  # fresh relative to cutoff
    s = presence.derive_state(seen, cutoff=cutoff, now=NOW)
    assert s["state"] == "present"
    assert s["delayed"] is True
    # honest seconds_ago relative to real now is > 30 min
    assert s["seconds_ago"] >= 1800


def test_fresh_window_uses_cutoff_not_now():
    # A sighting 5 min before cutoff is "present" even though it is 35 min
    # before real now (free tier).
    cutoff = NOW - timedelta(seconds=1800)
    seen = cutoff - timedelta(minutes=5)
    s = presence.derive_state(seen, cutoff=cutoff, now=NOW)
    assert s["state"] == "present"


# ── DB-backed dependant_status ───────────────────────────────────────

def _link(**kw):
    base = dict(live_presence=False, revoked_at=None, expires_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _mock_db(observation, camera):
    """Return an AsyncMock db whose two execute() calls yield the observation
    then the camera."""
    db = MagicMock()
    results = []
    for obj in (observation, camera):
        r = MagicMock()
        r.scalar_one_or_none.return_value = obj
        results.append(r)
    db.execute = AsyncMock(side_effect=results)
    return db


@pytest.mark.asyncio
async def test_status_present_with_zone():
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed", nickname=None)
    cam = SimpleNamespace(id=uuid.uuid4(), name="Cam B", location_label="Classroom B")
    obs = SimpleNamespace(
        id=uuid.uuid4(),
        camera_id=cam.id,
        started_at=NOW - timedelta(minutes=1),
        thumbnail_path="x.jpg",
    )
    db = _mock_db(obs, cam)
    out = await presence.dependant_status(
        db, _link(live_presence=True), person, NOW, free_delay_seconds=1800
    )
    assert out["state"] == "present"
    assert out["zone"] == "Classroom B"
    assert out["display_name"] == "Ahmed"
    assert out["delayed"] is False


@pytest.mark.asyncio
async def test_status_unknown_when_no_obs():
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed", nickname=None)
    db = MagicMock()
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=r)
    out = await presence.dependant_status(
        db, _link(), person, NOW, free_delay_seconds=1800
    )
    assert out["state"] == "unknown"
    assert out["zone"] is None
    assert out["observation_id"] is None


@pytest.mark.asyncio
async def test_status_empty_allowed_cameras_is_unknown():
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed", nickname=None)
    db = MagicMock()
    db.execute = AsyncMock()  # must NOT be called when allowed list is empty
    out = await presence.dependant_status(
        db, _link(), person, NOW, free_delay_seconds=1800, allowed_camera_ids=[]
    )
    assert out["state"] == "unknown"
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_nickname_preferred():
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed Anough", nickname="Ammu")
    db = MagicMock()
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=r)
    out = await presence.dependant_status(db, _link(), person, NOW, free_delay_seconds=1800)
    assert out["display_name"] == "Ammu"
