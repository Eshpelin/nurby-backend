"""Tests for services.agent.driver.

The driver is exercised via a fully stubbed LLM (monkeypatched
``llm_call`` from services.agent.driver) and stubbed db/run/budget
helpers. The goal is to assert the loop control flow, the WS event
sequence, the tool-loop dedupe, the budget-mid-loop abort path, and the
max-turns forced-synthesis path.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from services.agent import driver as driver_mod
from services.agent.llm import LLMResponse, LLMToolUse


def _run(coro):
    return asyncio.run(coro)


# ── Fakes ──────────────────────────────────────────────────────────


@dataclass
class _FakeProvider:
    kind: str = "anthropic"
    api_key: str | None = "k"
    base_url: str | None = "https://example"
    default_model: str | None = "claude-sonnet-4"
    id: Any = None

    def __post_init__(self):
        if self.id is None:
            self.id = uuid.uuid4()


@dataclass
class _FakeUser:
    id: Any = None
    role: str = "viewer"
    is_active: bool = True

    def __post_init__(self):
        if self.id is None:
            self.id = uuid.uuid4()


class _FakeRunRow:
    def __init__(self):
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_cents = 0
        self.turns_used = 0
        self.status = "running"
        self.final_answer = None
        self.error_message = None
        self.ended_at = None
        self.latency_ms = None
        self.plan = None


def _fake_db_session(run_row: _FakeRunRow):
    """An async-context-manager that yields a stub db with .get(AgentRun)
    returning the same run_row each time. ``add``/``commit``/``refresh``
    are no-ops."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.get = AsyncMock(return_value=run_row)
    db.execute = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield db

    return factory, db


def _patch_runs(monkeypatch, run_row):
    """Wire services.agent.runs functions used by the driver to act on
    the in-memory ``run_row`` so we don't need a real DB."""

    async def _update_run(run_id, db, **patch):
        for k, v in patch.items():
            setattr(run_row, k, v)
        return run_row

    async def _append_tool_call(run_id, turn_index, tool_name, arguments, db):
        row = MagicMock()
        row.id = uuid.uuid4()
        row.turn_index = turn_index
        row.tool_name = tool_name
        return row

    async def _complete_tool_call(call_id, db, **kw):
        return None

    async def _cancel_run(run_id, reason, db):
        run_row.status = "cancelled"
        run_row.error_message = reason
        return run_row

    monkeypatch.setattr(driver_mod.runs_mod, "update_run", _update_run)
    monkeypatch.setattr(driver_mod.runs_mod, "append_tool_call", _append_tool_call)
    monkeypatch.setattr(driver_mod.runs_mod, "complete_tool_call", _complete_tool_call)
    monkeypatch.setattr(driver_mod.runs_mod, "cancel_run", _cancel_run)


class _BudgetOk:
    ok = True
    warn = False
    reason = ""
    remaining_tokens = 999_999
    remaining_cost_cents = 999_999
    used_tokens = 0
    used_cost_cents = 0
    token_budget = 1_000_000
    cost_budget_cents = 1_000_000


class _BudgetExhausted:
    ok = False
    warn = True
    reason = "out of cents"
    remaining_tokens = 0
    remaining_cost_cents = 0
    used_tokens = 1_000_000
    used_cost_cents = 1_000_000
    token_budget = 1_000_000
    cost_budget_cents = 1_000_000


def _patch_budget(monkeypatch, statuses):
    """Each call to check_budget returns the next status in the list."""
    it = iter(statuses)
    fallback = statuses[-1]

    async def _check(user_id, db):
        try:
            return next(it)
        except StopIteration:
            return fallback

    async def _record(*a, **kw):
        return None

    monkeypatch.setattr(driver_mod, "check_budget", _check)
    monkeypatch.setattr(driver_mod, "record_usage", _record)
    monkeypatch.setattr(driver_mod, "estimate_cost", lambda *a, **kw: 1)


def _patch_get_setting(monkeypatch, **vals):
    base = {
        "agent_max_turns_per_run": 12,
        "agent_max_vlm_calls_per_run": 8,
        "system_timezone": "UTC",
    }
    base.update(vals)

    async def _get_setting(key, default=None):
        return base.get(key, default)

    monkeypatch.setattr(driver_mod, "get_setting", _get_setting)


def _scripted_llm(monkeypatch, scripted: list[LLMResponse]):
    """Return successive LLMResponse objects on each llm_call invocation."""
    it = iter(scripted)

    async def _llm_call(**kwargs):
        try:
            resp = next(it)
        except StopIteration:
            resp = LLMResponse(stop_reason="end_turn", text="ran out of script", tool_uses=[])
        cb = kwargs.get("stream_callback")
        if cb and resp.text:
            await cb(resp.text)
        return resp

    monkeypatch.setattr(driver_mod, "llm_call", _llm_call)


# ── Tests ──────────────────────────────────────────────────────────


def test_driver_runs_one_tool_then_finishes(monkeypatch):
    run_id = uuid.uuid4()
    run_row = _FakeRunRow()
    factory, db = _fake_db_session(run_row)
    _patch_runs(monkeypatch, run_row)
    _patch_budget(monkeypatch, [_BudgetOk(), _BudgetOk(), _BudgetOk()])
    _patch_get_setting(monkeypatch)

    # First call: emit one tool_use; second call: end_turn with final text.
    tool_use = LLMToolUse(id="t1", name="query_observations",
                          arguments={"query": "cat", "hours": 24})
    _scripted_llm(monkeypatch, [
        LLMResponse(stop_reason="tool_use", text="<plan>look for cat</plan>",
                    tool_uses=[tool_use], tokens_in=100, tokens_out=20),
        LLMResponse(stop_reason="end_turn", text="No cat sightings.",
                    tool_uses=[], tokens_in=50, tokens_out=10),
    ])

    # Tool function shim that does not touch the DB.
    async def _fake_query_observations(ctx, **kw):
        return {"count": 0, "observations": []}

    import services.agent.tools as tools_mod
    original = tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"]
    tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = _fake_query_observations
    try:
        events: list[dict] = []

        async def _broadcast(rid, ev):
            events.append(ev)

        driver = driver_mod.AgentDriver(db_factory=factory, broadcast=_broadcast)
        _run(driver.run(
            run_id=run_id,
            user=_FakeUser(),
            question="did the cat go out?",
            provider=_FakeProvider(kind="anthropic"),
            model="claude-sonnet-4",
            parent_run_id=None,
        ))
    finally:
        tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = original

    types = [e["type"] for e in events]
    assert types[0] == "started"
    assert "tool_start" in types
    assert "tool_result" in types
    assert types[-1] == "done"
    done = events[-1]
    assert done["final_answer"] == "No cat sightings."
    assert done["partial"] is False
    assert run_row.status == "completed"
    assert run_row.final_answer == "No cat sightings."


def test_driver_dedupes_repeated_tool_calls(monkeypatch):
    run_id = uuid.uuid4()
    run_row = _FakeRunRow()
    factory, db = _fake_db_session(run_row)
    _patch_runs(monkeypatch, run_row)
    _patch_budget(monkeypatch, [_BudgetOk()] * 10)
    _patch_get_setting(monkeypatch)

    same = LLMToolUse(id="t1", name="query_observations",
                      arguments={"query": "cat", "hours": 24})
    same2 = LLMToolUse(id="t2", name="query_observations",
                       arguments={"query": "cat", "hours": 24})
    _scripted_llm(monkeypatch, [
        LLMResponse(stop_reason="tool_use", text="", tool_uses=[same]),
        LLMResponse(stop_reason="tool_use", text="", tool_uses=[same2]),
        LLMResponse(stop_reason="end_turn", text="done", tool_uses=[]),
    ])

    async def _fake(ctx, **kw):
        return {"count": 0, "observations": []}

    import services.agent.tools as tools_mod
    original = tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"]
    tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = _fake

    events: list[dict] = []

    async def _broadcast(rid, ev):
        events.append(ev)

    try:
        driver = driver_mod.AgentDriver(db_factory=factory, broadcast=_broadcast)
        _run(driver.run(
            run_id=run_id, user=_FakeUser(),
            question="q", provider=_FakeProvider(), model="claude-sonnet-4",
            parent_run_id=None,
        ))
    finally:
        tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = original

    # find the second tool_result. it should be the dedupe sentinel.
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) >= 2
    assert "tool_loop_detected" in tool_results[1].get("result_summary", "")


def test_driver_budget_exhausted_mid_loop_triggers_forced_synthesis(monkeypatch):
    run_id = uuid.uuid4()
    run_row = _FakeRunRow()
    factory, db = _fake_db_session(run_row)
    _patch_runs(monkeypatch, run_row)
    # first check_budget ok (pre-flight), second still ok (after first call check)
    # third returns exhausted to trigger forced synthesis.
    _patch_budget(monkeypatch, [_BudgetOk(), _BudgetExhausted()])
    _patch_get_setting(monkeypatch)

    tu = LLMToolUse(id="t1", name="query_observations",
                    arguments={"query": "x", "hours": 1})
    _scripted_llm(monkeypatch, [
        LLMResponse(stop_reason="tool_use", text="", tool_uses=[tu],
                    tokens_in=10, tokens_out=5),
        # forced synthesis call:
        LLMResponse(stop_reason="end_turn", text="partial answer", tool_uses=[]),
    ])

    async def _fake(ctx, **kw):
        return {"count": 0, "observations": []}

    import services.agent.tools as tools_mod
    original = tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"]
    tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = _fake

    events: list[dict] = []

    async def _broadcast(rid, ev):
        events.append(ev)

    try:
        driver = driver_mod.AgentDriver(db_factory=factory, broadcast=_broadcast)
        _run(driver.run(
            run_id=run_id, user=_FakeUser(),
            question="q", provider=_FakeProvider(), model="claude-sonnet-4",
            parent_run_id=None,
        ))
    finally:
        tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = original

    types = [e["type"] for e in events]
    assert "budget_warn" in types
    assert types[-1] == "done"
    assert events[-1]["partial"] is True
    assert run_row.status == "budget_exhausted"


def test_driver_respects_max_turns_cap(monkeypatch):
    run_id = uuid.uuid4()
    run_row = _FakeRunRow()
    factory, db = _fake_db_session(run_row)
    _patch_runs(monkeypatch, run_row)
    _patch_budget(monkeypatch, [_BudgetOk()] * 50)
    _patch_get_setting(monkeypatch, agent_max_turns_per_run=2)

    # Always return a new tool_use so the loop never naturally ends.
    def _make_tu(i):
        return LLMToolUse(id=f"t{i}", name="query_observations",
                          arguments={"query": f"x{i}", "hours": 1})

    scripted = [
        LLMResponse(stop_reason="tool_use", text="", tool_uses=[_make_tu(0)]),
        LLMResponse(stop_reason="tool_use", text="", tool_uses=[_make_tu(1)]),
        # forced synthesis
        LLMResponse(stop_reason="end_turn", text="partial summary", tool_uses=[]),
    ]
    _scripted_llm(monkeypatch, scripted)

    async def _fake(ctx, **kw):
        return {"count": 0, "observations": []}

    import services.agent.tools as tools_mod
    original = tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"]
    tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = _fake

    events: list[dict] = []

    async def _broadcast(rid, ev):
        events.append(ev)

    try:
        driver = driver_mod.AgentDriver(db_factory=factory, broadcast=_broadcast)
        _run(driver.run(
            run_id=run_id, user=_FakeUser(),
            question="q", provider=_FakeProvider(), model="claude-sonnet-4",
            parent_run_id=None,
        ))
    finally:
        tools_mod._REGISTRY_BY_NAME["query_observations"]["fn"] = original

    types = [e["type"] for e in events]
    # max_turns surfaces as an error then done w/ partial=True.
    assert any(e.get("message") == "max_turns_reached" for e in events if e["type"] == "error")
    assert types[-1] == "done"
    assert events[-1]["partial"] is True


def test_ws_replay_returns_buffered_events_after_seq(monkeypatch):
    from services.agent import ws as ws_mod

    ws_mod._reset_for_tests()
    rid = "abc"

    async def go():
        await ws_mod.publish_event(rid, {"type": "started", "seq": 1})
        await ws_mod.publish_event(rid, {"type": "tool_start", "seq": 2})
        await ws_mod.publish_event(rid, {"type": "tool_result", "seq": 3})
        backlog = await ws_mod.replay_after(rid, after_seq=1)
        return backlog

    backlog = _run(go())
    assert [e["seq"] for e in backlog] == [2, 3]


def test_summarize_prior_evidence_returns_lines(monkeypatch):
    """Parent-context evidence preamble surfaces the prior run's tool calls."""

def test_format_evidence_preamble_renders_tool_calls():
    """Parent-context evidence preamble surfaces the prior run's tool calls."""
    from types import SimpleNamespace

    from services.agent.driver import _format_evidence_preamble

    rows_newest_first = [
        SimpleNamespace(
            tool_name="query_observations",
            arguments={"query": "cat", "hours": 24},
            result={"count": 0, "observations": []},
        ),
        SimpleNamespace(
            tool_name="get_household_snapshot",
            arguments={},
            result={"cameras": [1, 2, 3, 4]},
        ),
    ]
    out = _format_evidence_preamble(rows_newest_first)
    assert "Prior evidence I gathered:" in out
    assert "get_household_snapshot" in out
    assert "query_observations" in out
    # Output is oldest-first (input is newest-first; the formatter reverses).
    assert out.index("get_household_snapshot") < out.index("query_observations")


def test_format_evidence_preamble_empty_when_no_rows():
    from services.agent.driver import _format_evidence_preamble
    assert _format_evidence_preamble([]) == ""
