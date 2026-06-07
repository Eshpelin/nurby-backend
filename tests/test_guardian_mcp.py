"""Tests for the guardian-scoped MCP tool. Self-scopes to the caller's links."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.guardian import mcp_tools

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def _link(owner_id, person_id, **kw):
    base = dict(
        id=uuid.uuid4(),
        guardian_user_id=owner_id,
        person_id=person_id,
        tier="full",
        revoked_at=None,
        expires_at=None,
        premium=False,
        live_presence=False,
        live_video=False,
        audio=False,
        is_primary_parent=False,
        reveal_min_confidence=None,
        last_image_served_at=None,
        alert_prefs=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_status_only_returns_own_active_links(monkeypatch):
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    person = SimpleNamespace(id=pid, display_name="Ahmed", nickname=None)
    mine = _link(uid, pid)
    revoked = _link(uid, uuid.uuid4(), revoked_at=NOW - timedelta(hours=1))

    db = MagicMock()
    # execute() -> links query
    res = MagicMock()
    res.scalars.return_value.all.return_value = [mine, revoked]
    db.execute = AsyncMock(return_value=res)
    db.get = AsyncMock(return_value=person)

    async def fake_setting(key, default=None):
        return 1800

    async def fake_status(_db, _link, _person, free_delay_seconds):
        return {"state": "present", "zone": "Classroom B", "last_seen_at": NOW, "delayed": True}

    monkeypatch.setattr(mcp_tools, "get_setting", fake_setting)
    monkeypatch.setattr(mcp_tools.presence_mod, "dependant_status", fake_status)

    ctx = {"user": SimpleNamespace(id=uid), "db": db}
    out = await mcp_tools.guardian_dependant_status(ctx)
    assert out["count"] == 1  # revoked link excluded
    assert out["dependants"][0]["display_name"] == "Ahmed"
    assert out["dependants"][0]["delayed"] is True


@pytest.mark.asyncio
async def test_status_name_filter(monkeypatch):
    uid = uuid.uuid4()
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    persons = {
        p1: SimpleNamespace(id=p1, display_name="Ahmed", nickname=None),
        p2: SimpleNamespace(id=p2, display_name="Sara", nickname=None),
    }
    links = [_link(uid, p1), _link(uid, p2)]

    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = links
    db.execute = AsyncMock(return_value=res)
    db.get = AsyncMock(side_effect=lambda model, oid: persons.get(oid))

    monkeypatch.setattr(mcp_tools, "get_setting", AsyncMock(return_value=1800))

    async def fake_status(_db, _link, _person, free_delay_seconds):
        return {"state": "present", "zone": "Z", "last_seen_at": None, "delayed": False}

    monkeypatch.setattr(mcp_tools.presence_mod, "dependant_status", fake_status)

    ctx = {"user": SimpleNamespace(id=uid), "db": db}
    out = await mcp_tools.guardian_dependant_status(ctx, person_name="sara")
    assert out["count"] == 1
    assert out["dependants"][0]["display_name"] == "Sara"
