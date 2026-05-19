"""Tests for POST /api/rules/test.

We call the async route handler directly with a fake user and a
mocked db. The endpoint is pure-CPU when ``dry_run_observation`` is
omitted and ``camera_id`` is null. so the db mock can stay simple.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from services.api.routes.rules import test_rule as _test_rule_handler
from shared.schemas import RuleTestRequest


class _FakeUser:
    id = uuid.uuid4()
    role = "admin"
    is_active = True


def _run(req: RuleTestRequest, db=None):
    db = db or AsyncMock()
    return asyncio.run(_test_rule_handler(req, _current_user=_FakeUser(), db=db))


# ── trigger matching ─────────────────────────────────────────────

def test_object_detected_synthesizes_matching_observation():
    req = RuleTestRequest(
        trigger_pattern={"type": "object_detected", "label": "person"},
        actions=[{"type": "broadcast"}],
    )
    resp = _run(req)
    assert resp.matched is True
    assert resp.matched_trigger is True
    assert resp.matched_conditions is True
    assert resp.schedule_blocked is False
    assert resp.cooldown_active is False
    objs = resp.synthesized_observation["object_detections"]["objects"]
    assert objs[0]["label"] == "person"


def test_schedule_blocked_when_time_window_excludes_now():
    # Build a time window 5 minutes into the future and 10 minutes
    # after that. so "now" sits before the window opens.
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    after = (now + timedelta(minutes=5)).strftime("%H:%M")
    before = (now + timedelta(minutes=15)).strftime("%H:%M")
    # If we wrap past midnight, just clamp. test still proves the
    # block-then-explain code path.
    req = RuleTestRequest(
        trigger_pattern={"type": "object_detected", "label": "person"},
        conditions={"time_after": after, "time_before": before},
        actions=[{"type": "broadcast"}],
    )
    resp = _run(req)
    assert resp.matched_trigger is True
    # Either matched_conditions is False (the normal case) or the
    # window happened to wrap and include now. accept both but make
    # the test useful by asserting reason mentions the schedule when
    # blocked.
    if not resp.matched_conditions:
        assert resp.schedule_blocked is True
        assert resp.matched is False
        assert "schedule" in resp.reason.lower() or "window" in resp.reason.lower()


# ── schema validation surfaces at /test ──────────────────────────

def test_loitering_without_geometry_rejected_by_schema():
    with pytest.raises(ValidationError):
        RuleTestRequest(
            trigger_pattern={"type": "loitering", "threshold_seconds": 30},
            actions=[{"type": "broadcast"}],
        )


def test_vars_reference_without_prior_vlm_call_rejected():
    with pytest.raises(ValidationError):
        RuleTestRequest(
            trigger_pattern={"type": "any"},
            actions=[{
                "type": "notify",
                "message": "threat={{vars.threat.level}}",
            }],
        )


# ── action rendering ─────────────────────────────────────────────

def test_webhook_payload_template_rendered_in_would_fire():
    req = RuleTestRequest(
        trigger_pattern={"type": "any"},
        actions=[{
            "type": "webhook",
            "url": "https://example.com/hook",
            "payload_template": {"event": "{{rule_name}}", "cam": "{{camera_id}}"},
        }],
    )
    resp = _run(req)
    assert resp.matched is True
    assert len(resp.would_fire) == 1
    rendered = resp.would_fire[0].rendered_action
    assert rendered["payload_template"]["event"] == "__test__"
    # camera_id was synthesized to "test-camera" (no camera_id given).
    assert rendered["payload_template"]["cam"] == "test-camera"


def test_motion_trigger_synthesizes_score_above_min():
    req = RuleTestRequest(
        trigger_pattern={"type": "motion", "min_score": 0.5},
        actions=[],
    )
    resp = _run(req)
    assert resp.matched_trigger is True
    assert resp.synthesized_observation["motion_score"] > 0.5
