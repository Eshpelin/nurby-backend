"""Tests for services.guardian.lifecycle. perception -> guardian bridge."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.guardian import lifecycle


def _person():
    return SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed", nickname=None)


def _link(pid, active=True):
    return SimpleNamespace(
        id=uuid.uuid4(), person_id=pid, revoked_at=None,
        expires_at=None, tier="full", alert_prefs=None,
        live_presence=False, premium=False, live_video=False, audio=False,
        is_primary_parent=False,
    )


def _patch_session(monkeypatch, db):
    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(lifecycle, "async_session", fake_session)


@pytest.mark.asyncio
async def test_skips_non_person():
    out = await lifecycle.notify_journey_event("arrived", "cluster", "Unknown 5")
    assert out is None


@pytest.mark.asyncio
async def test_skips_unknown_kind():
    out = await lifecycle.notify_journey_event("loitered", "person", "Ahmed")
    assert out is None


@pytest.mark.asyncio
async def test_emits_for_person_with_active_link(monkeypatch):
    person = _person()
    link = _link(person.id)
    db = MagicMock()
    # first execute -> person, second execute -> links
    pres = MagicMock()
    pres.scalars.return_value.first.return_value = person
    lres = MagicMock()
    lres.scalars.return_value.all.return_value = [link]
    db.execute = AsyncMock(side_effect=[pres, lres])
    db.get = AsyncMock(return_value=SimpleNamespace(location_label="Gate", name="Cam"))
    _patch_session(monkeypatch, db)

    captured = {}

    async def fake_emit(_db, p, kind, links, **kw):
        captured["kind"] = kind
        captured["zone"] = kw.get("zone")
        captured["n"] = len(links)
        return {"recipients": ["x"]}

    monkeypatch.setattr(lifecycle.alerts_mod, "emit", fake_emit)
    out = await lifecycle.notify_journey_event("arrived", "person", "Ahmed", uuid.uuid4())
    assert out == {"recipients": ["x"]}
    assert captured == {"kind": "arrived", "zone": "Gate", "n": 1}


@pytest.mark.asyncio
async def test_noop_when_no_active_links(monkeypatch):
    person = _person()
    revoked = _link(person.id)
    revoked.revoked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    db = MagicMock()
    pres = MagicMock()
    pres.scalars.return_value.first.return_value = person
    lres = MagicMock()
    lres.scalars.return_value.all.return_value = [revoked]
    db.execute = AsyncMock(side_effect=[pres, lres])
    _patch_session(monkeypatch, db)
    emit = AsyncMock()
    monkeypatch.setattr(lifecycle.alerts_mod, "emit", emit)
    out = await lifecycle.notify_journey_event("departed", "person", "Ahmed")
    assert out is None
    emit.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_person_noop(monkeypatch):
    db = MagicMock()
    pres = MagicMock()
    pres.scalars.return_value.first.return_value = None
    db.execute = AsyncMock(return_value=pres)
    _patch_session(monkeypatch, db)
    out = await lifecycle.notify_journey_event("arrived", "person", "Ghost")
    assert out is None


@pytest.mark.asyncio
async def test_errors_are_swallowed(monkeypatch):
    @asynccontextmanager
    async def boom_session():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(lifecycle, "async_session", boom_session)
    out = await lifecycle.notify_journey_event("arrived", "person", "Ahmed")
    assert out is None


def test_coerce_uuid():
    u = uuid.uuid4()
    assert lifecycle._coerce_uuid(u) == u
    assert lifecycle._coerce_uuid(str(u)) == u
    assert lifecycle._coerce_uuid(None) is None
    assert lifecycle._coerce_uuid("not-a-uuid") is None
