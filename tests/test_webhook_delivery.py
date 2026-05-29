import hashlib
import hmac

import pytest

from services.events import actions as actions_mod
from services.events.actions import deliver_signed, sign_body


def test_sign_body_matches_manual_hmac():
    secret = "topsecret"
    body = b'{"a":1}'
    sig = sign_body(body, secret)
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


class _Resp:
    def __init__(self, sc):
        self.status_code = sc


class _FakeClient:
    def __init__(self, scripted, sink):
        self._scripted = list(scripted)
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, **kw):
        self._sink.append(kw)
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def _patch(monkeypatch, scripted):
    sink = []
    monkeypatch.setattr(actions_mod.httpx, "AsyncClient", lambda: _FakeClient(scripted, sink))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(actions_mod.asyncio, "sleep", _no_sleep)
    return sink


@pytest.mark.asyncio
async def test_deliver_retries_5xx_then_succeeds(monkeypatch):
    sink = _patch(monkeypatch, [500, 200])
    ok, detail = await deliver_signed("POST", "http://x", {"a": 1}, attempts=3)
    assert ok is True
    assert len(sink) == 2  # retried once


@pytest.mark.asyncio
async def test_deliver_gives_up_after_attempts(monkeypatch):
    sink = _patch(monkeypatch, [500, 500, 500])
    ok, detail = await deliver_signed("POST", "http://x", {"a": 1}, attempts=3)
    assert ok is False
    assert len(sink) == 3
    assert "500" in detail


@pytest.mark.asyncio
async def test_deliver_does_not_retry_4xx(monkeypatch):
    sink = _patch(monkeypatch, [403, 200])
    ok, detail = await deliver_signed("POST", "http://x", {"a": 1}, attempts=3)
    assert ok is False
    assert len(sink) == 1  # 4xx is terminal


@pytest.mark.asyncio
async def test_deliver_signs_when_secret_present(monkeypatch):
    sink = _patch(monkeypatch, [200])
    await deliver_signed("POST", "http://x", {"a": 1}, secret="s")
    assert "X-Nurby-Signature" in sink[0]["headers"]
    assert sink[0]["headers"]["X-Nurby-Signature"].startswith("sha256=")


@pytest.mark.asyncio
async def test_deliver_unsigned_when_no_secret(monkeypatch):
    sink = _patch(monkeypatch, [200])
    await deliver_signed("POST", "http://x", {"a": 1})
    assert "X-Nurby-Signature" not in sink[0]["headers"]
