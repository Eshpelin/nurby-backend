"""Seed a dev database with the union of every eval fixture's seed
block so the /ask UI can be exercised against realistic data.

Usage.
    python -m scripts.seed_eval_db

This is a developer-ergonomics script. CI does NOT call it. The eval
runner itself does NOT call it; the runner stays DB-free and uses the
seed blocks in-process. The two consumers are intentionally separate
so a fixture YAML edit cannot accidentally rewrite production data.

What gets inserted.
- One Camera row per unique ``camera_id`` referenced by any fixture.
- One Observation row per ``seed.observations[*]`` entry.
- Other entities (Person, Journey, UserCameraAccess) are NOT touched
  by this script in v1; add them as need arises.

Idempotency. The script uses INSERT ... ON CONFLICT DO NOTHING by id
so re-running is safe.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.agent.eval import list_fixture_paths, load_fixture
from shared.database import async_session
from shared.models import Camera, Observation

logger = logging.getLogger("nurby.scripts.seed_eval_db")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def main() -> None:
    cameras: dict[uuid.UUID, dict] = {}
    observations: list[dict] = []

    for path in list_fixture_paths():
        fx = load_fixture(path)
        for cam in fx.seed.get("cameras") or []:
            try:
                cid = uuid.UUID(cam["id"])
            except (KeyError, ValueError):
                continue
            cameras.setdefault(
                cid,
                {
                    "id": cid,
                    "name": cam.get("name") or f"Eval Camera {cid}",
                    "location_label": cam.get("location_label"),
                    "status": cam.get("status") or "online",
                },
            )
        for obs in fx.seed.get("observations") or []:
            try:
                oid = uuid.UUID(obs["id"])
                cam_id = uuid.UUID(obs["camera_id"])
            except (KeyError, ValueError):
                continue
            ts = _parse_ts(obs["timestamp"]) if obs.get("timestamp") else datetime.now(timezone.utc)
            observations.append(
                {
                    "id": oid,
                    "camera_id": cam_id,
                    "started_at": ts,
                    "ended_at": ts,
                    "vlm_description": obs.get("description"),
                    "object_detections": obs.get("detections"),
                    "person_detections": {"faces": [{"person_name": n} for n in obs.get("person_names") or []]},
                }
            )

    async with async_session() as db:
        if cameras:
            stmt = pg_insert(Camera).values(list(cameras.values()))
            stmt = stmt.on_conflict_do_nothing(index_elements=[Camera.id])
            await db.execute(stmt)
        if observations:
            stmt = pg_insert(Observation).values(observations)
            stmt = stmt.on_conflict_do_nothing(index_elements=[Observation.id])
            await db.execute(stmt)
        await db.commit()

    logger.info("seeded %d cameras + %d observations", len(cameras), len(observations))
    print(f"seeded {len(cameras)} cameras + {len(observations)} observations")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
