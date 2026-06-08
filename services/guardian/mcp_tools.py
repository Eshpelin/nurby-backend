"""Guardian-scoped, read-only MCP tools.

These let a guardian ask "is my child at school right now?" from an MCP client.
Every tool self-scopes to the calling user's own active guardian links and
honors the same delay/throttle/blur entitlements as the app. A non-guardian
user simply has no links, so the tools return an empty result rather than
leaking anyone.

Tool fns share the agent-tool signature ``async fn(ctx, **kwargs)`` where
``ctx`` carries ``user`` and ``db``, so the existing MCP server dispatch and
budget gating apply unchanged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from services.guardian import entitlements as ent
from services.guardian import presence as presence_mod
from shared.app_settings import get_setting
from shared.models import GuardianLink, Person


async def _active_links_for_user(db, user_id) -> list:
    rows = (
        await db.execute(select(GuardianLink).where(GuardianLink.guardian_user_id == user_id))
    ).scalars().all()
    return [link for link in rows if ent.is_active(link)]


async def guardian_dependant_status(ctx: dict, person_name: str | None = None) -> dict:
    """Where each of your dependants is right now (or as of the free-tier
    delay). Returns one entry per dependant you follow. Never references anyone
    you are not bound to. Optionally filter by name."""
    user = ctx["user"]
    db = ctx["db"]
    delay = int(await get_setting("guardian_free_delay_seconds", 1800))
    links = await _active_links_for_user(db, user.id)
    out: list[dict[str, Any]] = []
    for link in links:
        if not ent.can_view(link, ent.CAP_STATUS):
            continue
        person = await db.get(Person, link.person_id)
        if person is None:
            continue
        name = person.nickname or person.display_name
        if person_name and person_name.strip().lower() not in (name or "").lower():
            continue
        status = await presence_mod.dependant_status(db, link, person, free_delay_seconds=delay)
        seen = status["last_seen_at"]
        out.append(
            {
                "display_name": name,
                "state": status["state"],
                "zone": status["zone"],
                "last_seen_at": seen.isoformat() if seen else None,
                "delayed": status["delayed"],
            }
        )
    return {"dependants": out, "count": len(out)}


async def guardian_recent_events(ctx: dict, limit: int = 10) -> dict:
    """Recent sightings of the dependants you follow (zone + time), honoring the
    free-tier delay. Use for 'what happened today'. Only your own dependants."""
    from datetime import timedelta

    from sqlalchemy import String as SAString
    from sqlalchemy import cast

    from shared.models import Camera, Observation

    user = ctx["user"]
    db = ctx["db"]
    limit = max(1, min(50, int(limit)))
    delay = int(await get_setting("guardian_free_delay_seconds", 1800))
    links = await _active_links_for_user(db, user.id)
    events: list[dict[str, Any]] = []
    for link in links:
        if not ent.can_view(link, ent.CAP_TIMELINE):
            continue
        person = await db.get(Person, link.person_id)
        if person is None:
            continue
        cutoff = ent.cutoff_time(link, delay)
        needle = f'%"person_id": "{person.id}"%'
        rows = (
            await db.execute(
                select(Observation)
                .where(Observation.started_at <= cutoff)
                .where(Observation.started_at >= cutoff - timedelta(days=7))
                .where(cast(Observation.person_detections, SAString).ilike(needle))
                .order_by(Observation.started_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        for o in rows:
            cam = await db.get(Camera, o.camera_id)
            events.append(
                {
                    "display_name": person.nickname or person.display_name,
                    "at": o.started_at.isoformat(),
                    "zone": (cam.location_label or cam.name) if cam else None,
                    "delayed": not getattr(link, "live_presence", False),
                }
            )
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"events": events[:limit], "count": len(events[:limit])}


async def guardian_wellbeing(ctx: dict, person_name: str | None = None) -> dict:
    """Wellbeing rollup for the dependants you follow: did they eat today, did
    they fall recently, and a per-action breakdown over the last week. Reads the
    structured action signals, honors the free-tier delay, and only ever returns
    your own dependants. Best-effort, never a medical guarantee."""
    from services.guardian import wellbeing as wb

    user = ctx["user"]
    db = ctx["db"]
    delay = int(await get_setting("guardian_free_delay_seconds", 1800))
    links = await _active_links_for_user(db, user.id)
    out: list[dict[str, Any]] = []
    for link in links:
        if not ent.can_view(link, ent.CAP_TIMELINE):
            continue
        person = await db.get(Person, link.person_id)
        if person is None:
            continue
        name = person.nickname or person.display_name
        if person_name and person_name.strip().lower() not in (name or "").lower():
            continue
        cutoff = ent.cutoff_time(link, delay)
        summary = await wb.wellbeing_summary(db, person.id, cutoff=cutoff)
        out.append(
            {
                "display_name": name,
                "ate_today": summary["ate_today"],
                "last_fall_at": summary["last_fall_at"],
                "last_action": summary["last_action"],
                "counts": summary["counts"],
                "delayed": ent.effective_delay_seconds(link, delay) > 0,
            }
        )
    return {"dependants": out, "count": len(out)}


GUARDIAN_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "guardian_dependant_status",
        "description": (
            "Current presence of the dependants you are a guardian for. Use for "
            "'is my child at school?'. Free tier data is delayed 30 minutes; the "
            "'delayed' flag tells you. Only ever returns your own dependants."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person_name": {
                    "type": "string",
                    "description": "Optional. Filter to one dependant by name.",
                }
            },
        },
        "side_effect": "read",
        "fn": guardian_dependant_status,
    },
    {
        "name": "guardian_recent_events",
        "description": (
            "Recent sightings of the dependants you follow (zone + time). Use for "
            "'what happened today'. Free tier data is delayed. Only your own "
            "dependants are ever returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max events to return (1-50, default 10).",
                }
            },
        },
        "side_effect": "read",
        "fn": guardian_recent_events,
    },
    {
        "name": "guardian_wellbeing",
        "description": (
            "Wellbeing rollup for the dependants you follow: did they eat today, "
            "did they fall recently, and a per-action breakdown over the last "
            "week. Use for 'did Mum eat lunch' or 'has Dad fallen'. Best-effort "
            "signals, not a medical guarantee. Free tier data is delayed. Only "
            "your own dependants are ever returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person_name": {
                    "type": "string",
                    "description": "Optional. Filter to one dependant by name.",
                }
            },
        },
        "side_effect": "read",
        "fn": guardian_wellbeing,
    },
]


def guardian_tool_names() -> list[str]:
    return [t["name"] for t in GUARDIAN_MCP_TOOLS]


def get_guardian_tool(name: str) -> dict[str, Any] | None:
    for t in GUARDIAN_MCP_TOOLS:
        if t["name"] == name:
            return t
    return None
