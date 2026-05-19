"""Coverage for RuleEngine._check_conditions.

Covers camera filter, day-of-week, time window (plain and overnight),
min_confidence, and timezone awareness. The min_confidence=0 and
timezone cases are xfail-marked until the Pass B engine fix lands.
"""

import asyncio
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from tests._engine_helpers import FakeRule, install_engine


# ── camera filter ─────────────────────────────────────────────────

def test_camera_ids_array_in(monkeypatch):
    cam_a = str(uuid.uuid4())
    cam_b = str(uuid.uuid4())
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"camera_ids": [cam_a, cam_b]},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"camera_id": cam_a}))
    asyncio.run(eng.evaluate({"camera_id": str(uuid.uuid4())}))
    assert rec.call_count == 1


def test_camera_id_single(monkeypatch):
    cam = str(uuid.uuid4())
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"camera_id": cam},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"camera_id": cam}))
    asyncio.run(eng.evaluate({"camera_id": str(uuid.uuid4())}))
    assert rec.call_count == 1


# ── min_confidence ────────────────────────────────────────────────

def test_min_confidence_filter(monkeypatch):
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"min_confidence": 0.5},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"confidence": 0.9}))
    asyncio.run(eng.evaluate({"confidence": 0.1}))
    asyncio.run(eng.evaluate({"confidence": None}))
    assert rec.call_count == 1


def test_min_confidence_zero_does_not_skip_falsy_check(monkeypatch):
    """min_confidence == 0 should still allow firing on confidence=0.

    Today the engine writes ``if min_conf and ...`` which falsily
    short-circuits on 0 and effectively skips the filter. That happens
    to be the desired result here (any confidence passes), so this
    test guards against future regressions where 0 starts rejecting
    everything.
    """
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"min_confidence": 0},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"confidence": 0}))
    asyncio.run(eng.evaluate({"confidence": 0.5}))
    assert rec.call_count == 2


# ── day-of-week ────────────────────────────────────────────────────

def _patch_datetime_now(monkeypatch, fixed: datetime):
    import services.events.engine as engine_mod

    class _DT:
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed.replace(tzinfo=tz) if fixed.tzinfo is None else fixed.astimezone(tz)
            return fixed

    monkeypatch.setattr(engine_mod, "datetime", _DT)


def test_days_filter_match(monkeypatch):
    # Monday 2025-01-06 noon.
    _patch_datetime_now(monkeypatch, datetime(2025, 1, 6, 12, 0))
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"days": ["mon", "tue"]},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 1


def test_days_filter_miss(monkeypatch):
    # Sunday 2025-01-05.
    _patch_datetime_now(monkeypatch, datetime(2025, 1, 5, 12, 0))
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"days": ["mon", "tue"]},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 0


# ── time window ────────────────────────────────────────────────────

def test_time_window_plain(monkeypatch):
    _patch_datetime_now(monkeypatch, datetime(2025, 1, 6, 12, 0))
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"time_after": "10:00", "time_before": "18:00"},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 1


def test_time_window_plain_miss(monkeypatch):
    _patch_datetime_now(monkeypatch, datetime(2025, 1, 6, 8, 0))
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"time_after": "10:00", "time_before": "18:00"},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 0


def test_time_window_overnight_in(monkeypatch):
    # 23:30 should be inside a 22:00→06:00 window.
    _patch_datetime_now(monkeypatch, datetime(2025, 1, 6, 23, 30))
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"time_after": "22:00", "time_before": "06:00"},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 1


def test_time_window_overnight_out(monkeypatch):
    # 10:00 is outside 22:00→06:00.
    _patch_datetime_now(monkeypatch, datetime(2025, 1, 6, 10, 0))
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"time_after": "22:00", "time_before": "06:00"},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 0


# ── timezone awareness ────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="Pass B. engine must read system_timezone and pass tz into datetime.now()",
)
def test_time_window_respects_system_timezone(monkeypatch):
    """When system_timezone is set, the day+window must be computed in
    that zone, not the host's locale or UTC.

    Fixed instant. 2025-01-06 03:00 UTC. In America/Los_Angeles that
    is the previous day (Sunday) at 19:00. A rule restricted to Mondays
    must NOT fire when evaluated as Sunday.
    """
    from zoneinfo import ZoneInfo
    from datetime import timezone as _tz

    fixed_utc = datetime(2025, 1, 6, 3, 0, tzinfo=_tz.utc)

    import services.events.engine as engine_mod

    class _DT:
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    monkeypatch.setattr(engine_mod, "datetime", _DT)

    async def fake_get_setting(key, default=None):
        if key == "system_timezone":
            return "America/Los_Angeles"
        return default

    # The Pass B engine reads system_timezone via shared.app_settings.
    import shared.app_settings as app_settings
    monkeypatch.setattr(app_settings, "get_setting", fake_get_setting)

    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "any"},
        conditions={"days": ["mon"]},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    # In LA it is Sunday 19:00, so a Monday-only rule must NOT fire.
    assert rec.call_count == 0
