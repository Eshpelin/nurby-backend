"""Unit tests for services.guardian.entitlements.

Pure logic, no DB. Links are SimpleNamespace shaped like GuardianLink rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.guardian import entitlements as ent

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def link(**kw):
    base = dict(
        tier="full",
        premium=False,
        live_presence=False,
        live_video=False,
        audio=False,
        is_primary_parent=False,
        revoked_at=None,
        expires_at=None,
        reveal_min_confidence=None,
        last_image_served_at=None,
        alert_prefs=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── active / expiry ──────────────────────────────────────────────────

def test_active_default():
    assert ent.is_active(link(), NOW) is True


def test_revoked_is_inactive():
    assert ent.is_active(link(revoked_at=NOW - timedelta(hours=1)), NOW) is False


def test_expired_is_inactive():
    assert ent.is_active(link(expires_at=NOW - timedelta(seconds=1)), NOW) is False


def test_future_expiry_active():
    assert ent.is_active(link(expires_at=NOW + timedelta(days=1)), NOW) is True


# ── delay ────────────────────────────────────────────────────────────

def test_free_tier_delayed():
    assert ent.effective_delay_seconds(link(), 1800) == 1800


def test_live_presence_removes_delay():
    assert ent.effective_delay_seconds(link(live_presence=True), 1800) == 0


def test_cutoff_free():
    c = ent.cutoff_time(link(), 1800, NOW)
    assert c == NOW - timedelta(seconds=1800)


def test_cutoff_paid_is_now():
    c = ent.cutoff_time(link(live_presence=True), 1800, NOW)
    assert c == NOW


# ── image throttle ───────────────────────────────────────────────────

def test_first_image_allowed():
    assert ent.image_allowed(link(), 3600, NOW) is True


def test_second_image_within_hour_blocked():
    lk = link(last_image_served_at=NOW - timedelta(minutes=10))
    assert ent.image_allowed(lk, 3600, NOW) is False


def test_image_after_interval_allowed():
    lk = link(last_image_served_at=NOW - timedelta(minutes=61))
    assert ent.image_allowed(lk, 3600, NOW) is True


def test_live_video_lifts_image_cap():
    lk = link(live_video=True, last_image_served_at=NOW - timedelta(seconds=1))
    assert ent.image_allowed(lk, 3600, NOW) is True


def test_seconds_until_next_image():
    lk = link(last_image_served_at=NOW - timedelta(minutes=10))
    assert ent.seconds_until_next_image(lk, 3600, NOW) == 3000
    assert ent.seconds_until_next_image(link(), 3600, NOW) == 0


# ── tier + entitlement gating ────────────────────────────────────────

@pytest.mark.parametrize("cap", [ent.CAP_STATUS, ent.CAP_TIMELINE, ent.CAP_IMAGE])
def test_full_basic_caps(cap):
    assert ent.can_view(link(), cap) is True


def test_alerts_only_sees_nothing():
    lk = link(tier="alerts_only", premium=True, live_video=True, audio=True)
    for cap in (
        ent.CAP_STATUS, ent.CAP_TIMELINE, ent.CAP_IMAGE, ent.CAP_LIVE_VIDEO,
        ent.CAP_AUDIO, ent.CAP_RECAP, ent.CAP_SEARCH,
    ):
        assert ent.can_view(lk, cap) is False


def test_recap_requires_premium():
    assert ent.can_view(link(), ent.CAP_RECAP) is False
    assert ent.can_view(link(premium=True), ent.CAP_RECAP) is True


def test_search_requires_premium():
    assert ent.can_view(link(premium=True), ent.CAP_SEARCH) is True
    assert ent.can_view(link(), ent.CAP_SEARCH) is False


def test_live_video_requires_full_and_flag():
    assert ent.can_view(link(live_video=True), ent.CAP_LIVE_VIDEO) is True
    assert ent.can_view(link(), ent.CAP_LIVE_VIDEO) is False
    # summary tier can never see live video even with the flag
    assert ent.can_view(link(tier="summary", live_video=True), ent.CAP_LIVE_VIDEO) is False


def test_audio_requires_flag_both_tiers():
    assert ent.can_view(link(audio=True), ent.CAP_AUDIO) is True
    assert ent.can_view(link(tier="summary", audio=True), ent.CAP_AUDIO) is True
    assert ent.can_view(link(audio=False), ent.CAP_AUDIO) is False


def test_summary_no_live_video_but_has_recap():
    lk = link(tier="summary", premium=True)
    assert ent.can_view(lk, ent.CAP_RECAP) is True
    assert ent.can_view(lk, ent.CAP_LIVE_VIDEO) is False


# ── reveal threshold ─────────────────────────────────────────────────

def test_reveal_default():
    assert ent.reveal_threshold(link(), system_default=0.90) == 0.90


def test_reveal_facility_raises():
    assert ent.reveal_threshold(link(), system_default=0.90, facility_floor=0.95) == 0.95


def test_reveal_link_can_only_raise():
    # link override below floor is ignored (max wins)
    lk = link(reveal_min_confidence=0.80)
    assert ent.reveal_threshold(lk, system_default=0.90) == 0.90
    # link override above floor wins
    l2 = link(reveal_min_confidence=0.97)
    assert ent.reveal_threshold(l2, system_default=0.90, facility_floor=0.93) == 0.97


def test_reveal_camera_floor():
    assert ent.reveal_threshold(link(), system_default=0.90, camera_floor=0.99) == 0.99


# ── extra guardians free ─────────────────────────────────────────────

def test_extra_guardians_locked_when_no_paid_parent():
    links = [link(is_primary_parent=True), link(is_primary_parent=False, premium=True)]
    assert ent.extra_guardians_unlocked(links, NOW) is False


def test_extra_guardians_unlocked_by_paid_parent():
    links = [link(is_primary_parent=True, live_presence=True)]
    assert ent.extra_guardians_unlocked(links, NOW) is True


def test_extra_guardians_ignores_revoked_paid_parent():
    links = [link(is_primary_parent=True, premium=True, revoked_at=NOW - timedelta(hours=1))]
    assert ent.extra_guardians_unlocked(links, NOW) is False


# ── alert prefs ──────────────────────────────────────────────────────

def test_alert_defaults():
    lk = link()
    assert ent.alert_enabled(lk, "arrived") is True
    assert ent.alert_enabled(lk, "not_seen") is False


def test_alert_prefs_override():
    lk = link(alert_prefs={"arrived": False, "not_seen": True})
    assert ent.alert_enabled(lk, "arrived") is False
    assert ent.alert_enabled(lk, "not_seen") is True


def test_sanitize_restricts_to_allowed():
    out = ent.sanitize_alert_prefs(
        {"arrived": True, "entered_zone": True}, allowed=["arrived", "departed"]
    )
    assert out["arrived"] is True
    assert out["entered_zone"] is False  # not in allowed set


def test_sanitize_coerces_booleans():
    out = ent.sanitize_alert_prefs({"arrived": "yes", "picked_up": 0})
    assert out["arrived"] is True
    assert out["picked_up"] is False


def test_entitlement_summary_shape():
    s = ent.entitlement_summary(link(premium=True, live_presence=True))
    assert s["delayed"] is False
    assert s["premium"] is True
    assert s["can"][ent.CAP_RECAP] is True
    assert s["can"][ent.CAP_LIVE_VIDEO] is False
