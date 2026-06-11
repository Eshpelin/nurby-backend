"""Tests for services.agent.budget.

The budget helpers touch ``agent_daily_usage`` and the AppSetting
store. Both are mocked here so the suite stays DB-free. The pattern
follows ``test_rule_replay_endpoint`` (``AsyncMock`` shaped to match
the bits of ``AsyncSession`` the code under test calls).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent import budget as budget_mod
from services.agent.budget import (
    check_budget,
    estimate_cost,
    record_usage,
)

# ── settings store ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _settings_store(monkeypatch):
    """In-memory replacement for ``shared.app_settings.get_setting``.
    Tests mutate the dict directly to push the user over budget."""

    store: dict[str, object] = {
        "agent_daily_token_budget_per_user": 1000,
        "agent_daily_cost_cents_per_user": 500,
        "agent_warn_threshold_pct": 80,
    }

    async def fake_get(key, default=None):
        return store.get(key, default)

    # The module imports the symbol directly; patch on the module.
    monkeypatch.setattr(budget_mod, "get_setting", fake_get)
    return store


def _run(coro):
    return asyncio.run(coro)


# ── db shim ──────────────────────────────────────────────────────────


def _db_with_usage(row):
    """Mock the AsyncSession surface used by ``check_budget``.

    ``row`` is either None (no usage today) or a SimpleNamespace with
    tokens_in/tokens_out/cost_cents attributes.
    """

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=result)
    return db


def _usage_row(tokens_in=0, tokens_out=0, cost_cents=0):
    return SimpleNamespace(
        tokens_in=tokens_in, tokens_out=tokens_out, cost_cents=cost_cents
    )


# ── estimate_cost ────────────────────────────────────────────────────


def test_estimate_cost_zero_tokens_zero_cents():
    assert estimate_cost("anthropic", "claude-sonnet-4", 0, 0) == 0


def test_estimate_cost_known_sonnet_model_rounds_up():
    # 1000 in @ 0.3c + 1000 out @ 1.5c = 1.8c -> ceil 2
    assert estimate_cost("anthropic", "claude-sonnet-4", 1000, 1000) == 2


def test_estimate_cost_ollama_is_free():
    assert estimate_cost("ollama", "llama3", 50_000, 50_000) == 0


def test_estimate_cost_unknown_model_falls_back_to_sonnet():
    # Unknown model -> default pricing. Same as sonnet for the
    # same input/output.
    fallback = estimate_cost("anthropic", "totally-made-up", 1000, 1000)
    sonnet = estimate_cost("anthropic", "claude-sonnet-4", 1000, 1000)
    assert fallback == sonnet


def test_estimate_cost_versioned_model_prefix_match():
    # "claude-sonnet-4-20250514" should match "claude-sonnet-4".
    assert estimate_cost("anthropic", "claude-sonnet-4-20250514", 1000, 1000) == 2


def test_estimate_cost_strips_provider_prefix():
    assert estimate_cost("openai", "openai/gpt-4o", 1000, 1000) == estimate_cost(
        "openai", "gpt-4o", 1000, 1000
    )


# ── check_budget ─────────────────────────────────────────────────────


def test_check_budget_zero_usage_returns_full_remaining():
    db = _db_with_usage(None)
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is True
    assert status.warn is False
    assert status.remaining_tokens == 1000
    assert status.remaining_cost_cents == 500
    assert status.used_tokens == 0
    assert status.used_cost_cents == 0


def test_check_budget_at_80pct_warns_but_allows():
    # 800 tokens of 1000 budget = 80% -> warn=True, ok=True.
    db = _db_with_usage(_usage_row(tokens_in=400, tokens_out=400, cost_cents=0))
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is True
    assert status.warn is True
    assert "80%" in status.reason or "%" in status.reason
    assert status.remaining_tokens == 200


def test_check_budget_at_100pct_blocks():
    db = _db_with_usage(_usage_row(tokens_in=500, tokens_out=500))
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is False
    assert status.warn is True
    assert "token" in status.reason.lower()
    assert status.remaining_tokens == 0


def test_check_budget_cost_cap_blocks_even_if_tokens_ok():
    db = _db_with_usage(_usage_row(tokens_in=10, tokens_out=10, cost_cents=500))
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is False
    assert "cost" in status.reason.lower()


def test_check_budget_admin_can_raise_cap(_settings_store):
    """Increasing the AppSetting cap immediately re-opens the budget."""

    # First. peg the user at 1000 tokens. should block.
    db = _db_with_usage(_usage_row(tokens_in=500, tokens_out=500))
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is False

    # Then. admin doubles the cap. Same usage row, now under.
    _settings_store["agent_daily_token_budget_per_user"] = 2000
    db = _db_with_usage(_usage_row(tokens_in=500, tokens_out=500))
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is True


def test_check_budget_below_warn_threshold_no_warn():
    db = _db_with_usage(_usage_row(tokens_in=100, tokens_out=100))
    status = _run(check_budget(uuid.uuid4(), db))
    assert status.ok is True
    assert status.warn is False
    assert status.reason == ""


# ── record_usage ─────────────────────────────────────────────────────


def test_record_usage_executes_upsert_and_commits():
    """``record_usage`` MUST issue exactly one INSERT...ON CONFLICT
    against ``agent_daily_usage`` and commit. We can't easily inspect
    the SQL here without a real connection, so we assert the call
    shape against a MagicMock session."""

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    _run(
        record_usage(
            user_id=uuid.uuid4(),
            tokens_in=100,
            tokens_out=200,
            cost_cents=3,
            db=db,
            increment_run_count=True,
        )
    )

    assert db.execute.await_count == 1
    assert db.commit.await_count == 1
    # The single arg is a SQLAlchemy Insert statement against the
    # AgentDailyUsage table. Sanity check the table name appears.
    stmt = db.execute.await_args.args[0]
    assert "agent_daily_usage" in str(stmt).lower()


def test_record_usage_clamps_negative_inputs_to_zero():
    """Defense in depth. accidental negative deltas must not bleed
    through to the rollup."""

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    _run(
        record_usage(
            user_id=uuid.uuid4(),
            tokens_in=-50,
            tokens_out=-1,
            cost_cents=-100,
            db=db,
        )
    )

    stmt = db.execute.await_args.args[0]
    # Bound parameters carry the clamped values.
    params = stmt.compile().params
    assert params["tokens_in"] == 0
    assert params["tokens_out"] == 0
    assert params["cost_cents"] == 0
