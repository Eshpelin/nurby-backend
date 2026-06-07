"""Tests for services.guardian.pickup escort inference."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.guardian import pickup

DEP = uuid.uuid4()
MOM = uuid.uuid4()


def _det(person_ids=None, plates=None):
    faces = [{"person_id": str(p)} for p in (person_ids or [])]
    vehicles = [{"plate_text": pl} for pl in (plates or [])]
    return {
        "person_detections": {"faces": faces} if faces else None,
        "vehicle_detections": {"vehicles": vehicles} if vehicles else None,
    }


# ── pure summarize_escort ────────────────────────────────────────────

def test_no_escort_when_only_dependant():
    out = pickup.summarize_escort([_det([DEP]), _det([DEP])], DEP)
    assert out["has_escort"] is False
    assert out["escort_person_id"] is None


def test_picks_most_frequent_other_person():
    other = uuid.uuid4()
    dets = [_det([DEP, MOM]), _det([DEP, MOM]), _det([DEP, other])]
    out = pickup.summarize_escort(dets, DEP)
    assert out["escort_person_id"] == str(MOM)
    assert out["escort_person_support"] == 2
    assert out["has_escort"] is True


def test_picks_plate():
    out = pickup.summarize_escort([_det(plates=["DHA-1"]), _det(plates=["DHA-1"])], DEP)
    assert out["escort_plate"] == "DHA-1"
    assert out["escort_plate_support"] == 2
    assert out["has_escort"] is True


def test_dependant_excluded_even_if_frequent():
    out = pickup.summarize_escort([_det([DEP]), _det([DEP]), _det([DEP])], DEP)
    assert out["escort_person_id"] is None


def test_persons_fallback_shape():
    other = uuid.uuid4()
    det = {
        "person_detections": {"persons": [{"person_id": str(other)}]},
        "vehicle_detections": None,
    }
    out = pickup.summarize_escort([det], DEP)
    assert out["escort_person_id"] == str(other)


def test_empty():
    out = pickup.summarize_escort([], DEP)
    assert out["has_escort"] is False


# ── async detect_escort ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_escort_pulls_window():
    obs = [
        SimpleNamespace(
            person_detections={"faces": [{"person_id": str(DEP)}, {"person_id": str(MOM)}]},
            vehicle_detections=None,
        ),
        SimpleNamespace(
            person_detections={"faces": [{"person_id": str(MOM)}]},
            vehicle_detections=None,
        ),
    ]
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = obs
    db.execute = AsyncMock(return_value=res)
    out = await pickup.detect_escort(
        db, DEP, uuid.uuid4(), datetime.now(timezone.utc), 120
    )
    assert out["escort_person_id"] == str(MOM)


@pytest.mark.asyncio
async def test_detect_escort_no_camera():
    db = MagicMock()
    db.execute = AsyncMock()
    out = await pickup.detect_escort(db, DEP, None, datetime.now(timezone.utc), 120)
    assert out["has_escort"] is False
    db.execute.assert_not_called()
