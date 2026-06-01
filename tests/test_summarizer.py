"""Tests for the map-reduce long-window summarizer (Learning 4).

Reuse the FakeDB / responder stubs from the agent-tools tests so these
run without a real Postgres. The map step is asserted to be zero-LLM;
the reduce step is exercised with a mocked llm_call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent import summarizer as summ_mod
from services.agent.summarizer import summarize_window


# ── stubs (mirrors tests/test_agent_tools.py) ───────────────────────


class FakeResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        scalars = []
        for r in self._rows:
            scalars.append(r[0] if isinstance(r, tuple) else r)
        return FakeScalars(scalars)


class FakeScalars:
    def __init__(self, items: list[Any]):
        self._items = items

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class FakeDB:
    def __init__(self, responder):
        self._responder = responder
        self._gets: dict[Any, Any] = {}

    async def execute(self, stmt):
        return FakeResult(self._responder(str(stmt)))

    async def get(self, model, ident):
        return self._gets.get(ident)


def _user(role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)


def _provider(kind="openai", model="gpt-4o-mini") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Test",
        kind=kind,
        base_url="http://x",
        api_key="k",
        default_model=model,
        active=True,
        max_output_tokens=None,
    )


def _segment(cam_id, cam_name, started, last_seen):
    return {
        "camera_id": str(cam_id),
        "camera_name": cam_name,
        "started_at": started.isoformat(),
        "last_seen_at": last_seen.isoformat(),
        "occurrence_count": 1,
    }


def _journey(subject_key, started, last_seen, segments):
    return SimpleNamespace(
        id=uuid.uuid4(),
        subject_kind="person",
        subject_key=subject_key,
        started_at=started,
        last_seen_at=last_seen,
        ended_at=last_seen,
        segments=segments,
        transitions=[],
        person_id=None,
    )


@pytest.fixture(autouse=True)
def _patch_access(monkeypatch):
    """Default. one accessible camera. Individual tests override."""
    cam = uuid.uuid4()

    async def fake_access(user, db):
        return {cam}

    monkeypatch.setattr(summ_mod, "accessible_camera_ids", fake_access)
    return cam


def _llm_response(text="combined narrative", ti=120, to=60):
    return SimpleNamespace(
        stop_reason="end_turn", text=text, tool_uses=[], tokens_in=ti, tokens_out=to
    )


async def _ok_budget(user_id, db):
    return SimpleNamespace(ok=True, warn=False, reason="", token_budget=1, cost_budget_cents=1)


# ── chunking ────────────────────────────────────────────────────────


def test_plan_chunks_7day_by_day_yields_seven():
    now = datetime.now(timezone.utc)
    slices, eff = summ_mod._plan_chunks(now, 168, "day")
    assert eff == "day"
    assert len(slices) == 7


def test_plan_chunks_short_window_hourly():
    now = datetime.now(timezone.utc)
    slices, eff = summ_mod._plan_chunks(now, 12, "auto")
    # 12h window in 6h buckets -> 2 chunks, hourly grain.
    assert eff == "hour"
    assert len(slices) == 2


def test_plan_chunks_auto_picks_daily_for_multiday():
    now = datetime.now(timezone.utc)
    slices, eff = summ_mod._plan_chunks(now, 168, "auto")
    assert eff == "day"
    assert len(slices) == 7


# ── map step is deterministic + zero-LLM ────────────────────────────


@pytest.mark.asyncio
async def test_map_step_is_zero_llm(monkeypatch, _patch_access):
    cam = _patch_access
    now = datetime.now(timezone.utc)
    j = _journey("Dad", now - timedelta(days=1), now - timedelta(hours=20),
                 [_segment(cam, "Front Door", now - timedelta(days=1), now - timedelta(hours=20))])

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(j,)]
        if "from cameras" in s:
            return [(SimpleNamespace(id=cam, name="Front Door"),)]
        if "from observations" in s:
            return [(uuid.uuid4(), cam, now - timedelta(hours=22),
                     {"objects": [{"label": "person"}, {"label": "dog"}]})]
        if "from events" in s:
            return []
        return []

    # Any llm_call during map is a failure. We allow it ONLY for reduce,
    # so we count calls and assert facts were built before the first one.
    calls = {"n": 0}

    async def guard_llm(**kwargs):
        calls["n"] += 1
        return _llm_response()

    monkeypatch.setattr(summ_mod, "llm_call", guard_llm)
    monkeypatch.setattr(summ_mod, "check_budget", _ok_budget)

    async def fake_record(*a, **k):
        return None

    monkeypatch.setattr(summ_mod, "record_usage", fake_record)

    async def fake_provider(db, provider_id):
        return _provider()

    monkeypatch.setattr(summ_mod, "_resolve_provider", fake_provider)

    db = FakeDB(responder)
    ctx = {"user": _user(), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=48, chunk_by="day")

    # The chunk mini-summaries are built with zero model calls. Exactly
    # one llm_call (the single reduce pass) happened total.
    assert calls["n"] == 1
    # Each chunk has a deterministic mini_summary derived from facts.
    assert out["chunk_count"] == 2
    assert any("Dad" in c["mini_summary"] for c in out["chunks"])


@pytest.mark.asyncio
async def test_map_raises_if_llm_called_during_map(monkeypatch, _patch_access):
    """Stronger guard. monkeypatch llm_call to RAISE so any map-phase
    model call would blow up; the deterministic _chunk_facts +
    _mini_summary path must never touch it."""
    cam = _patch_access
    now = datetime.now(timezone.utc)

    def responder(stmt: str):
        s = stmt.lower()
        if "from cameras" in s:
            return [(SimpleNamespace(id=cam, name="Cam"),)]
        return []

    async def boom(**kwargs):
        raise AssertionError("llm_call must not run during the map phase")

    # No provider -> reduce is skipped entirely, so llm_call is never
    # legitimately called. If the map phase called it, boom fires.
    async def no_provider(db, provider_id):
        return None

    monkeypatch.setattr(summ_mod, "llm_call", boom)
    monkeypatch.setattr(summ_mod, "_resolve_provider", no_provider)

    db = FakeDB(responder)
    ctx = {"user": _user(), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=24, chunk_by="day")
    assert out["partial"] is True
    assert "no LLM provider" in (out.get("note") or "")


# ── reduce with mocked llm_call records usage ───────────────────────


@pytest.mark.asyncio
async def test_reduce_records_usage(monkeypatch, _patch_access):
    cam = _patch_access
    now = datetime.now(timezone.utc)

    def responder(stmt: str):
        s = stmt.lower()
        if "from cameras" in s:
            return [(SimpleNamespace(id=cam, name="Cam"),)]
        return []

    async def fake_llm(**kwargs):
        return _llm_response(text="THE NARRATIVE", ti=200, to=80)

    recorded = {}

    async def fake_record(user_id, tokens_in, tokens_out, cost_cents, db, **k):
        recorded["ti"] = tokens_in
        recorded["to"] = tokens_out
        recorded["cost"] = cost_cents

    monkeypatch.setattr(summ_mod, "llm_call", fake_llm)
    monkeypatch.setattr(summ_mod, "check_budget", _ok_budget)
    monkeypatch.setattr(summ_mod, "record_usage", fake_record)

    async def fake_provider(db, provider_id):
        return _provider()

    monkeypatch.setattr(summ_mod, "_resolve_provider", fake_provider)

    db = FakeDB(responder)
    ctx = {"user": _user(), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=24, chunk_by="day")

    assert out["summary"] == "THE NARRATIVE"
    assert out["partial"] is False
    assert recorded["ti"] == 200
    assert recorded["to"] == 80
    assert out["tokens_used"] == 280
    assert out["cost_cents"] == recorded["cost"]


# ── budget exhausted mid-reduce ─────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_exhausted_returns_partial(monkeypatch, _patch_access):
    cam = _patch_access

    def responder(stmt: str):
        s = stmt.lower()
        if "from cameras" in s:
            return [(SimpleNamespace(id=cam, name="Cam"),)]
        return []

    # check_budget says NOT ok -> reduce never spends.
    async def blocked_budget(user_id, db):
        return SimpleNamespace(ok=False, warn=True, reason="cap reached",
                               token_budget=1, cost_budget_cents=1)

    called = {"llm": 0}

    async def fake_llm(**kwargs):
        called["llm"] += 1
        return _llm_response()

    monkeypatch.setattr(summ_mod, "check_budget", blocked_budget)
    monkeypatch.setattr(summ_mod, "llm_call", fake_llm)

    async def fake_record(*a, **k):
        return None

    monkeypatch.setattr(summ_mod, "record_usage", fake_record)

    async def fake_provider(db, provider_id):
        return _provider()

    monkeypatch.setattr(summ_mod, "_resolve_provider", fake_provider)

    db = FakeDB(responder)
    ctx = {"user": _user(), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=168, chunk_by="day")

    assert out["partial"] is True
    assert called["llm"] == 0  # budget gate fired before any spend
    assert out["tokens_used"] == 0
    assert out["cost_cents"] == 0
    assert "budget" in (out.get("note") or "")
    # Still useful. the deterministic concat is the summary.
    assert out["summary"]


# ── no-provider fallback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_provider_fallback(monkeypatch, _patch_access):
    cam = _patch_access
    now = datetime.now(timezone.utc)
    j = _journey("Aisha", now - timedelta(hours=10), now - timedelta(hours=9),
                 [_segment(cam, "Hall", now - timedelta(hours=10), now - timedelta(hours=9))])

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(j,)]
        if "from cameras" in s:
            return [(SimpleNamespace(id=cam, name="Hall"),)]
        return []

    async def no_provider(db, provider_id):
        return None

    monkeypatch.setattr(summ_mod, "_resolve_provider", no_provider)

    db = FakeDB(responder)
    ctx = {"user": _user(), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=24, chunk_by="day")

    assert out["partial"] is True
    assert "no LLM provider" in (out.get("note") or "")
    assert "Aisha" in out["summary"]
    assert out["tokens_used"] == 0


# ── access filter restricts results ─────────────────────────────────


@pytest.mark.asyncio
async def test_access_filter_restricts(monkeypatch):
    visible = uuid.uuid4()
    hidden = uuid.uuid4()
    now = datetime.now(timezone.utc)
    # Journey only on the hidden camera -> must not appear.
    j = _journey("Ghost", now - timedelta(hours=5), now - timedelta(hours=4),
                 [_segment(hidden, "Garage", now - timedelta(hours=5), now - timedelta(hours=4))])

    async def fake_access(user, db):
        return {visible}

    monkeypatch.setattr(summ_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(j,)]
        if "from cameras" in s:
            return [(SimpleNamespace(id=visible, name="Living"),)]
        return []

    async def no_provider(db, provider_id):
        return None

    monkeypatch.setattr(summ_mod, "_resolve_provider", no_provider)

    db = FakeDB(responder)
    ctx = {"user": _user("viewer"), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=24, chunk_by="day")

    # Ghost's journey is on a hidden camera, so it is filtered out and
    # never cited.
    assert all(c["kind"] != "journey" for c in out["citations"]) or not out["citations"]
    assert "Ghost" not in out["summary"]


# ── window clamp ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_clamped(monkeypatch, _patch_access):
    cam = _patch_access

    def responder(stmt: str):
        if "from cameras" in stmt.lower():
            return [(SimpleNamespace(id=cam, name="Cam"),)]
        return []

    async def no_provider(db, provider_id):
        return None

    monkeypatch.setattr(summ_mod, "_resolve_provider", no_provider)

    db = FakeDB(responder)
    ctx = {"user": _user(), "run_id": None, "db": db}
    out = await summarize_window(ctx, hours=9999, chunk_by="day")
    assert out["hours"] == 720
    assert "clamped" in (out.get("note") or "")


@pytest.mark.asyncio
async def test_hours_too_small_rejected(_patch_access):
    db = FakeDB(lambda s: [])
    ctx = {"user": _user(), "run_id": None, "db": db}
    with pytest.raises(ValueError):
        await summarize_window(ctx, hours=0)
