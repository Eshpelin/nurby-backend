"""Tier A speaker attribution. Video-correlated.

For a transcript spanning ``[t0, t1]`` on camera C, we look at the
overlapping observations on the same camera and count how long each
named person was visible inside the segment window. If exactly one
named person hits at least the 60% coverage bar, the transcript is
attributed to them. Otherwise the segment is marked ambiguous.

Pure DB read. Safe to call from the write path before committing the
transcript row. No I/O outside one SELECT.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Observation

logger = logging.getLogger("nurby.perception.audio.speaker")

# Plan §7.7.3. ≥60% coverage of a single named person attributes the
# segment by face. Tunable. Higher reduces false positives at the cost
# of more 'ambiguous' rows.
SPEAKER_VIDEO_COVERAGE_MIN = 0.6


@dataclass(slots=True)
class SpeakerAttribution:
    person_id: uuid.UUID | None
    confidence: float | None
    source: str  # 'video' | 'ambiguous'


async def attribute_by_video(
    db: AsyncSession,
    camera_id: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
) -> SpeakerAttribution:
    """Return the person who dominated the camera frame during this
    segment, if any. ``ambiguous`` when zero or multiple candidates
    cross the coverage threshold."""
    duration = max((ended_at - started_at).total_seconds(), 0.001)

    q = (
        select(Observation)
        .where(
            and_(
                Observation.camera_id == camera_id,
                Observation.started_at <= ended_at,
                # ended_at can be NULL while the obs is still open. Treat
                # those as ongoing so they can still anchor an attribution.
                (Observation.ended_at.is_(None))
                | (Observation.ended_at >= started_at),
            )
        )
        .order_by(Observation.started_at.asc())
    )
    rows = (await db.execute(q)).scalars().all()

    coverage: dict[uuid.UUID, float] = {}
    name_lookup: dict[uuid.UUID, str] = {}
    for obs in rows:
        faces = (obs.person_detections or {}).get("faces") or []
        named = _named_persons(faces)
        if not named:
            continue
        # Overlap window between segment and observation.
        obs_end = obs.ended_at or ended_at
        a = max(started_at, obs.started_at)
        b = min(ended_at, obs_end)
        overlap = max((b - a).total_seconds(), 0.0)
        if overlap <= 0:
            continue
        for person_id, person_name in named:
            coverage[person_id] = coverage.get(person_id, 0.0) + overlap
            name_lookup[person_id] = person_name

    if not coverage:
        return SpeakerAttribution(None, None, "ambiguous")

    # Normalize to fraction of segment duration.
    ratios = {pid: cov / duration for pid, cov in coverage.items()}
    qualifiers = [
        (pid, ratio) for pid, ratio in ratios.items() if ratio >= SPEAKER_VIDEO_COVERAGE_MIN
    ]
    if len(qualifiers) != 1:
        return SpeakerAttribution(None, None, "ambiguous")

    person_id, ratio = qualifiers[0]
    return SpeakerAttribution(person_id, min(ratio, 1.0), "video")


def _named_persons(faces: Iterable[dict]) -> list[tuple[uuid.UUID, str]]:
    """Extract (person_id, name) tuples for faces matched to a person.

    Unmatched (cluster-only) faces are ignored. Phase 1 stays
    name-attribution only. Cluster-anchored attribution is a Phase 3
    add-on once voice samples disambiguate them.
    """
    out: list[tuple[uuid.UUID, str]] = []
    seen: set[uuid.UUID] = set()
    for f in faces:
        pid = f.get("person_id")
        name = f.get("person_name")
        if not pid or not name:
            continue
        try:
            pid_uuid = uuid.UUID(pid) if isinstance(pid, str) else pid
        except (ValueError, AttributeError):
            continue
        if pid_uuid in seen:
            continue
        seen.add(pid_uuid)
        out.append((pid_uuid, name))
    return out
