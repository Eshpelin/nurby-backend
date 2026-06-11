"""Scheduled report due-logic and question shaping."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from services.api.report_scheduler import build_question, is_due

LA = ZoneInfo("America/Los_Angeles")


def _report(**kw):
    defaults = dict(
        id=uuid.uuid4(),
        enabled=True,
        hour=19,
        minute=0,
        days=None,
        last_run_at=None,
        prompt="What was Simon doing all day?",
        name="Simon daily",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# 19:00 LA on 2026-06-10 (PDT, UTC-7) == 2026-06-11 02:00 UTC.


def test_due_after_slot_never_run():
    r = _report()
    assert is_due(r, _utc(2026, 6, 11, 2, 5), LA) is True


def test_not_due_before_slot():
    r = _report()
    assert is_due(r, _utc(2026, 6, 11, 1, 55), LA) is False


def test_not_due_twice_for_same_slot():
    r = _report(last_run_at=_utc(2026, 6, 11, 2, 1))
    assert is_due(r, _utc(2026, 6, 11, 2, 30), LA) is False


def test_due_again_next_day():
    r = _report(last_run_at=_utc(2026, 6, 11, 2, 1))
    assert is_due(r, _utc(2026, 6, 12, 2, 5), LA) is True


def test_missed_slot_fires_late_not_skipped():
    # API was down at 19:00; at 22:40 local the report is still due.
    r = _report(last_run_at=_utc(2026, 6, 10, 2, 1))
    assert is_due(r, _utc(2026, 6, 11, 5, 40), LA) is True


def test_disabled_never_due():
    r = _report(enabled=False)
    assert is_due(r, _utc(2026, 6, 11, 2, 5), LA) is False


def test_day_filter():
    # 2026-06-10 is a Wednesday in LA at the 19:00 slot.
    r = _report(days=["mon", "tue"])
    assert is_due(r, _utc(2026, 6, 11, 2, 5), LA) is False
    r2 = _report(days=["wed"])
    assert is_due(r2, _utc(2026, 6, 11, 2, 5), LA) is True


def test_naive_last_run_treated_as_utc():
    r = _report(last_run_at=datetime(2026, 6, 11, 2, 1))  # naive
    assert is_due(r, _utc(2026, 6, 11, 2, 30), LA) is False


def test_build_question_includes_person_and_window():
    r = _report()
    q = build_question(r, "Simon")
    assert "Simon" in q
    assert "last 24 hours" in q
    assert r.prompt.rstrip(".") in q


def test_build_question_without_person():
    q = build_question(_report(), None)
    assert "Focus on" not in q
