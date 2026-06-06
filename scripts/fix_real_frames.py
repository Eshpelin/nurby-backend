"""Replace the seeded observations' synthetic PIL thumbnails with REAL
frames the pipeline cut from the demo footage, matched by content
(person scenes for person events, vehicle scenes for vehicle events).
Run in the api container."""

from __future__ import annotations

import asyncio
import os
import random

from sqlalchemy import select

from shared.database import async_session
from shared.models import Observation

random.seed(23)

PERSON = {"person"}
VEHICLE = {"car", "truck", "van", "bus", "motorcycle"}


def labels_of(o):
    return {x.get("label") for x in (o.object_detections or {}).get("objects", [])}


async def main():
    async with async_session() as db:
        real = list((await db.execute(
            select(Observation)
            .where(Observation.vlm_provider.is_(None))
            .where(Observation.thumbnail_path.is_not(None))
            .order_by(Observation.started_at.desc())
            .limit(1500)
        )).scalars())
        person_frames, vehicle_frames, any_frames = [], [], []
        for o in real:
            if not os.path.exists(o.thumbnail_path):
                continue
            lb = labels_of(o)
            any_frames.append(o.thumbnail_path)
            if lb & VEHICLE:
                vehicle_frames.append(o.thumbnail_path)
            if lb & PERSON:
                person_frames.append(o.thumbnail_path)
        for pool in (person_frames, vehicle_frames, any_frames):
            random.shuffle(pool)

        def take(pool, used):
            for f in pool:
                if f not in used:
                    used.add(f)
                    return f
            return None

        seeded = list((await db.execute(
            select(Observation).where(Observation.vlm_provider == "gemma3:4b")
            .order_by(Observation.started_at.desc())
        )).scalars())
        used = set()
        n = 0
        for o in seeded:
            lb = labels_of(o)
            pool = (person_frames if lb & PERSON else
                    vehicle_frames if lb & VEHICLE else any_frames)
            frame = take(pool, used) or take(any_frames, used)
            if frame:
                o.thumbnail_path = frame
                n += 1
        await db.commit()
        print(f"reassigned {n}/{len(seeded)} seeded observations to real frames "
              f"(person={len(person_frames)} vehicle={len(vehicle_frames)} any={len(any_frames)})")


if __name__ == "__main__":
    asyncio.run(main())
