"""Tests for vehicle identity (perception) and the get_vehicles agent tool."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.perception import vehicles as veh


def _run(coro):
    return asyncio.run(coro)


# ── pure helpers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("abc 123", "ABC123"),
    ("AB-12-CD", "AB12CD"),
    ("  xy ", None),       # < 3 chars
    ("", None),
    (None, None),
])
def test_norm_plate(raw, expected):
    assert veh._norm_plate(raw) == expected


def test_bbox_center_inside():
    vehicle = [100, 100, 300, 300]
    assert veh._bbox_center_inside([180, 250, 260, 290], vehicle) is True   # center 220,270 inside
    assert veh._bbox_center_inside([0, 0, 20, 20], vehicle) is False        # center 10,10 outside
    assert veh._bbox_center_inside([], vehicle) is False                    # malformed


def test_parse_attributes():
    color, make, model = veh._parse_attributes("Red Nissan sedan with tinted windows")
    assert color == "Red"
    assert make == "Nissan"
    # "sedan" is a body word, not a model, so model is omitted.
    assert model is None
    color2, make2, _ = veh._parse_attributes("a white pickup truck")
    assert color2 == "White"
    assert make2 is None


# ── identify_vehicles ────────────────────────────────────────────────

def _exec_none():
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    return res


def _stub_db():
    db = AsyncMock()
    added: list = []
    db.add = MagicMock(side_effect=lambda o: added.append(o))

    async def _flush():
        for o in added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=_flush)
    db.execute = AsyncMock(return_value=_exec_none())
    db._added = added
    return db


def test_identify_creates_vehicle_for_plated_detection():
    db = _stub_db()
    cam = uuid.uuid4()
    ts = datetime.now(timezone.utc)
    detections = [
        {"label": "car", "confidence": 0.9, "bbox": [100, 100, 300, 300]},
        {"label": "license_plate", "confidence": 0.8, "bbox": [180, 250, 260, 290], "plate_text": "ABC 123"},
    ]
    vd, jobs = _run(veh.identify_vehicles(db, cam, detections, ts))

    assert vd["count"] == 1
    entry = vd["vehicles"][0]
    assert entry["plate_text"] == "ABC123"
    assert entry["vehicle_id"] is not None
    assert entry["identity_key"] == "ABC123"
    # A new Vehicle row was created and queued for a description.
    assert len(db._added) == 1
    assert db._added[0].identity_key == "ABC123"
    assert db._added[0].license_plate == "ABC123"
    assert len(jobs) == 1


def test_identify_plateless_vehicle_gets_no_identity():
    db = _stub_db()
    detections = [{"label": "car", "confidence": 0.9, "bbox": [100, 100, 300, 300]}]
    vd, jobs = _run(veh.identify_vehicles(db, uuid.uuid4(), detections, datetime.now(timezone.utc)))

    assert vd["count"] == 1
    entry = vd["vehicles"][0]
    assert entry["plate_text"] is None
    assert entry["vehicle_id"] is None     # plateless -> no persistent identity
    assert db._added == []                  # no Vehicle row created
    assert jobs == []


def test_identify_returns_none_without_vehicles():
    db = _stub_db()
    detections = [{"label": "chair", "confidence": 0.9, "bbox": [0, 0, 10, 10]}]
    vd, jobs = _run(veh.identify_vehicles(db, uuid.uuid4(), detections, datetime.now(timezone.utc)))
    assert vd is None and jobs == []


def test_identify_existing_plate_updates_not_inserts():
    db = _stub_db()
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.sighting_count = 4
    existing.vehicle_type = "car"
    existing.description_status = "done"
    existing.description = "Red car"
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=res)

    detections = [
        {"label": "car", "confidence": 0.9, "bbox": [100, 100, 300, 300]},
        {"label": "license_plate", "bbox": [180, 250, 260, 290], "plate_text": "ABC123"},
    ]
    vd, jobs = _run(veh.identify_vehicles(db, uuid.uuid4(), detections, datetime.now(timezone.utc)))

    assert vd["vehicles"][0]["vehicle_id"] == str(existing.id)
    assert existing.sighting_count == 5     # incremented
    assert db._added == []                  # reused, not inserted
    assert jobs == []                       # already described


# ── get_vehicles agent tool ──────────────────────────────────────────

def _fake_vehicle(**kw):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.display_name = kw.get("display_name", "Plate ABC123")
    v.license_plate = kw.get("license_plate", "ABC123")
    v.vehicle_type = kw.get("vehicle_type", "car")
    v.color = kw.get("color")
    v.make = kw.get("make")
    v.model = kw.get("model")
    v.description = kw.get("description")
    v.first_seen_at = datetime.now(timezone.utc)
    v.last_seen_at = datetime.now(timezone.utc)
    v.sighting_count = kw.get("sighting_count", 3)
    return v


def _tool_ctx(vehicles):
    res = MagicMock()
    scal = MagicMock()
    scal.all.return_value = vehicles
    res.scalars.return_value = scal
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    return {"db": db, "user": MagicMock()}


def test_get_vehicles_returns_all():
    from services.agent.tools import get_vehicles
    ctx = _tool_ctx([_fake_vehicle(), _fake_vehicle(license_plate="XYZ789")])
    out = _run(get_vehicles(ctx))
    assert out["count"] == 2


def test_get_vehicles_filters_by_plate():
    from services.agent.tools import get_vehicles
    ctx = _tool_ctx([_fake_vehicle(license_plate="ABC123"), _fake_vehicle(license_plate="XYZ789")])
    out = _run(get_vehicles(ctx, plate="xyz"))
    assert out["count"] == 1
    assert out["vehicles"][0]["license_plate"] == "XYZ789"


def test_get_vehicles_filters_by_query():
    from services.agent.tools import get_vehicles
    ctx = _tool_ctx([
        _fake_vehicle(description="Red Nissan sedan", make="Nissan", color="Red"),
        _fake_vehicle(description="White truck", vehicle_type="truck"),
    ])
    out = _run(get_vehicles(ctx, query="nissan"))
    assert out["count"] == 1
    assert out["vehicles"][0]["make"] == "Nissan"
