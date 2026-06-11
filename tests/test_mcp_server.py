"""Tests for the Nurby MCP server.

The auth + dispatch + budget logic is unit-tested directly against the
plain functions in ``services.mcp.server`` so the real ``mcp`` SDK does
NOT need to be installed. Only the ``build_server`` test (which imports
the SDK) is guarded with ``importorskip``.

These tests stub the DB session and budget so no Postgres is required.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent.budget import BudgetStatus
from services.agent.tools import TOOL_REGISTRY
from services.mcp import server as mcp_server

# ── fixtures / fakes ─────────────────────────────────────────────────


def _user(active: bool = True, role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=active, display_name="Tester"
    )


class _FakeSession:
    """Minimal async-session stand-in. ``get`` returns a preset user;
    everything else is unused by the dispatch path under test."""

    def __init__(self, user: Any):
        self._user = user

    async def get(self, model, key):  # noqa: ANN001
        return self._user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002
        return False


def _ok_budget() -> BudgetStatus:
    return BudgetStatus(
        ok=True,
        warn=False,
        reason="",
        remaining_tokens=1000,
        remaining_cost_cents=100,
        used_tokens=0,
        used_cost_cents=0,
        token_budget=1000,
        cost_budget_cents=100,
    )


def _exhausted_budget() -> BudgetStatus:
    return BudgetStatus(
        ok=False,
        warn=True,
        reason="daily token budget 1000 reached",
        remaining_tokens=0,
        remaining_cost_cents=0,
        used_tokens=1000,
        used_cost_cents=100,
        token_budget=1000,
        cost_budget_cents=100,
    )


@pytest.fixture
def patched_env(monkeypatch):
    """Wire a fake session, valid token decode, and OK budget. Returns the
    user that will be resolved."""
    user = _user()

    monkeypatch.setattr(
        mcp_server, "async_session", lambda: _FakeSession(user)
    )
    monkeypatch.setattr(
        mcp_server, "decode_access_token", lambda tok: user.id
    )

    async def _ok(*_a, **_k):
        return _ok_budget()

    monkeypatch.setattr(mcp_server, "check_budget", _ok)
    return user


# ── read-tool surface ────────────────────────────────────────────────


def test_read_tools_match_registry_read_subset():
    expected = {
        t["name"] for t in TOOL_REGISTRY if t.get("side_effect") == "read"
    }
    got = set(mcp_server.read_tool_names())
    assert got == expected
    # The two analyzer tools are read per their registry entry, so they
    # ARE exposed. ``verify`` is a rule action, not an agent tool, so it
    # is absent from the registry entirely.
    assert {"analyze_clip", "analyze_frame"} <= got
    assert "verify" not in got
    # No write-side-effect tool ever leaks through.
    for t in TOOL_REGISTRY:
        if t.get("side_effect") != "read":
            assert t["name"] not in got


def test_tool_definitions_are_mcp_shaped():
    defs = mcp_server.tool_definitions()
    assert defs, "expected at least one read tool"
    for d in defs:
        assert set(d) == {"name", "description", "input_schema"}
        assert isinstance(d["input_schema"], dict)


# ── dispatch routing ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_routes_to_fn_with_ctx(monkeypatch, patched_env):
    user = patched_env
    captured: dict[str, Any] = {}

    async def fake_fn(ctx, **kwargs):  # noqa: ANN001
        captured["ctx"] = ctx
        captured["kwargs"] = kwargs
        return {"hello": "world"}

    # Monkeypatch a known read tool's fn in place. get_tool reads the
    # same registry entry, so the dispatcher picks up our fake.
    entry = next(t for t in TOOL_REGISTRY if t["name"] == "summarize_activity")
    monkeypatch.setitem(entry, "fn", fake_fn)

    out = await mcp_server.dispatch_tool_call(
        "summarize_activity", {"hours": 12}, token="tok"
    )

    assert out["ok"] is True
    assert out["result"] == {"hello": "world"}
    ctx = captured["ctx"]
    assert ctx["user"] is user
    assert ctx["run_id"] is None
    assert ctx["db"] is not None
    assert captured["kwargs"] == {"hours": 12}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error(patched_env):
    out = await mcp_server.dispatch_tool_call("no_such_tool", {}, token="tok")
    assert out["ok"] is False
    assert out["kind"] == "unknown_tool"


@pytest.mark.asyncio
async def test_dispatch_budget_exhausted_does_not_call_tool(
    monkeypatch, patched_env
):
    called = {"n": 0}

    async def fake_fn(ctx, **kwargs):  # noqa: ANN001
        called["n"] += 1
        return {}

    entry = next(t for t in TOOL_REGISTRY if t["name"] == "summarize_activity")
    monkeypatch.setitem(entry, "fn", fake_fn)

    async def _exhausted(*_a, **_k):
        return _exhausted_budget()

    monkeypatch.setattr(mcp_server, "check_budget", _exhausted)

    out = await mcp_server.dispatch_tool_call(
        "summarize_activity", {}, token="tok"
    )
    assert out["ok"] is False
    assert out["kind"] == "budget_exhausted"
    assert called["n"] == 0  # tool never ran


@pytest.mark.asyncio
async def test_dispatch_tool_exception_is_clean(monkeypatch, patched_env):
    async def boom(ctx, **kwargs):  # noqa: ANN001
        raise RuntimeError("kaboom")

    entry = next(t for t in TOOL_REGISTRY if t["name"] == "summarize_activity")
    monkeypatch.setitem(entry, "fn", boom)

    out = await mcp_server.dispatch_tool_call(
        "summarize_activity", {}, token="tok"
    )
    assert out["ok"] is False
    assert out["kind"] == "tool_error"
    assert "kaboom" in out["error"]


# ── auth ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_user_missing_token_raises():
    fake = _FakeSession(_user())
    with pytest.raises(mcp_server.McpAuthError):
        await mcp_server.resolve_user(None, fake)


@pytest.mark.asyncio
async def test_resolve_user_invalid_token_raises(monkeypatch):
    monkeypatch.setattr(mcp_server, "decode_access_token", lambda tok: None)
    fake = _FakeSession(_user())
    with pytest.raises(mcp_server.McpAuthError):
        await mcp_server.resolve_user("garbage", fake)


@pytest.mark.asyncio
async def test_resolve_user_inactive_user_raises(monkeypatch):
    inactive = _user(active=False)
    monkeypatch.setattr(
        mcp_server, "decode_access_token", lambda tok: inactive.id
    )
    fake = _FakeSession(inactive)
    with pytest.raises(mcp_server.McpAuthError):
        await mcp_server.resolve_user("tok", fake)


@pytest.mark.asyncio
async def test_dispatch_auth_failure_returns_error(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "async_session", lambda: _FakeSession(_user())
    )
    monkeypatch.setattr(mcp_server, "decode_access_token", lambda tok: None)
    out = await mcp_server.dispatch_tool_call(
        "summarize_activity", {}, token=None
    )
    assert out["ok"] is False
    assert out["kind"] == "auth"


# ── serialization ────────────────────────────────────────────────────


def test_serialize_result_handles_uuid_and_datetime():
    payload = {"ok": True, "result": {"id": uuid.uuid4()}}
    text = mcp_server.serialize_result(payload)
    parsed = json.loads(text)
    assert parsed["ok"] is True
    assert isinstance(parsed["result"]["id"], str)


# ── SDK-dependent. only runs when mcp is installed ───────────────────


def test_build_server_registers_read_tools(monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv(mcp_server.TOKEN_ENV, "some-token")
    server = mcp_server.build_server()
    assert server is not None
    # Server name is exposed on the low-level Server. exact attribute may
    # vary across SDK versions; tolerate either.
    name = getattr(server, "name", None)
    if name is not None:
        assert name == mcp_server.SERVER_NAME


def test_build_server_without_token_raises(monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.delenv(mcp_server.TOKEN_ENV, raising=False)
    with pytest.raises(mcp_server.McpAuthError):
        mcp_server.build_server()
