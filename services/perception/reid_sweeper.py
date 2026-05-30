"""
Body re-identification housekeeping.

Two scheduled jobs.

1. Tentative decay. Body clusters with no face co-verification and no
   activity for N days get marked status='ignored' so they drop out of
   the suggestion list and the cluster search. This is the durable
   answer to "we never confirmed who that was; stop tracking them."

2. Body+face overlap fusion. When a body cluster and a face cluster
   appear together on the same observation window and share a Journey
   slot, the body cluster inherits the face cluster's Person link. This
   closes the loop on "saw their face for the first time today, all
   the body-only sightings from the past hour should retroactively
   collapse to that Person."

Both jobs are cheap enough to run alongside the daily digest scheduler.
Tick every `body_reid_fusion_interval_seconds` (default 5 min).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.app_settings import get_setting
from shared.database import async_session
from shared.models import BodyCluster, FaceCluster, Observation

logger = logging.getLogger("nurby.perception.reid_sweeper")


class BodyReIDSweeper:
    """Periodic decay + face-overlap fusion for body clusters."""

    def __init__(self) -> None:
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("body re-id sweeper started")
        try:
            while not self._stopping.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("body re-id sweeper tick failed")
                interval = int(await get_setting(
                    "body_reid_fusion_interval_seconds", 300,
                ))
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=max(60, interval),
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("body re-id sweeper stopped")

    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        async with async_session() as db:
            await self._decay_tentative(db)
            await self._fuse_face_overlaps(db)
            await db.commit()
        # Phase 4. Prompt the household to name new clusters that
        # crossed the sightings threshold. Wrapped in a try so a
        # Telegram outage never breaks the perception sweep.
        try:
            await self._prompt_cluster_naming()
        except Exception:
            logger.exception("cluster naming prompts failed")

    async def _prompt_cluster_naming(self) -> None:
        """Send a Telegram naming prompt for any face or body cluster
        that just crossed the configured sighting threshold.

        Idempotent. ``naming_prompted_at`` is stamped on first send so
        we never re-prompt the same cluster. Requires at least one
        ``shared_with_household=true`` or paired channel; the
        initiator module no-ops otherwise so a deployment without
        Telegram doesn't trip on this code path.
        """
        from services.notify.cluster_naming_telegram import (
            request_body_cluster_naming,
            request_face_cluster_naming,
        )

        # Threshold knobs share the existing app_settings table so an
        # ops user can flip them without a redeploy.
        min_sightings = int(await get_setting("cluster_naming_min_sightings", 3))
        if min_sightings <= 0:
            return

        async with async_session() as db:
            face_rows = (
                await db.execute(
                    select(FaceCluster.id)
                    .where(FaceCluster.status == "pending")
                    .where(FaceCluster.sighting_count >= min_sightings)
                    .where(FaceCluster.naming_prompted_at.is_(None))
                    .limit(5)
                )
            ).all()
            body_rows = (
                await db.execute(
                    select(BodyCluster.id)
                    .where(BodyCluster.status == "pending")
                    .where(BodyCluster.person_id.is_(None))
                    .where(BodyCluster.sighting_count >= min_sightings)
                    .where(BodyCluster.naming_prompted_at.is_(None))
                    .limit(5)
                )
            ).all()

        for (fc_id,) in face_rows:
            try:
                await request_face_cluster_naming(fc_id)
            except Exception:
                logger.exception(
                    "request_face_cluster_naming failed cluster=%s", fc_id,
                )
        for (bc_id,) in body_rows:
            try:
                await request_body_cluster_naming(bc_id)
            except Exception:
                logger.exception(
                    "request_body_cluster_naming failed cluster=%s", bc_id,
                )

    async def _decay_tentative(self, db: AsyncSession) -> None:
        """Mark long-stale tentative clusters as ignored."""
        days = int(await get_setting("body_reid_tentative_decay_days", 14))
        if days <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await db.execute(
            update(BodyCluster)
            .where(BodyCluster.status == "pending")
            .where(BodyCluster.confidence == "tentative")
            .where(BodyCluster.person_id.is_(None))
            .where(BodyCluster.last_seen_at < cutoff)
            .values(status="ignored")
            .returning(BodyCluster.id)
        )
        ids = list(result.scalars().all())
        if ids:
            logger.info(
                "body re-id decay. ignored %d tentative cluster(s) idle > %dd",
                len(ids), days,
            )

    async def _fuse_face_overlaps(self, db: AsyncSession) -> None:
        """Promote tentative body clusters when they co-occur with a
        named face cluster on the same observation.

        Walks recent observations carrying both `faces` and `bodies`
        in `person_detections`. For each such observation, if any face
        in the payload maps to a Person and any body cluster is still
        tentative, link the body cluster to that Person and flip its
        confidence to confirmed.
        """
        # Look back over the last hour. Cheaper than scanning forever.
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        rows = (
            await db.execute(
                select(Observation.id, Observation.person_detections)
                .where(Observation.started_at >= since)
                .where(Observation.person_detections.is_not(None))
            )
        ).all()

        promoted = 0
        for obs_id, pd in rows:
            if not isinstance(pd, dict):
                continue
            faces = pd.get("faces") or []
            bodies = pd.get("bodies") or []
            if not faces or not bodies:
                continue

            # Pull face cluster IDs whose face cluster is linked to a Person.
            face_cluster_ids = []
            for f in faces:
                cid = f.get("cluster_id")
                if cid:
                    try:
                        face_cluster_ids.append(uuid_from(cid))
                    except Exception:
                        pass
            if not face_cluster_ids:
                continue
            fc_rows = (
                await db.execute(
                    select(FaceCluster.id, FaceCluster.person_id)
                    .where(FaceCluster.id.in_(face_cluster_ids))
                    .where(FaceCluster.person_id.is_not(None))
                )
            ).all()
            if not fc_rows:
                continue
            # Pick the first named face cluster. Multi-person frames
            # produce ambiguous links; we conservatively skip body
            # promotion when more than one named face is present.
            if len({r.person_id for r in fc_rows}) > 1:
                continue
            person_id = fc_rows[0].person_id
            face_cluster_id = fc_rows[0].id

            for b in bodies:
                bc_id = b.get("body_cluster_id")
                if not bc_id:
                    continue
                try:
                    bc_uuid = uuid_from(bc_id)
                except Exception:
                    continue
                cluster = await db.get(BodyCluster, bc_uuid)
                if cluster is None:
                    continue
                if cluster.person_id is not None and cluster.person_id == person_id:
                    continue
                if cluster.confidence == "confirmed" and cluster.person_id is not None:
                    continue
                cluster.person_id = person_id
                cluster.linked_face_cluster_id = face_cluster_id
                cluster.confidence = "confirmed"
                promoted += 1
        if promoted:
            logger.info(
                "body re-id fusion. promoted %d body cluster(s) via face overlap",
                promoted,
            )


def uuid_from(value):
    import uuid as _uuid
    if isinstance(value, _uuid.UUID):
        return value
    return _uuid.UUID(str(value))
