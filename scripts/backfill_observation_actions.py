"""Backfill the observation_actions table from existing observation captions.

The structured action pass only runs on new observations, so the table starts
empty and the new wellbeing API/MCP tools have nothing historical to read. This
script derives a coarse action for past observations WITHOUT re-running the VLM:
it maps each observation's existing prose caption to one action via keyword cues
(services.perception.actions.coarse_action_from_caption) and writes one row per
recognised dependant in that frame.

These backfilled rows are deliberately lower-fidelity than live ones: confidence
is left null and posture is null, because they came from text, not a fresh crop.
They are good enough to answer "did Mum eat lunch last week" from history while
the live classifier accumulates higher-quality rows going forward.

Idempotent. Observations that already have any observation_actions row are
skipped, so re-running never duplicates. Scoped to observations that (a) have a
caption and (b) have at least one recognised dependant face, which is exactly the
set the live pass would have classified.

Run inside the api container:
    python -m scripts.backfill_observation_actions [--days N] [--commit]
Without --commit it is a dry run that only reports counts.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from services.perception import actions as actions_mod
from shared.database import async_session
from shared.models import Observation, ObservationAction


async def _existing_observation_ids(db) -> set:
    rows = (await db.execute(select(ObservationAction.observation_id))).scalars().all()
    return set(rows)


async def run(days: int | None, commit: bool) -> dict:
    scanned = 0
    eligible = 0
    inserted = 0
    skipped_existing = 0
    by_action: dict[str, int] = {}

    async with async_session() as db:
        already = await _existing_observation_ids(db)

        q = select(Observation).where(Observation.vlm_description.isnot(None))
        if days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.where(Observation.started_at >= cutoff)
        q = q.order_by(Observation.started_at.asc())

        rows = (await db.execute(q)).scalars().all()
        for obs in rows:
            scanned += 1
            if obs.id in already:
                skipped_existing += 1
                continue
            deps = actions_mod.dependant_faces(obs.person_detections)
            if not deps:
                continue
            action = actions_mod.coarse_action_from_caption(obs.vlm_description)
            if action is None or action == "unknown":
                continue
            eligible += 1
            for pid, name, _bbox in deps:
                inserted += 1
                by_action[action] = by_action.get(action, 0) + 1
                if commit:
                    db.add(
                        ObservationAction(
                            observation_id=obs.id,
                            camera_id=obs.camera_id,
                            person_id=_safe_uuid(pid),
                            person_name=name,
                            action=action,
                            posture=None,
                            confidence=None,  # text-derived, not a fresh crop
                            # The existing caption is an open description of the
                            # scene; carry it as detail so backfilled rows still
                            # have the open-world layer, capped to the column's
                            # practical size.
                            detail=(obs.vlm_description or "")[:240] or None,
                            observed_at=obs.started_at,
                        )
                    )
        if commit:
            await db.commit()

    return {
        "scanned": scanned,
        "eligible_observations": eligible,
        "rows_inserted": inserted,
        "skipped_already_had_rows": skipped_existing,
        "by_action": by_action,
        "committed": commit,
    }


def _safe_uuid(v):
    import uuid

    if v is None or isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=None, help="Only backfill the last N days.")
    ap.add_argument("--commit", action="store_true", help="Write rows. Omit for a dry run.")
    args = ap.parse_args()

    result = asyncio.run(run(args.days, args.commit))
    mode = "COMMIT" if result["committed"] else "DRY RUN"
    print(f"[{mode}] observation_actions backfill")
    print(f"  observations scanned        : {result['scanned']}")
    print(f"  skipped (already had rows)  : {result['skipped_already_had_rows']}")
    print(f"  eligible observations       : {result['eligible_observations']}")
    print(f"  rows to insert              : {result['rows_inserted']}")
    print(f"  by action                   : {result['by_action']}")
    if not result["committed"]:
        print("  (dry run. re-run with --commit to write.)")


if __name__ == "__main__":
    main()
