"""Shared helpers for rule engine pytests.

The engine is exercised without a real database by prefilling
``RuleEngine._rules`` and bumping ``_last_load`` past now so the
``_maybe_reload_rules`` short-circuits. ``_store_event`` and
``execute_action`` are monkeypatched on a per-test basis so tests do
not need Postgres, Redis, or any provider.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from unittest.mock import AsyncMock


@dataclass
class FakeRule:
    name: str
    trigger_pattern: dict
    actions: list | dict = field(default_factory=lambda: [{"type": "broadcast"}])
    conditions: dict | None = None
    cooldown_seconds: int = 0
    enabled: bool = True
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    snoozed_until: object = None


def install_engine(monkeypatch, rules):
    """Build a RuleEngine, prefill rules, and stub out IO.

    Returns (engine, recorder) where recorder is an ``AsyncMock`` that
    captures every ``execute_action`` invocation as positional args.
    """
    from services.events import engine as engine_mod

    eng = engine_mod.RuleEngine()
    eng._rules = list(rules)
    # Set far in the future so _maybe_reload_rules never refreshes.
    eng._last_load = time.monotonic() + 10_000

    recorder = AsyncMock()

    async def _store_event(*args, **kwargs):
        return uuid.uuid4()

    monkeypatch.setattr(engine_mod, "execute_action", recorder)
    # Patch the bound method via the instance so the class is untouched.
    eng._store_event = _store_event  # type: ignore[method-assign]
    return eng, recorder
