"""Cooldown gating tests for RuleEngine.

The cooldown lives in-process in ``self._cooldowns``. A second fire
within ``cooldown_seconds`` is suppressed. After the window elapses
the rule fires again. The in-process map does NOT survive a perception
restart; a regression-guard test asserts that fresh engines start with
an empty cooldown map (so a restart-aware Redis backing in Pass B can
be tested separately).
"""

import asyncio
import time

from tests._engine_helpers import FakeRule, install_engine


def test_second_fire_within_cooldown_suppressed(monkeypatch):
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        cooldown_seconds=60,
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 1


def test_second_fire_after_cooldown_passes(monkeypatch):
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        cooldown_seconds=0,
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    time.sleep(0.01)
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 2


def test_fresh_engine_has_empty_cooldown_map(monkeypatch):
    """Regression guard. A new perception process starts without any
    cached cooldowns, so a rule fires immediately after restart even
    if its last firing was inside the cooldown window. Pass B may
    move this to Redis to survive restarts; this test pins today's
    behaviour so the change is intentional.
    """
    from services.events.engine import RuleEngine

    eng_a = RuleEngine()
    eng_a._cooldowns[FakeRule(name="x", trigger_pattern={"type": "any"}).id] = time.monotonic()
    eng_b = RuleEngine()
    assert eng_b._cooldowns == {}
