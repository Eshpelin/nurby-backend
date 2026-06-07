"""Auto pickup-escort detection.

When a dependant departs, look back over a short window on the departure camera
and infer who they left with: the most frequently co-present *other* person, and
the most frequent vehicle plate. That escort is then verified against the
approved-pickup registry by the alert layer.

The counting logic is a pure function over detection dicts so it is fully
unit-testable; the async wrapper just pulls the observations.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from shared.models import Observation


def _faces(person_detections: dict | None) -> list[dict]:
    if not person_detections:
        return []
    if isinstance(person_detections.get("faces"), list):
        return person_detections["faces"]
    # Fallback shape used elsewhere: {"persons": [{"person_id": ...}]}
    if isinstance(person_detections.get("persons"), list):
        return person_detections["persons"]
    return []


def summarize_escort(detections: list[dict], dependant_id) -> dict:
    """Pure escort inference.

    ``detections`` is a list of ``{"person_detections": ..., "vehicle_detections": ...}``
    dicts (one per observation in the window). Returns the top co-present
    person id (string) and top plate, each with a support count.
    """
    dep = str(dependant_id)
    people: Counter = Counter()
    plates: Counter = Counter()
    for d in detections:
        for f in _faces(d.get("person_detections")):
            pid = f.get("person_id")
            if pid and str(pid) != dep:
                people[str(pid)] += 1
        vd = d.get("vehicle_detections") or {}
        for v in vd.get("vehicles", []) if isinstance(vd.get("vehicles"), list) else []:
            plate = v.get("plate_text")
            if plate:
                plates[str(plate)] += 1

    top_person, person_n = (people.most_common(1)[0] if people else (None, 0))
    top_plate, plate_n = (plates.most_common(1)[0] if plates else (None, 0))
    return {
        "escort_person_id": top_person,
        "escort_person_support": person_n,
        "escort_plate": top_plate,
        "escort_plate_support": plate_n,
        "has_escort": bool(top_person or top_plate),
    }


async def detect_escort(
    db,
    dependant_id: uuid.UUID,
    camera_id: uuid.UUID,
    departure_at,
    window_seconds: int = 120,
) -> dict:
    """Pull the window of observations on the departure camera and infer the
    escort. Returns the same shape as ``summarize_escort`` (has_escort=False
    when nothing co-present was found)."""
    if camera_id is None or departure_at is None:
        return summarize_escort([], dependant_id)
    start = departure_at - timedelta(seconds=max(1, int(window_seconds)))
    end = departure_at + timedelta(seconds=10)
    rows = (
        await db.execute(
            select(Observation)
            .where(Observation.camera_id == camera_id)
            .where(Observation.started_at >= start)
            .where(Observation.started_at <= end)
            .order_by(Observation.started_at.desc())
            .limit(200)
        )
    ).scalars().all()
    detections = [
        {
            "person_detections": o.person_detections,
            "vehicle_detections": o.vehicle_detections,
        }
        for o in rows
    ]
    return summarize_escort(detections, dependant_id)
