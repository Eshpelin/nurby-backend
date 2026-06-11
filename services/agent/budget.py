"""Per-user daily budget enforcement for agent runs.

Pre-flight call from the driver. ``check_budget`` reads the user's
rollup row in ``agent_daily_usage`` for today, compares against the
``agent_daily_token_budget_per_user`` and ``agent_daily_cost_cents_per_user``
AppSettings, and returns a structured status the driver can act on.

Post-call from the driver. ``record_usage`` UPSERTs the rollup row
atomically using Postgres ``ON CONFLICT DO UPDATE``. Safe under
concurrent agent runs for the same user (e.g. two browser tabs).

``estimate_cost`` is a pure helper. It looks up a rough per-model
price in cents per 1k tokens and returns the call cost. The numbers
are best-effort approximations; update ``MODEL_PRICING`` when provider
prices move.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.app_settings import get_setting
from shared.models import AgentDailyUsage

_logger = logging.getLogger("nurby.agent.budget")


# ── Pricing table ──────────────────────────────────────────────────
#
# Cents per 1000 tokens, per direction. APPROXIMATE. Update when
# provider prices change. Numbers reflect public list pricing as of
# the Wave 1A authoring date and are intentionally rounded up so
# budget enforcement errs conservative.
#
# Source notes (current as of authoring; verify before tightening
# budget defaults):
# * Anthropic Claude Sonnet 4.x. ~ 0.3c in / 1.5c out per 1k
# * Anthropic Claude Opus 4.x.   ~ 1.5c in / 7.5c out per 1k
# * OpenAI GPT-4o.               ~ 0.25c in / 1.0c out per 1k
# * OpenAI GPT-4o-mini.          ~ 0.015c in / 0.06c out per 1k
# * Google Gemini 1.5 Flash.     ~ 0.01c in / 0.03c out per 1k
# * Google Gemini 1.5 Pro.       ~ 0.125c in / 0.5c out per 1k
# * Ollama (local).              free
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (cents_per_1k_in, cents_per_1k_out)
    "claude-sonnet-4": (0.3, 1.5),
    "claude-sonnet-4.5": (0.3, 1.5),
    "claude-sonnet-4.6": (0.3, 1.5),
    "claude-sonnet-4.7": (0.3, 1.5),
    "claude-opus-4": (1.5, 7.5),
    "claude-opus-4.7": (1.5, 7.5),
    "gpt-4o": (0.25, 1.0),
    "gpt-4o-mini": (0.015, 0.06),
    "gemini-1.5-flash": (0.01, 0.03),
    "gemini-flash": (0.01, 0.03),
    "gemini-1.5-pro": (0.125, 0.5),
}
_DEFAULT_PRICING = (0.3, 1.5)  # fallback. claude-sonnet-equivalent.


@dataclass(frozen=True)
class BudgetStatus:
    """Result of ``check_budget``.

    * ``ok`` False blocks the run outright.
    * ``warn`` True means the user has crossed the soft warning
      threshold but is still allowed to proceed. Driver should
      bubble this up to the UI as a banner.
    * ``reason`` is a short human-readable string for the UI when
      ``ok`` is False.
    * ``remaining_*`` are positive integers floored at 0.
    """

    ok: bool
    warn: bool
    reason: str
    remaining_tokens: int
    remaining_cost_cents: int
    used_tokens: int
    used_cost_cents: int
    token_budget: int
    cost_budget_cents: int


def _normalize_model(name: str | None) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    # Strip a leading provider prefix ("anthropic/claude-sonnet-4").
    if "/" in n:
        n = n.split("/", 1)[1]
    return n


def estimate_cost(provider_kind: str | None, model: str | None, tokens_in: int, tokens_out: int) -> int:
    """Estimate the per-call cost in cents.

    ``provider_kind`` short-circuits free providers (``ollama``) to 0.
    Otherwise the model name is matched against ``MODEL_PRICING``
    (case-insensitive, provider-prefix-stripped). Unknown models
    fall back to a conservative Sonnet-equivalent price so we
    over-estimate rather than under-estimate.
    """

    if provider_kind and provider_kind.lower() in {"ollama", "local"}:
        return 0
    name = _normalize_model(model)
    if not name:
        in_rate, out_rate = _DEFAULT_PRICING
    else:
        in_rate, out_rate = MODEL_PRICING.get(name, _DEFAULT_PRICING)
        # Try a prefix match if no exact hit. Catches versioned
        # variants like "claude-sonnet-4-20250514".
        if (in_rate, out_rate) == _DEFAULT_PRICING and name not in MODEL_PRICING:
            for key, rates in MODEL_PRICING.items():
                if name.startswith(key):
                    in_rate, out_rate = rates
                    break
    cents = (tokens_in * in_rate + tokens_out * out_rate) / 1000.0
    # Round up so we never under-charge a budget.
    import math

    return int(math.ceil(cents))


async def check_budget(user_id: uuid.UUID, db: AsyncSession) -> BudgetStatus:
    """Return the user's remaining daily budget.

    Reads ``agent_daily_usage`` for today and compares against the
    AppSetting caps. ``warn`` is True once usage crosses
    ``agent_warn_threshold_pct`` (default 80) on either dimension.
    """

    token_budget = int(await get_setting("agent_daily_token_budget_per_user") or 0)
    cost_budget = int(await get_setting("agent_daily_cost_cents_per_user") or 0)
    warn_pct = int(await get_setting("agent_warn_threshold_pct") or 80)

    today = datetime.now(timezone.utc).date()
    stmt = select(AgentDailyUsage).where(
        AgentDailyUsage.user_id == user_id,
        AgentDailyUsage.usage_date == today,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()

    used_tokens = (row.tokens_in + row.tokens_out) if row else 0
    used_cents = row.cost_cents if row else 0

    remaining_tokens = max(0, token_budget - used_tokens)
    remaining_cost = max(0, cost_budget - used_cents)

    over_token = token_budget > 0 and used_tokens >= token_budget
    over_cost = cost_budget > 0 and used_cents >= cost_budget
    if over_token or over_cost:
        reason_parts = []
        if over_token:
            reason_parts.append(f"daily token budget {token_budget} reached")
        if over_cost:
            reason_parts.append(f"daily cost budget {cost_budget}c reached")
        return BudgetStatus(
            ok=False,
            warn=True,
            reason="; ".join(reason_parts),
            remaining_tokens=0,
            remaining_cost_cents=0,
            used_tokens=used_tokens,
            used_cost_cents=used_cents,
            token_budget=token_budget,
            cost_budget_cents=cost_budget,
        )

    token_pct = (used_tokens * 100 / token_budget) if token_budget else 0
    cost_pct = (used_cents * 100 / cost_budget) if cost_budget else 0
    warn = token_pct >= warn_pct or cost_pct >= warn_pct
    reason = ""
    if warn:
        reason = f"usage at {int(max(token_pct, cost_pct))}% of daily budget"

    return BudgetStatus(
        ok=True,
        warn=warn,
        reason=reason,
        remaining_tokens=remaining_tokens,
        remaining_cost_cents=remaining_cost,
        used_tokens=used_tokens,
        used_cost_cents=used_cents,
        token_budget=token_budget,
        cost_budget_cents=cost_budget,
    )


async def record_usage(
    user_id: uuid.UUID,
    tokens_in: int,
    tokens_out: int,
    cost_cents: int,
    db: AsyncSession,
    *,
    increment_run_count: bool = False,
) -> None:
    """Atomically increment today's rollup for ``user_id``.

    Uses Postgres ``INSERT ... ON CONFLICT (user_id, usage_date) DO
    UPDATE`` so concurrent agent runs from the same user converge to
    the correct total without an explicit transaction lock.
    """

    today = datetime.now(timezone.utc).date()
    payload: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "usage_date": today,
        "tokens_in": max(0, int(tokens_in)),
        "tokens_out": max(0, int(tokens_out)),
        "cost_cents": max(0, int(cost_cents)),
        "run_count": 1 if increment_run_count else 0,
    }
    stmt = pg_insert(AgentDailyUsage).values(**payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentDailyUsage.user_id, AgentDailyUsage.usage_date],
        set_={
            "tokens_in": AgentDailyUsage.tokens_in + stmt.excluded.tokens_in,
            "tokens_out": AgentDailyUsage.tokens_out + stmt.excluded.tokens_out,
            "cost_cents": AgentDailyUsage.cost_cents + stmt.excluded.cost_cents,
            "run_count": AgentDailyUsage.run_count + stmt.excluded.run_count,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await db.execute(stmt)
    await db.commit()


__all__ = [
    "BudgetStatus",
    "MODEL_PRICING",
    "check_budget",
    "estimate_cost",
    "record_usage",
]
