"""
Recording retention cleanup.

Periodically checks each camera's retention policy and deletes
old recordings that exceed time or size limits.
"""

import asyncio
import logging
import os

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, and_

from shared.config import settings
from shared.database import async_session
from shared.models import AudioCapture, Camera, Recording, Transcript

logger = logging.getLogger("nurby.ingestion.retention")

CLEANUP_INTERVAL = 3600  # run every hour

_RELATIVE_PREFIXES = ["./recordings/", "recordings/", "./"]


def _resolve_path(file_path: str | None) -> str | None:
    """Turn a stored (possibly relative) file path into an absolute disk path."""
    if not file_path:
        return None
    if os.path.isabs(file_path):
        return file_path
    rel = file_path
    for prefix in _RELATIVE_PREFIXES:
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    return os.path.join(os.path.abspath(settings.recordings_path), rel)


def _remove_file(path: str | None) -> tuple[int, bool]:
    """Remove a file from disk. Returns (size_freed, success)."""
    if not path or not os.path.exists(path):
        return 0, True  # nothing to delete is fine
    try:
        size = os.path.getsize(path)
        os.remove(path)
        return size, True
    except OSError:
        logger.warning("Could not delete file %s", path)
        return 0, False


class RetentionManager:
    async def run(self):
        """Periodically enforce retention policies for all cameras."""
        while True:
            try:
                await self._enforce_all()
            except Exception:
                logger.exception("Retention cleanup failed")
            await asyncio.sleep(CLEANUP_INTERVAL)

    async def _enforce_all(self):
        async with async_session() as db:
            # Recordings retention only fires for cameras with a non-off
            # policy. Audio + transcript retention runs on every camera
            # because the columns always have a meaningful default and
            # the user can lower them per camera.
            all_cams = list((await db.execute(select(Camera))).scalars().all())

        rec_cams = [c for c in all_cams if (c.retention_mode or "none") != "none"]
        if rec_cams:
            logger.info(
                "Running recording retention cleanup for %d cameras", len(rec_cams)
            )
            for cam in rec_cams:
                try:
                    if cam.retention_mode == "time":
                        await self._enforce_time(cam, cam.retention_days)
                    elif cam.retention_mode == "size":
                        await self._enforce_size(cam, cam.retention_gb)
                except Exception:
                    logger.exception(
                        "Recording retention failed for camera %s", cam.id
                    )

        # Audio + transcript retention. always time-based.
        for cam in all_cams:
            try:
                await self._enforce_audio_retention(cam)
            except Exception:
                logger.exception(
                    "Audio retention failed for camera %s", cam.id
                )
            try:
                await self._enforce_transcript_retention(cam)
            except Exception:
                logger.exception(
                    "Transcript retention failed for camera %s", cam.id
                )

    async def _enforce_audio_retention(self, camera: Camera) -> None:
        """Delete AudioCapture rows + opus blobs older than the camera's
        ``audio_retention_days``.

        Transcripts may keep a foreign key to the deleted capture.
        ``ondelete=SET NULL`` on the FK takes care of the column. The
        transcript text survives independently and falls under the
        transcript retention window.
        """
        days = int(getattr(camera, "audio_retention_days", 0) or 0)
        if days <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with async_session() as db:
            rows = list(
                (
                    await db.execute(
                        select(AudioCapture)
                        .where(AudioCapture.camera_id == camera.id)
                        .where(AudioCapture.started_at < cutoff)
                    )
                ).scalars().all()
            )
            if not rows:
                return
            freed_bytes = 0
            deleted = 0
            for cap in rows:
                _, ok = _remove_file(cap.file_path)
                if not ok:
                    continue
                freed_bytes += int(cap.size_bytes or 0)
                await db.delete(cap)
                deleted += 1
            await db.commit()
            if deleted:
                logger.info(
                    "Audio retention for camera %s. deleted %d captures, freed %.2f MB (cutoff %s, %d days)",
                    camera.name or camera.id,
                    deleted,
                    freed_bytes / (1024 ** 2),
                    cutoff.isoformat(),
                    days,
                )

    async def _enforce_transcript_retention(self, camera: Camera) -> None:
        days = int(getattr(camera, "transcript_retention_days", 0) or 0)
        if days <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with async_session() as db:
            rows = list(
                (
                    await db.execute(
                        select(Transcript)
                        .where(Transcript.camera_id == camera.id)
                        .where(Transcript.started_at < cutoff)
                    )
                ).scalars().all()
            )
            if not rows:
                return
            for tx in rows:
                await db.delete(tx)
            await db.commit()
            logger.info(
                "Transcript retention for camera %s. deleted %d rows (cutoff %s, %d days)",
                camera.name or camera.id, len(rows), cutoff.isoformat(), days,
            )

    async def _enforce_time(self, camera: Camera, retention_days: int):
        """Delete recordings older than retention_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        reason = f"retention_time: older than {retention_days} days"

        async with async_session() as db:
            result = await db.execute(
                select(Recording).where(
                    and_(
                        Recording.camera_id == camera.id,
                        Recording.started_at < cutoff,
                    )
                )
            )
            old_recordings = list(result.scalars().all())

            if not old_recordings:
                return

            deleted_count = 0
            freed_bytes = 0

            for rec in old_recordings:
                abs_path = _resolve_path(rec.file_path)
                size, ok = _remove_file(abs_path)
                if not ok:
                    logger.warning("Skipping DB delete for recording %s, file still on disk", rec.id)
                    continue
                freed_bytes += size
                _remove_file(_resolve_path(rec.thumbnail_path))

                logger.info(
                    "Deleting recording for camera %s, file %s, reason %s",
                    camera.name or camera.id, abs_path or rec.file_path, reason,
                )

                await db.delete(rec)
                deleted_count += 1

            await db.commit()

            freed_gb = freed_bytes / (1024 ** 3)
            logger.info(
                "Time retention for camera %s. deleted %d recordings, freed %.2f GB (cutoff %s)",
                camera.name or camera.id, deleted_count, freed_gb, cutoff.isoformat(),
            )

    async def _enforce_size(self, camera: Camera, max_gb: float):
        """Delete oldest recordings until total size is under max_gb."""
        max_bytes = int(max_gb * 1024 ** 3)
        reason = f"retention_size: exceeded {max_gb:.1f} GB limit"

        async with async_session() as db:
            total_result = await db.execute(
                select(func.coalesce(func.sum(Recording.file_size_bytes), 0)).where(
                    Recording.camera_id == camera.id
                )
            )
            total_bytes = total_result.scalar()

            if total_bytes <= max_bytes:
                return

            excess = total_bytes - max_bytes
            logger.info(
                "Size retention for camera %s. %.2f GB used, limit %.2f GB, need to free %.2f GB",
                camera.name or camera.id,
                total_bytes / (1024 ** 3),
                max_gb,
                excess / (1024 ** 3),
            )

            result = await db.execute(
                select(Recording)
                .where(Recording.camera_id == camera.id)
                .order_by(Recording.started_at.asc())
            )
            recordings = list(result.scalars().all())

            deleted_count = 0
            freed_bytes = 0

            for rec in recordings:
                if freed_bytes >= excess:
                    break

                rec_size = rec.file_size_bytes or 0
                abs_path = _resolve_path(rec.file_path)
                size, ok = _remove_file(abs_path)
                if not ok:
                    logger.warning("Skipping DB delete for recording %s, file still on disk", rec.id)
                    continue
                _remove_file(_resolve_path(rec.thumbnail_path))

                logger.info(
                    "Deleting recording for camera %s, file %s, reason %s",
                    camera.name or camera.id, abs_path or rec.file_path, reason,
                )

                await db.delete(rec)
                freed_bytes += size or rec_size
                deleted_count += 1

            await db.commit()

            logger.info(
                "Size retention for camera %s. deleted %d recordings, freed %.2f GB",
                camera.name or camera.id, deleted_count, freed_bytes / (1024 ** 3),
            )
