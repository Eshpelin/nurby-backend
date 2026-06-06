"""Unit tests for the adaptive live-VLM pacing decision."""

from __future__ import annotations

from services.perception.vlm_pacing import (
    HIGH_BACKLOG_CAP,
    NORMAL_BACKLOG_CAP,
    should_enqueue,
)


# ---- high priority -------------------------------------------------------

def test_high_priority_enqueues_when_backlog_below_ceiling():
    assert should_enqueue(
        "high", avg_latency=10.0, backlog=HIGH_BACKLOG_CAP - 1,
        base_interval=5, seconds_since_last=0.0,
    ) is True


def test_high_priority_blocked_at_hard_ceiling():
    assert should_enqueue(
        "high", avg_latency=0.0, backlog=HIGH_BACKLOG_CAP,
        base_interval=0, seconds_since_last=999,
    ) is False


def test_high_priority_ignores_cadence():
    # would fail the interval gate, but high priority bypasses it
    assert should_enqueue(
        "high", avg_latency=30.0, backlog=0,
        base_interval=30, seconds_since_last=0.0,
    ) is True


# ---- normal priority. backlog guard -------------------------------------

def test_normal_skipped_while_backlog_nontrivial():
    assert should_enqueue(
        "normal", avg_latency=1.0, backlog=NORMAL_BACKLOG_CAP,
        base_interval=0, seconds_since_last=999,
    ) is False


def test_normal_allowed_when_backlog_clear_and_interval_elapsed():
    assert should_enqueue(
        "normal", avg_latency=2.0, backlog=0,
        base_interval=1, seconds_since_last=5.0,
    ) is True


# ---- normal priority. adaptive cadence ----------------------------------

def test_learned_latency_raises_the_gap_above_configured_floor():
    # VLM measured at 3s/frame. a 1s configured interval must not let frames
    # through every 1s. the gap self-tunes up to ~3s.
    assert should_enqueue(
        "normal", avg_latency=3.0, backlog=0,
        base_interval=1, seconds_since_last=2.0,
    ) is False
    assert should_enqueue(
        "normal", avg_latency=3.0, backlog=0,
        base_interval=1, seconds_since_last=3.1,
    ) is True


def test_configured_interval_is_a_floor_when_vlm_is_fast():
    # fast VLM (0.2s) but the camera wants at most one caption every 5s
    assert should_enqueue(
        "normal", avg_latency=0.2, backlog=0,
        base_interval=5, seconds_since_last=3.0,
    ) is False
    assert should_enqueue(
        "normal", avg_latency=0.2, backlog=0,
        base_interval=5, seconds_since_last=6.0,
    ) is True


def test_no_interval_and_no_latency_lets_first_frame_through():
    assert should_enqueue(
        "normal", avg_latency=0.0, backlog=0,
        base_interval=0, seconds_since_last=0.0,
    ) is True
