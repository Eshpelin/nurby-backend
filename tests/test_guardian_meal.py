"""Tests for guardian meal-attendance detection (caption-based eating action)."""

import uuid
from datetime import datetime

import pytest

from services.perception import guardian_meal as gm

DEP = str(uuid.uuid4())
LUNCH = datetime(2026, 6, 8, 13, 0, 0)
MIDNIGHT = datetime(2026, 6, 8, 0, 30, 0)
CAM = uuid.uuid4()


def _dets():
    return {"faces": [{"person_id": DEP, "person_name": "Inara", "bbox": [1, 2, 3, 4]}]}


def setup_function():
    gm.reset_state()


def test_looks_like_eating():
    assert gm.looks_like_eating("An elderly woman eating soup at a table") is True
    assert gm.looks_like_eating("A resident having lunch in their room") is True
    assert gm.looks_like_eating("A person standing in a hallway") is False
    assert gm.looks_like_eating("An empty dining room with tables") is False


def test_current_meal_windows():
    assert gm.current_meal(LUNCH) == "lunch"
    assert gm.current_meal(MIDNIGHT) is None


@pytest.mark.asyncio
async def test_records_eating_anywhere_once_per_meal(monkeypatch):
    calls = []

    async def fake_emit(name, camera_id, meal):
        calls.append((name, meal))

    monkeypatch.setattr(gm, "_safe_emit", fake_emit)
    # eating in their own room (no dining zone needed) during lunch
    await gm.process_caption("Inara is eating a sandwich in her room", _dets(), CAM, LUNCH)
    assert calls == [("Inara", "lunch")]
    # same meal/day -> deduped
    await gm.process_caption("Inara is eating again", _dets(), CAM, LUNCH)
    assert calls == [("Inara", "lunch")]


@pytest.mark.asyncio
async def test_no_record_when_not_eating_or_off_window(monkeypatch):
    calls = []

    async def fake_emit(name, camera_id, meal):
        calls.append(meal)

    monkeypatch.setattr(gm, "_safe_emit", fake_emit)
    # present but not eating
    await gm.process_caption("Inara is watching television", _dets(), CAM, LUNCH)
    assert calls == []
    # eating but outside any meal window
    await gm.process_caption("Inara is eating a snack", _dets(), CAM, MIDNIGHT)
    assert calls == []
    # eating in window but no recognised dependant
    await gm.process_caption("Someone is eating", {"faces": []}, CAM, LUNCH)
    assert calls == []
