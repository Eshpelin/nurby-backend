"""Small key-value store for runtime-toggleable flags.

Covers things that should be flippable from the UI without a redeploy.
All values are wrapped in a `{"value": ...}` JSON object so a bare bool
or scalar stays a first-class column citizen.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import async_session
from shared.models import AppSetting


DEFAULTS: dict[str, Any] = {
    "nudity_blur": True,  # default-on safety feature
    "nudity_blur_min_score": 0.5,
    # Cross-camera journey idle window. Formerly hardcoded
    # JOURNEY_IDLE_SECONDS = 300 in journey_tracker.py.
    "journey_idle_seconds": 300,
    # Household-wide daily AI digest.
    "daily_digest_enabled": True,
    "daily_digest_hour": 7,  # 0-23 local time
    "daily_digest_provider_id": None,
    # IANA timezone for the household (anchors daily digest hour
    # selection). Null = use the perception host's locale.
    "system_timezone": None,
    # PANNs audio tagging master switch.
    "audio_events": True,
    # Body re-identification housekeeping. Tentative body clusters
    # (no face co-verification) get pruned after this many days of
    # inactivity. Confirmed clusters (linked to a Person) are never
    # auto-pruned. Set 0 to disable decay.
    "body_reid_tentative_decay_days": 14,
    # Body+face fusion sweeper interval (seconds).
    "body_reid_fusion_interval_seconds": 300,
    # Tracklet centroid buffer. How many body samples buffered per
    # Journey before a centroid-clustering pass replaces the
    # per-frame cluster decisions.
    "body_reid_tracklet_min_samples": 5,
    # Minimum sighting count before a body/face cluster is offered
    # for naming via Telegram. Used by reid_sweeper.
    "cluster_naming_min_sightings": 3,
    # Public-facing base URL for the API (used in templated
    # notifications and Telegram webhook registration). Sourced from
    # env config by default; an explicit override stored here wins.
    "public_base_url": None,
    # Rule cooldown backing store. "redis" persists per-rule last-fired
    # epoch across perception restarts and across multiple workers;
    # "memory" reverts to single-process in-RAM tracking (cooldowns
    # reset on restart). Default redis so cooldowns survive restarts.
    "rules_cooldown_backend": "redis",
    # ── Agent v1 (Wave 1A, docs/agent-design.md section 7) ──────────
    # Per-user daily token budget across all agent runs. Counted on
    # both orchestration LLM + analyzer (VLM) tokens.
    "agent_daily_token_budget_per_user": 500000,
    # Per-user daily cost budget in USD cents ($5/day).
    "agent_daily_cost_cents_per_user": 500,
    # Hard cap on tool turns per single agent run. Driver stops the
    # loop and triggers the partial-answer synthesis path on hit.
    "agent_max_turns_per_run": 15,
    # Hard cap on VLM analyzer calls per single agent run.
    "agent_max_vlm_calls_per_run": 8,
    # Refuse analyze_clip targets wider than this many minutes. Keeps
    # stitched-clip frame sampling bounded.
    "agent_max_clip_minutes": 60,
    # Default orchestration provider for agent runs. Null = the user
    # picks one each ask (or the UI defaults to the user's last pick).
    "agent_default_provider_id": None,
    # Soft warning threshold (% of either daily token or cost budget).
    # The driver emits a banner to the user once usage crosses this
    # line; runs are still allowed up to 100%.
    "agent_warn_threshold_pct": 80,
    # ── VLM backlog (Redis-backed per-camera buffer) ─────────────────
    # Per-camera capacity of the VLM job backlog. 50 covers a typical
    # 30-sec walk-by even on a slow Ollama host that takes 15s/frame.
    "vlm_backlog_capacity_per_camera": 50,
    # JPEG-encoded frames sit on a separate Redis key with this TTL.
    # If the worker can't catch up within the window, the frame self-
    # expires and the worker skips the stale entry with a drop record.
    "vlm_frame_ttl_seconds": 1800,
}


async def get_setting(key: str, default: Any = None) -> Any:
    if default is None:
        default = DEFAULTS.get(key)
    try:
        async with async_session() as db:
            row = await db.get(AppSetting, key)
            if row is None:
                return default
            val = row.value
            if isinstance(val, dict) and "value" in val:
                return val["value"]
            return val
    except Exception:
        return default


async def set_setting(key: str, value: Any) -> None:
    async with async_session() as db:
        row = await db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value={"value": value})
            db.add(row)
        else:
            row.value = {"value": value}
        await db.commit()


async def get_all_settings(db: AsyncSession) -> dict[str, Any]:
    result = await db.execute(select(AppSetting))
    stored: dict[str, Any] = {}
    for row in result.scalars().all():
        v = row.value
        if isinstance(v, dict) and "value" in v:
            stored[row.key] = v["value"]
        else:
            stored[row.key] = v
    # Merge defaults so the UI always knows every supported key.
    merged = dict(DEFAULTS)
    merged.update(stored)
    return merged
