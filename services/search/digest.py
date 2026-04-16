"""
Digest summary generator.

Produces hourly and daily summaries of observation activity
across all cameras. Uses VLM for natural language summaries
when available, falls back to structured statistics.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Observation, Camera
from services.search.query import _call_text_llm

logger = logging.getLogger("nurby.search.digest")


async def generate_digest(
    db: AsyncSession,
    period: str = "daily",
    camera_id: uuid.UUID | None = None,
    target_time: datetime | None = None,
    provider=None,
) -> dict:
    """Generate a summary digest for the given period.

    period: "hourly" or "daily"
    camera_id: optional filter to single camera
    target_time: end of period (defaults to now)
    provider: VLM provider for natural language summary
    """
    now = target_time or datetime.now(timezone.utc)

    if period == "hourly":
        start = now - timedelta(hours=1)
        period_label = f"{start.strftime('%H:%M')} to {now.strftime('%H:%M')}"
    else:
        start = now - timedelta(days=1)
        period_label = f"{start.strftime('%b %d')} to {now.strftime('%b %d')}"

    # Fetch observations in period
    filters = [
        Observation.started_at >= start,
        Observation.started_at <= now,
    ]
    if camera_id:
        filters.append(Observation.camera_id == camera_id)

    result = await db.execute(
        select(Observation)
        .where(and_(*filters))
        .order_by(Observation.started_at)
    )
    observations = list(result.scalars().all())

    if not observations:
        return {
            "period": period,
            "period_label": period_label,
            "start": start.isoformat(),
            "end": now.isoformat(),
            "total_observations": 0,
            "summary": "No observations recorded during this period.",
            "stats": {},
            "highlights": [],
        }

    # Build statistics
    camera_ids = {obs.camera_id for obs in observations}
    cam_result = await db.execute(select(Camera).where(Camera.id.in_(camera_ids)))
    camera_map = {c.id: c.name for c in cam_result.scalars().all()}

    # Object counts
    object_counts: dict[str, int] = {}
    person_counts: dict[str, int] = {}
    camera_activity: dict[str, int] = {}

    for obs in observations:
        cam_name = camera_map.get(obs.camera_id, "Unknown")
        camera_activity[cam_name] = camera_activity.get(cam_name, 0) + 1

        if obs.object_detections:
            for obj in obs.object_detections.get("objects", []):
                label = obj.get("label", "unknown")
                object_counts[label] = object_counts.get(label, 0) + 1

        if obs.person_detections:
            for face in obs.person_detections.get("faces", []):
                name = face.get("person_name") or "Unknown person"
                person_counts[name] = person_counts.get(name, 0) + 1

    # Top objects and people
    top_objects = sorted(object_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_people = sorted(person_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    busiest_camera = max(camera_activity.items(), key=lambda x: x[1]) if camera_activity else None

    # Build highlights
    highlights = []
    vlm_descriptions = [
        obs.vlm_description for obs in observations if obs.vlm_description
    ]

    if top_objects:
        obj_parts = [f"{count}x {label}" for label, count in top_objects]
        highlights.append(f"Most detected objects. {', '.join(obj_parts)}")

    if top_people:
        ppl_parts = [f"{name} ({count}x)" for name, count in top_people]
        highlights.append(f"People seen. {', '.join(ppl_parts)}")

    if busiest_camera:
        highlights.append(f"Busiest camera. {busiest_camera[0]} ({busiest_camera[1]} observations)")

    stats = {
        "total_observations": len(observations),
        "cameras_active": len(camera_activity),
        "unique_objects": len(object_counts),
        "unique_people": len(person_counts),
        "vlm_descriptions": len(vlm_descriptions),
        "top_objects": top_objects,
        "top_people": top_people,
        "camera_activity": dict(sorted(camera_activity.items(), key=lambda x: x[1], reverse=True)),
    }

    # Generate natural language summary with VLM if available
    summary_text = None
    if provider and vlm_descriptions:
        try:
            context = "\n".join([
                f"- {d}" for d in vlm_descriptions[:20]
            ])
            system_prompt = (
                "You are Nurby, an AI camera monitoring assistant. "
                "Summarize the following camera observations into a brief digest. "
                "Be concise (2-4 sentences). Mention key activity, people, and patterns."
            )
            user_prompt = (
                f"Period. {period_label}\n"
                f"Total observations. {len(observations)}\n"
                f"Cameras. {', '.join(camera_activity.keys())}\n"
                f"Top objects. {', '.join(f'{l} ({c}x)' for l, c in top_objects)}\n"
                f"People. {', '.join(f'{n} ({c}x)' for n, c in top_people) if top_people else 'none identified'}\n\n"
                f"Scene descriptions from cameras.\n{context}\n\n"
                f"Write a brief summary digest."
            )
            summary_text = await _call_text_llm(provider, system_prompt, user_prompt)
        except Exception:
            logger.exception("VLM digest summary failed")

    if not summary_text:
        # Fallback structured summary
        parts = [f"{len(observations)} observations across {len(camera_activity)} camera{'s' if len(camera_activity) != 1 else ''}."]
        if top_objects:
            parts.append(f"Top detections. {', '.join(f'{l} ({c}x)' for l, c in top_objects[:3])}.")
        if top_people:
            parts.append(f"People identified. {', '.join(n for n, _ in top_people[:3])}.")
        summary_text = " ".join(parts)

    return {
        "period": period,
        "period_label": period_label,
        "start": start.isoformat(),
        "end": now.isoformat(),
        "total_observations": len(observations),
        "summary": summary_text,
        "stats": stats,
        "highlights": highlights,
    }
