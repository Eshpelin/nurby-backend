"""Tests for services.guardian.delivery. Per-recipient Telegram + email push."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.guardian import delivery


def _link(uid):
    return SimpleNamespace(id=uuid.uuid4(), guardian_user_id=uid)


def _channel(**kw):
    base = dict(
        id=uuid.uuid4(),
        chat_id="123",
        bot_token_enc=b"enc",
        rate_limit_per_chat_qps=1.0,
        rate_limit_per_chat_burst=3,
        dedupe_window_seconds=30,
        media_quality="high",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _db_with_channels(channels, user):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = channels
    db.execute = AsyncMock(return_value=res)
    db.get = AsyncMock(return_value=user)
    return db


@pytest.mark.asyncio
async def test_delivers_telegram_and_email(monkeypatch):
    uid = uuid.uuid4()
    user = SimpleNamespace(id=uid, email="p@x.com")
    db = _db_with_channels([_channel()], user)

    sent = {"tg": 0, "em": 0}

    async def fake_tg(channel, text, photo):
        sent["tg"] += 1
        return True

    async def fake_em(to, subject, body):
        sent["em"] += 1
        return True

    monkeypatch.setattr(delivery, "_send_telegram", fake_tg)
    monkeypatch.setattr(delivery, "_send_email", fake_em)

    out = await delivery.deliver_to_guardians(db, [_link(uid)], message="hi")
    assert out["telegram_sent"] == 1
    assert out["email_sent"] == 1
    assert out["guardians"] == 1


@pytest.mark.asyncio
async def test_dedupes_same_guardian(monkeypatch):
    uid = uuid.uuid4()
    user = SimpleNamespace(id=uid, email="p@x.com")
    db = _db_with_channels([], user)
    monkeypatch.setattr(delivery, "_send_email", AsyncMock(return_value=True))
    # same guardian appears twice (two links) -> delivered once
    out = await delivery.deliver_to_guardians(db, [_link(uid), _link(uid)], message="hi")
    assert out["guardians"] == 1


@pytest.mark.asyncio
async def test_no_email_without_smtp(monkeypatch):
    monkeypatch.setattr(delivery.settings, "smtp_host", "")
    out = await delivery._send_email("p@x.com", "s", "b")
    assert out is False


@pytest.mark.asyncio
async def test_telegram_failure_isolated(monkeypatch):
    uid = uuid.uuid4()
    user = SimpleNamespace(id=uid, email=None)
    db = _db_with_channels([_channel(), _channel()], user)

    calls = {"n": 0}

    async def flaky(channel, text, photo):
        calls["n"] += 1
        return calls["n"] == 1  # first ok, second fails

    monkeypatch.setattr(delivery, "_send_telegram", flaky)
    out = await delivery.deliver_to_guardians(db, [_link(uid)], message="hi")
    assert out["telegram_sent"] == 1  # only the successful one counted
    assert calls["n"] == 2  # both attempted


@pytest.mark.asyncio
async def test_db_error_does_not_raise(monkeypatch):
    uid = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("boom"))
    db.get = AsyncMock(side_effect=RuntimeError("boom"))
    out = await delivery.deliver_to_guardians(db, [_link(uid)], message="hi")
    assert out["telegram_sent"] == 0
    assert out["email_sent"] == 0
