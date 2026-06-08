"""Tests for guardian meal-attendance detection."""

import uuid
from datetime import datetime

import pytest

from services.perception import guardian_meal as gm

DEP = str(uuid.uuid4())
LUNCH = datetime(2026, 6, 8, 13, 0, 0)
MIDNIGHT = datetime(2026, 6, 8, 0, 30, 0)


class _Cam:
    id = uuid.uuid4()
    motion_zones = [
        {"name": "Dining hall", "points": [[0, 0], [100, 0], [100, 100], [0, 100]]},
        {"name": "Hallway", "points": [[200, 0], [300, 0], [300, 100], [200, 100]]},
    ]


def _face(bbox):
    return {"person_id": DEP, "person_name": "Inara", "bbox": bbox}


def setup_function():
    gm.reset_state()


def test_is_dining_zone():
    assert gm.is_dining_zone("Dining hall") is True
    assert gm.is_dining_zone("Cafeteria B") is True
    assert gm.is_dining_zone("Hallway") is False


def test_current_meal_windows():
    assert gm.current_meal(LUNCH) == "lunch"
    assert gm.current_meal(datetime(2026, 6, 8, 8, 0)) == "breakfast"
    assert gm.current_meal(MIDNIGHT) is None


@pytest.mark.asyncio
async def test_records_once_per_meal_then_dedupes(monkeypatch):
    calls = []

    async def fake_emit(name, camera, meal):
        calls.append((name, meal))

    monkeypatch.setattr(gm, "_safe_emit", fake_emit)
    cam = _Cam()
    face_in = [_face([40, 40, 60, 60])]  # centre (50,50) inside dining hall

    await gm.process(cam, face_in, now=LUNCH)
    assert calls == [("Inara", "lunch")]
    # same meal, same day -> deduped
    await gm.process(cam, face_in, now=LUNCH)
    assert calls == [("Inara", "lunch")]


@pytest.mark.asyncio
async def test_no_record_outside_meal_or_outside_dining(monkeypatch):
    calls = []

    async def fake_emit(name, camera, meal):
        calls.append(meal)

    monkeypatch.setattr(gm, "_safe_emit", fake_emit)
    cam = _Cam()
    # in dining zone but outside any meal window
    await gm.process(cam, [_face([40, 40, 60, 60])], now=MIDNIGHT)
    assert calls == []
    # meal window but face in the hallway, not dining
    await gm.process(cam, [_face([240, 40, 260, 60])], now=LUNCH)
    assert calls == []
