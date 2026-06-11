"""
Digest scheduler service.

Runs as a long-lived async task that automatically generates
digests for cameras on their configured schedule. Checks every
60 seconds whether any camera is due for a new digest, then
calls generate_digest() and stores the result as a DigestEntry.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.search.digest import PERIOD_DELTAS, generate_digest
from services.search.embeddings import get_embedding_provider
from shared.database import async_session
from shared.models import Camera, DigestEntry, Provider

logger = logging.getLogger("nurby.search.scheduler")

CHECK_INTERVAL_SECONDS = 60


def _parse_period(period: str) -> timedelta:
    """Convert a period string like '1h', '24h', '7d' to a timedelta."""
    delta = PERIOD_DELTAS.get(period)
    if delta:
        return delta
    # Fallback. Try parsing manually.
    if period.endswith("h"):
        try:
            return timedelta(hours=int(period[:-1]))
        except ValueError:
            pass
    elif period.endswith("d"):
        try:
            return timedelta(days=int(period[:-1]))
        except ValueError:
            pass
    return timedelta(days=1)


class DigestScheduler:
    """Background service that generates digests on each camera's schedule."""

    def __init__(self):
        self._last_digest_times: dict[uuid.UUID, datetime] = {}
        self._running = False

    async def _load_last_digest_times(self, db: AsyncSession) -> None:
        """Load the most recent digest time for each camera from the database."""
        from sqlalchemy import func as sa_func

        stmt = (
            select(
                DigestEntry.camera_id,
                sa_func.max(DigestEntry.generated_at).label("last_generated"),
            )
            .where(DigestEntry.camera_id.isnot(None))
            .group_by(DigestEntry.camera_id)
        )
        result = await db.execute(stmt)
        for row in result.all():
            camera_id = row[0]
            last_generated = row[1]
            if camera_id and last_generated:
                if last_generated.tzinfo is None:
                    last_generated = last_generated.replace(tzinfo=timezone.utc)
                self._last_digest_times[camera_id] = last_generated

        logger.info(
            "Loaded last digest times for %d cameras", len(self._last_digest_times)
        )

    async def _get_provider_for_camera(
        self, db: AsyncSession, camera: Camera
    ) -> Provider | None:
        """Resolve the VLM provider to use for digest generation."""
        provider = None
        if camera.digest_provider_id:
            provider = await db.get(Provider, camera.digest_provider_id)
        if not provider:
            provider = await get_embedding_provider()
        return provider

    async def _generate_and_store(
        self, db: AsyncSession, camera: Camera
    ) -> DigestEntry | None:
        """Generate a digest for a camera and persist it as a DigestEntry."""
        try:
            provider = await self._get_provider_for_camera(db, camera)

            digest_data = await generate_digest(
                db,
                period=camera.digest_period,
                camera_id=camera.id,
                provider=provider,
                custom_prompt=camera.digest_prompt,
            )

            entry = DigestEntry(
                camera_id=camera.id,
                period=camera.digest_period,
                summary=digest_data.get("summary", ""),
                highlights=digest_data.get("highlights", []),
                stats=digest_data.get("stats", {}),
                total_observations=digest_data.get("total_observations", 0),
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)

            self._last_digest_times[camera.id] = entry.generated_at

            logger.info(
                "Generated %s digest for camera '%s' (%s). %d observations",
                camera.digest_period,
                camera.name,
                camera.id,
                entry.total_observations,
            )
            return entry

        except Exception:
            logger.exception(
                "Failed to generate digest for camera '%s' (%s)",
                camera.name,
                camera.id,
            )
            await db.rollback()
            return None

    async def _check_and_generate(self) -> int:
        """Check all cameras and generate digests for those that are due.

        Returns the number of digests generated.
        """
        generated_count = 0
        now = datetime.now(timezone.utc)

        async with async_session() as db:
            result = await db.execute(
                select(Camera).where(Camera.digest_enabled.is_(True))
            )
            cameras = list(result.scalars().all())

            for camera in cameras:
                period_delta = _parse_period(camera.digest_period)
                last_time = self._last_digest_times.get(camera.id)

                if last_time is None:
                    # No previous digest recorded. Generate one now.
                    is_due = True
                else:
                    next_due = last_time + period_delta
                    is_due = now >= next_due

                if is_due:
                    entry = await self._generate_and_store(db, camera)
                    if entry:
                        generated_count += 1

        return generated_count

    async def run(self) -> None:
        """Main loop. Runs continuously, checking every 60 seconds."""
        self._running = True
        logger.info("Digest scheduler starting")

        # Load existing digest history so we know when each camera last ran
        async with async_session() as db:
            await self._load_last_digest_times(db)

        logger.info("Digest scheduler running. Checking every %ds", CHECK_INTERVAL_SECONDS)

        while self._running:
            try:
                count = await self._check_and_generate()
                if count > 0:
                    logger.info("Generated %d digest(s) this cycle", count)
            except Exception:
                logger.exception("Error in digest scheduler cycle")

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Signal the scheduler to stop after the current cycle."""
        self._running = False
        logger.info("Digest scheduler stopping")
