"""Tests for services.guardian.alerts: recipient fan-out, pickup verify, copy."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.guardian import alerts

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def _link(**kw):
    base = dict(
        id=uuid.uuid4(),
        tier="full",
        revoked_at=None,
        expires_at=None,
        alert_prefs=None,
        live_presence=False,
        premium=False,
        live_video=False,
        audio=False,
        is_primary_parent=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _pickup(**kw):
    base = dict(active=True, linked_person_id=None, vehicle_plate=None, name="X")
    base.update(kw)
    return SimpleNamespace(**base)


# ── recipients ───────────────────────────────────────────────────────

def test_recipients_default_arrived_on():
    links = [_link(), _link(tier="alerts_only")]
    assert len(alerts.recipients_for(links, "arrived", NOW)) == 2


def test_recipients_respect_optout():
    links = [_link(alert_prefs={"arrived": False})]
    assert alerts.recipients_for(links, "arrived", NOW) == []


def test_recipients_skip_revoked():
    links = [_link(revoked_at=NOW - timedelta(hours=1))]
    assert alerts.recipients_for(links, "arrived", NOW) == []


def test_recipients_not_seen_default_off():
    links = [_link()]
    assert alerts.recipients_for(links, "not_seen", NOW) == []
    on = [_link(alert_prefs={"not_seen": True})]
    assert len(alerts.recipients_for(on, "not_seen", NOW)) == 1


# ── pickup verification ──────────────────────────────────────────────

def test_pickup_match_by_person():
    pid = uuid.uuid4()
    pickups = [_pickup(linked_person_id=pid, name="Mother")]
    out = alerts.verify_pickup(pickups, detected_person_id=pid)
    assert out["matched"] is True
    assert out["approved_name"] == "Mother"
    assert out["by"] == "person"


def test_pickup_match_by_plate_normalized():
    pickups = [_pickup(vehicle_plate="DHA-1234", name="Dad car")]
    out = alerts.verify_pickup(pickups, detected_plate="dha 1234")
    assert out["matched"] is True
    assert out["by"] == "vehicle"


def test_pickup_unrecognized():
    pickups = [_pickup(linked_person_id=uuid.uuid4())]
    out = alerts.verify_pickup(pickups, detected_person_id=uuid.uuid4())
    assert out["matched"] is False
    assert out["approved_name"] is None


def test_pickup_ignores_inactive():
    pid = uuid.uuid4()
    pickups = [_pickup(linked_person_id=pid, active=False, name="Old")]
    out = alerts.verify_pickup(pickups, detected_person_id=pid)
    assert out["matched"] is False


# ── severity + copy ──────────────────────────────────────────────────

def test_severity_unrecognized_pickup_is_warning():
    assert alerts.severity_for("picked_up", pickup_matched=False) == "warning"
    assert alerts.severity_for("picked_up", pickup_matched=True) == "info"
    assert alerts.severity_for("arrived") == "info"
    assert alerts.severity_for("not_seen") == "warning"


def test_compose_arrived():
    msg = alerts.compose_message("arrived", "Ahmed", zone="Classroom B")
    assert msg == "Ahmed arrived at Classroom B."


def test_compose_pickup_matched():
    msg = alerts.compose_message("picked_up", "Ahmed", approved_name="Mother", pickup_matched=True)
    assert msg == "Ahmed was picked up by Mother."


def test_compose_pickup_unrecognized():
    msg = alerts.compose_message("picked_up", "Ahmed", pickup_matched=False)
    assert "not on the approved-pickup list" in msg


# ── emit (mock db) ───────────────────────────────────────────────────

class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        obj.id = uuid.uuid4()
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.mark.asyncio
async def test_emit_records_notification_and_recipients():
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed", nickname=None)
    links = [_link(), _link(alert_prefs={"arrived": False})]
    db = FakeDB()
    out = await alerts.emit(db, person, "arrived", links, zone="Gate", now=NOW)
    assert len(out["recipients"]) == 1  # one opted out
    assert out["notification_id"] is not None
    assert len(db.added) == 1
    assert db.added[0].message == "Ahmed arrived at Gate."


@pytest.mark.asyncio
async def test_emit_no_recipients_no_notification():
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ahmed", nickname=None)
    links = [_link(alert_prefs={"arrived": False})]
    db = FakeDB()
    out = await alerts.emit(db, person, "arrived", links, now=NOW)
    assert out["recipients"] == []
    assert out["notification_id"] is None
    assert db.added == []
