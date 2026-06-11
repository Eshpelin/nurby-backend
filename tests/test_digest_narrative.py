"""Tests for the narrative morning-digest prompt building.

Guards the rewrite from regressing back into a stats dump. the prompt must
feed a chronological event timeline (not counts), use friendly clock
times, and ask for a quiet-night sentence when there is nothing notable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.perception import daily_digest as dd

WS = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
WE = WS + timedelta(hours=24)


def test_quiet_when_no_events():
    prompt = dd._build_prompt({"notable_events": []}, WS, WE)
    low = prompt.lower()
    assert "quiet" in low
    assert "single" in low or "one" in low
    # No statistics language.
    assert "count" not in low


def test_narrative_prompt_lists_events_and_bans_counts():
    facts = {
        "notable_events": [
            {"when": "3:10 AM", "text": "Sarah seen on Kitchen"},
            {"when": "3:12 AM", "text": "fridge opened, person took an item"},
            {"when": "7:02 AM", "text": "Red Nissan (ABC123) seen"},
        ]
    }
    prompt = dd._build_prompt(facts, WS, WE)
    assert "3:10 AM - Sarah seen on Kitchen" in prompt
    assert "Red Nissan (ABC123) seen" in prompt
    low = prompt.lower()
    assert "no counts" in low
    assert "no bullet points" in low
    # Must not leak ISO timestamps into the instruction.
    assert "t00:00" not in low


def test_system_prompt_is_narrative_not_analyst():
    sp = dd.DAILY_SYSTEM_PROMPT.lower()
    assert "housemate" in sp
    assert "do not list statistics" in sp or "no statistics" in sp or "not a report" in sp


def test_incident_phrase():
    assert dd._incident_phrase("person", "Sarah") == "Sarah"
    assert dd._incident_phrase("object", "car,person") == "car and person"
    assert dd._incident_phrase("cluster", "abc") == "an unrecognized person"


def test_fmt_clock():
    # Friendly clock, no date.
    out = dd._fmt_clock("2026-06-04T15:05:00+00:00", timezone.utc)
    assert out in ("3:05 PM", "15:05")  # platform strftime may vary
    assert dd._fmt_clock(None, None) == ""
    assert dd._fmt_clock("not-a-date", None) == ""


def test_window_phrase():
    # 24h window ending in the morning.
    assert isinstance(dd._window_phrase(WS, WE), str)
    overnight = dd._window_phrase(WE - timedelta(hours=8), WE)
    assert "overnight" in overnight or "yesterday" in overnight
