"""Tests for the Guardian API helpers and scoping rules.

Focuses on the security-critical gates that are pure or near-pure:
- a guardian cannot reach another guardian's link (404, not 403)
- a revoked / expired link is gone (410)
- admin bypasses ownership
- image throttle returns 429

Full DB integration is covered by the end-to-end smoke against the running
stack. Here the session is an AsyncMock so the suite stays DB-free.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.api.routes import guardian as g

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def _user(role="guardian", uid=None):
    return SimpleNamespace(id=uid or uuid.uuid4(), email="p@x.com", display_name="P", role=role)


def _link(owner_id, **kw):
    base = dict(
        id=uuid.uuid4(),
        guardian_user_id=owner_id,
        person_id=uuid.uuid4(),
        facility_id=uuid.uuid4(),
        revoked_at=None,
        expires_at=None,
        tier="full",
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


# ── ownership scoping ────────────────────────────────────────────────

def test_owner_passes():
    u = _user()
    link = _link(u.id)
    g._ensure_owner_or_admin(link, u)  # no raise


def test_other_guardian_gets_404():
    owner = _user()
    intruder = _user()
    link = _link(owner.id)
    with pytest.raises(g.HTTPException) as ei:
        g._ensure_owner_or_admin(link, intruder)
    assert ei.value.status_code == 404  # not 403, no link-id probing


def test_admin_bypasses_ownership():
    admin = _user(role="admin")
    link = _link(uuid.uuid4())
    g._ensure_owner_or_admin(link, admin)  # no raise


# ── active / expiry gate ─────────────────────────────────────────────

def test_active_link_ok():
    g._ensure_active(_link(uuid.uuid4()))  # no raise


def test_revoked_link_410():
    link = _link(uuid.uuid4(), revoked_at=NOW - timedelta(hours=1))
    with pytest.raises(g.HTTPException) as ei:
        g._ensure_active(link)
    assert ei.value.status_code == 410


def test_expired_link_410():
    link = _link(uuid.uuid4(), expires_at=NOW - timedelta(days=1))
    # is_active uses real now; an expiry in the past is inactive
    with pytest.raises(g.HTTPException) as ei:
        g._ensure_active(link)
    assert ei.value.status_code == 410


# ── async endpoint behaviors with a mock db ──────────────────────────

class FakeDB:
    """Minimal async session stand-in. get() returns from a dict by id."""

    def __init__(self, objects=None):
        self._objs = {getattr(o, "id", None): o for o in (objects or [])}
        self.added = []
        self.committed = 0

    async def get(self, model, oid):
        return self._objs.get(oid)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        pass


@pytest.mark.asyncio
async def test_revoke_sets_timestamp(monkeypatch):
    owner = _user()
    link = _link(owner.id)
    db = FakeDB([link])
    out = await g.revoke_link(link.id, admin=_user(role="admin"), db=db)
    assert out["revoked"] is True
    assert link.revoked_at is not None


@pytest.mark.asyncio
async def test_image_throttle_429(monkeypatch):
    owner = _user()
    # free link that just served an image -> throttled
    link = _link(owner.id, last_image_served_at=NOW - timedelta(minutes=5))
    person = SimpleNamespace(id=link.person_id, display_name="Ahmed", nickname=None)
    db = FakeDB([link, person])

    async def fake_interval(_db):
        return 3600

    async def fake_user(_token, _request, _db):
        return owner

    monkeypatch.setattr(g, "_free_image_interval", fake_interval)
    monkeypatch.setattr(g, "_user_from_token_or_header", fake_user)
    # 5 min < 60 min so the free link is throttled with real now too.
    with pytest.raises(g.HTTPException) as ei:
        await g.link_image(link.id, request=None, token=None, db=db)
    assert ei.value.status_code == 429
    assert "Retry-After" in ei.value.headers


@pytest.mark.asyncio
async def test_recap_requires_premium(monkeypatch):
    owner = _user()
    link = _link(owner.id, premium=False)
    person = SimpleNamespace(
        id=link.person_id, display_name="Ahmed", nickname=None,
        recap_cached_status=None, recap_cached_at=None, recap_stale=False,
    )
    db = FakeDB([link, person])
    with pytest.raises(g.HTTPException) as ei:
        await g.link_recap(link.id, request=None, user=owner, db=db)
    assert ei.value.status_code == 402


@pytest.mark.asyncio
async def test_live_requires_entitlement(monkeypatch):
    owner = _user()
    link = _link(owner.id, live_presence=False, live_video=False)
    person = SimpleNamespace(id=link.person_id, display_name="Ahmed", nickname=None)
    db = FakeDB([link, person])
    with pytest.raises(g.HTTPException) as ei:
        await g.link_live(link.id, request=None, user=owner, db=db)
    assert ei.value.status_code == 402


@pytest.mark.asyncio
async def test_search_requires_premium(monkeypatch):
    owner = _user()
    link = _link(owner.id, premium=False)
    person = SimpleNamespace(id=link.person_id, display_name="Ahmed", nickname=None)
    db = FakeDB([link, person])
    body = SimpleNamespace(query="dog", limit=10)
    with pytest.raises(g.HTTPException) as ei:
        await g.link_search(link.id, body=body, request=None, user=owner, db=db)
    assert ei.value.status_code == 402


@pytest.mark.asyncio
async def test_alerts_patch_sanitizes(monkeypatch):
    owner = _user()
    link = _link(owner.id)
    db = FakeDB([link])

    async def fake_log(*a, **k):
        return None

    monkeypatch.setattr(g, "_log", fake_log)
    body = SimpleNamespace(alert_prefs={"arrived": False, "not_seen": True, "bogus": True})
    out = await g.link_alerts(link.id, body=body, request=None, user=owner, db=db)
    assert out["alert_prefs"]["arrived"] is False
    assert out["alert_prefs"]["not_seen"] is True
    assert "bogus" not in out["alert_prefs"]
