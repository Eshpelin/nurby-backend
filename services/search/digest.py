"""
Digest summary generator.

Produces hourly and daily summaries of observation activity
across all cameras. Uses VLM for natural language summaries
when available, falls back to structured statistics.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.search.query import _call_text_llm
from shared.models import Camera, FaceCluster, FaceClusterSample, Observation, Person

logger = logging.getLogger("nurby.search.digest")


PERIOD_DELTAS = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(days=1),
    "48h": timedelta(days=2),
    "7d": timedelta(days=7),
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
}

DEFAULT_DIGEST_PROMPT = (
    "You are Nurby, a home monitoring assistant. Your job is to turn a "
    "list of camera observations into a short human-friendly story about "
    "what actually happened in the home. "
    "Write in plain English, present tense, 3 to 6 sentences. "
    "Focus on who was seen (name them when named, call them 'an unknown "
    "person' otherwise, and keep separate unknowns separate), when they "
    "were there, which camera, and anything notable they were carrying "
    "or doing. Call out anything that could indicate danger (knife, gun, "
    "fire, glass break, weapon, injury) up front. "
    "Do not quote raw counts like '651 times'. Do not list statistics. "
    "Do not mention observation IDs. Do not use em-dashes, ellipses, or "
    "colons before lists. If nothing meaningful happened, say so in one "
    "sentence."
)


def _format_timestamp(ts: datetime) -> str:
    """Short human time like '6:32 pm'."""
    # %-I works on macOS/Linux, %#I on Windows. Use lstrip('0') to be safe.
    return ts.strftime("%I:%M %p").lstrip("0").lower()


SAFETY_LABELS = {"knife", "gun", "fire", "weapon"}


async def generate_digest(
    db: AsyncSession,
    period: str = "daily",
    camera_id: uuid.UUID | None = None,
    target_time: datetime | None = None,
    provider=None,
    custom_prompt: str | None = None,
) -> dict:
    """Generate a summary digest for the given period.

    period: "hourly", "daily", "1h", "6h", "12h", "24h", "48h", "7d"
    camera_id: optional filter to single camera
    target_time: end of period (defaults to now)
    provider: VLM provider for natural language summary
    custom_prompt: override system prompt for digest generation
    """
    now = target_time or datetime.now(timezone.utc)

    delta = PERIOD_DELTAS.get(period, timedelta(days=1))
    start = now - delta

    if delta <= timedelta(hours=1):
        period_label = f"{start.strftime('%H:%M')} to {now.strftime('%H:%M')}"
    elif delta <= timedelta(days=1):
        period_label = f"{start.strftime('%b %d %H:%M')} to {now.strftime('%H:%M')}"
    else:
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

    # Resolve cluster_id -> named-person display_name so historic
    # observations captured before the user named the cluster still
    # show up under the person's real name in the digest.
    # Household nicknames are shown in place of canonical names. Live
    # detections store the canonical name; map it through here too.
    alias_rows = (
        await db.execute(select(Person.display_name, Person.nickname))
    ).all()
    name_alias = {
        dn: nk.strip()
        for dn, nk in alias_rows
        if dn and isinstance(nk, str) and nk.strip()
    }

    cluster_name_map: dict[str, str] = {}
    cluster_ids_seen: set[str] = set()
    for obs in observations:
        for f in (obs.person_detections or {}).get("faces", []):
            cid = f.get("cluster_id")
            if cid:
                cluster_ids_seen.add(str(cid))
    if cluster_ids_seen:
        try:
            rows = await db.execute(
                select(FaceCluster.id, Person.display_name, Person.nickname)
                .join(Person, FaceCluster.person_id == Person.id)
                .where(FaceCluster.id.in_([uuid.UUID(c) for c in cluster_ids_seen]))
            )
            for cid, name, nick in rows.all():
                cluster_name_map[str(cid)] = (
                    nick.strip() if isinstance(nick, str) and nick.strip() else name
                )
        except Exception:
            logger.exception("Failed to resolve cluster -> person names")

    def _resolve_face_name(face: dict) -> str | None:
        """Prefer live match, then the cluster's named owner if any.
        Canonical names are rewritten to the household nickname."""
        name = face.get("person_name")
        if name:
            return name_alias.get(name, name)
        cid = face.get("cluster_id")
        if cid:
            return cluster_name_map.get(str(cid))
        return None

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
                name = _resolve_face_name(face) or "Unknown person"
                person_counts[name] = person_counts.get(name, 0) + 1

    # Top objects and people
    top_objects = sorted(object_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_people = sorted(person_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    busiest_camera = max(camera_activity.items(), key=lambda x: x[1]) if camera_activity else None

    vlm_descriptions = [
        obs.vlm_description for obs in observations if obs.vlm_description
    ]
    # Highlights are built later from narrative roll-ups (safety flags,
    # named visits, unknown appearance hints). Raw stat pills like
    # "Most detected objects. 1x person" are intentionally dropped.
    highlights: list[str] = []

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

    # Build a per-observation narrative feed for the LLM. We care about
    # when, where, who (named vs unknown), what VLM saw, and any safety
    # label. Raw counts are not included because they add noise.
    narrative_lines: list[str] = []
    safety_hits: list[str] = []
    named_sessions: dict[str, list[datetime]] = {}
    unknown_sessions: list[datetime] = []

    for obs in observations:
        cam_name = camera_map.get(obs.camera_id, "Unknown camera")
        t_start = _format_timestamp(obs.started_at)
        t_end = _format_timestamp(obs.ended_at) if obs.ended_at else None
        when = f"{t_start} to {t_end}" if t_end and t_end != t_start else t_start

        labels = [
            (o.get("label") or "").strip()
            for o in (obs.object_detections or {}).get("objects", [])
        ]
        labels = [l for l in labels if l]
        label_set = set(labels)

        resolved_names = [
            _resolve_face_name(f)
            for f in (obs.person_detections or {}).get("faces", [])
        ]
        named_here = sorted({n for n in resolved_names if n})
        unknown_here = sum(1 for n in resolved_names if not n)

        for name in named_here:
            named_sessions.setdefault(name, []).append(obs.started_at)
        if unknown_here and not named_here:
            unknown_sessions.append(obs.started_at)

        safety = sorted(label_set & SAFETY_LABELS)
        if safety:
            safety_hits.append(
                f"{when} on {cam_name}. Safety flag. {', '.join(safety)}."
            )

        who_parts: list[str] = []
        if named_here:
            who_parts.append(", ".join(named_here))
        if unknown_here:
            who_parts.append(
                "1 unknown person" if unknown_here == 1
                else f"{unknown_here} unknown people"
            )
        who = " and ".join(who_parts) if who_parts else None

        notable_objects = [
            l for l in label_set
            if l not in {"person"} and l in {
                "knife", "gun", "fire", "weapon", "cell phone", "laptop",
                "backpack", "handbag", "suitcase", "umbrella", "bottle",
                "wine glass", "dog", "cat", "car", "truck", "motorcycle",
                "bicycle", "bus", "package",
            }
        ]

        line = f"{when} on {cam_name}. "
        if who:
            line += f"Seen. {who}. "
        if notable_objects:
            line += f"With. {', '.join(sorted(notable_objects))}. "
        if obs.vlm_description:
            desc = obs.vlm_description.strip().replace("\n", " ")
            if len(desc) > 240:
                desc = desc[:237] + "."
            line += f"Scene. {desc}"
        narrative_lines.append(line.strip())

    # Trim so we stay under token budget. Keep earliest + latest slices.
    MAX_LINES = 60
    if len(narrative_lines) > MAX_LINES:
        half = MAX_LINES // 2
        narrative_lines = (
            narrative_lines[:half]
            + [f". {len(narrative_lines) - MAX_LINES} similar moments omitted ."]
            + narrative_lines[-half:]
        )

    # People roll-up for prompt context.
    roll_up_lines: list[str] = []
    for name, moments in named_sessions.items():
        first = _format_timestamp(min(moments))
        last = _format_timestamp(max(moments))
        if first == last:
            roll_up_lines.append(f"{name} seen around {first}.")
        else:
            roll_up_lines.append(f"{name} seen from about {first} to {last}.")
    if unknown_sessions:
        first = _format_timestamp(min(unknown_sessions))
        last = _format_timestamp(max(unknown_sessions))
        if first == last:
            roll_up_lines.append(f"Unknown person seen around {first}.")
        else:
            roll_up_lines.append(f"Unknown person activity from {first} to {last}.")

    # Look up unknown cluster appearance descriptions active in this window
    # so highlight pills read like "Unknown person in red jacket, 7:16 pm"
    # instead of raw statistics. Clusters are linked via face_cluster_samples
    # captured inside [start, now].
    unknown_highlights: list[str] = []
    try:
        sample_rows = await db.execute(
            select(FaceCluster)
            .join(FaceClusterSample, FaceClusterSample.cluster_id == FaceCluster.id)
            .where(FaceClusterSample.captured_at >= start)
            .where(FaceClusterSample.captured_at <= now)
            .where(FaceCluster.status == "pending")
            .where(FaceCluster.person_id.is_(None))
            .distinct()
        )
        seen_clusters = sample_rows.scalars().all()
        for cluster in seen_clusters:
            last = cluster.last_seen_at
            when = _format_timestamp(last) if last else ""
            label = (
                f"Unknown {cluster.auto_label_number}"
                if cluster.auto_label_number
                else "Unknown person"
            )
            appearance = (cluster.appearance_description or "").strip()
            if appearance:
                unknown_highlights.append(
                    f"{label} ({appearance}) around {when}" if when
                    else f"{label} ({appearance})"
                )
            else:
                unknown_highlights.append(
                    f"{label} around {when}" if when else label
                )
    except Exception:
        logger.exception("Failed to load cluster appearance for digest")

    # Compose highlight pills. Safety first, then named people visits,
    # then unknown appearance hints. Cap at 4 for the UI.
    for hit in safety_hits[:2]:
        highlights.append(hit)
    for name, moments in named_sessions.items():
        first = _format_timestamp(min(moments))
        last = _format_timestamp(max(moments))
        highlights.append(
            f"{name} seen around {first}" if first == last
            else f"{name} here from {first} to {last}"
        )
    for line in unknown_highlights:
        highlights.append(line)
    highlights = highlights[:4]

    # Generate natural language summary with the active text/VLM provider.
    summary_text = None
    if provider:
        try:
            system_prompt = custom_prompt or DEFAULT_DIGEST_PROMPT
            user_prompt = (
                f"Reporting period. {period_label}.\n"
                f"Cameras involved. {', '.join(camera_activity.keys()) or 'none'}.\n"
            )
            if safety_hits:
                user_prompt += "Safety flags.\n" + "\n".join(safety_hits) + "\n"
            if roll_up_lines:
                user_prompt += "People summary.\n" + "\n".join(roll_up_lines) + "\n"
            user_prompt += (
                "\nObservation feed (chronological).\n"
                + "\n".join(narrative_lines)
                + "\n\nWrite the digest as a short plain-English story "
                "about what happened. Lead with any safety flag. Use the "
                "people summary to avoid double counting. Do not output "
                "statistics."
            )
            summary_text = await _call_text_llm(provider, system_prompt, user_prompt)
            if summary_text:
                summary_text = summary_text.strip()
        except Exception:
            logger.exception("VLM digest summary failed")

    if not summary_text:
        # Narrative fallback built from the same roll-ups. No raw counts.
        parts: list[str] = []
        if safety_hits:
            parts.append(
                f"Safety flag. {safety_hits[0]}"
            )
        if roll_up_lines:
            parts.append(" ".join(roll_up_lines))
        elif camera_activity:
            parts.append(
                f"Quiet period on {', '.join(camera_activity.keys())}. "
                "Motion was detected but nothing notable was identified."
            )
        else:
            parts.append("Nothing notable happened during this period.")
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
