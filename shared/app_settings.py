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
    # First-run onboarding wizard dismissal. Household-wide + server-side
    # so it survives a browser/device change and an admin can re-trigger
    # the wizard by flipping it back to false. localStorage is only a
    # fast-path cache on top of this.
    "onboarding_dismissed": False,
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
    # ── CLIP zero-shot gate (Section 7H, "is anything interesting") ──
    # Master switch. When false the gate falls open and every frame
    # past dedupe + motion still hits the VLM enqueue.
    "vlm_gate_enabled": True,
    # Boring class must beat interesting by at least this much to
    # trigger a skip. Tighter (smaller) = skip more aggressively;
    # looser = let through more frames.
    "vlm_gate_margin": 0.05,
    # Absolute floor on the top interesting score. Below this the
    # classifier wasn't confident anything interesting was present.
    "vlm_gate_min_interesting_score": 0.20,
    # Override prompt lists per-camera in a future revision; for v1
    # the defaults baked into services/perception/vlm_gate.py win.
    "vlm_gate_interesting_prompts": None,
    "vlm_gate_boring_prompts": None,
    # ── Idle VLM enrichment (docs/vlm-enrichment-design.md) ──────────
    # When the live VLM backlog is empty, spend spare capacity adding
    # immutable enrichment passes to already-captured frames and a
    # synthesized summary. On by default. system-wide toggle. budget caps
    # how many VLM-minutes per hour enrichment may consume so it never
    # competes with live work for long.
    "vlm_enrichment_enabled": True,
    "vlm_enrichment_budget_minutes_per_hour": 20,
    "vlm_enrichment_max_passes": 6,
    # ── Vehicle appearance matching ─────────────────────────────────
    # When a vehicle's plate is not readable, a new sighting is matched to
    # an existing vehicle (known/plated or plateless) by CLIP appearance if
    # the cosine similarity is at least this, and the vehicle type agrees.
    # Higher = stricter (fewer false matches, more duplicate identities).
    "vehicle_appearance_match_min_similarity": 0.90,
    # ── Guardian by Nurby (docs/guardian-portal-product-brief.md s.24) ───
    # Master switch for the guardian panel + guardian API.
    "guardian_enabled": True,
    # Free-tier data is always delayed by this much. A non-paid guardian
    # only ever sees state at or before (now - delay). live_presence
    # entitlement removes the delay per link.
    "guardian_free_delay_seconds": 1800,
    # Free tier may be served at most one image per this interval per
    # dependant. live_video entitlement lifts the cap.
    "guardian_free_image_interval_seconds": 3600,
    # Default face-reveal confidence floor. The bound dependant is only
    # revealed (unblurred) above this. Facility and per-camera overrides may
    # raise it; a per-link override may only raise it further. Never lowered.
    "guardian_reveal_min_confidence": 0.90,
    # Safety governor. A single dependant cannot be followed across more than
    # this many cameras. Facility may override. Hitting it is logged.
    "guardian_max_cameras_per_person": 12,
    # Auto pickup-escort detection. On a dependant's departure, look back over
    # this window on the departure camera for a co-present person or vehicle
    # and treat them as the escort (verified against the approved-pickup
    # registry). When off, departures fire a plain "departed" alert.
    "guardian_pickup_detection_enabled": True,
    "guardian_pickup_window_seconds": 120,
    # Gaussian blur radius applied to every image served to a guardian, so no
    # non-dependant face is identifiable. Higher = more private, less legible.
    "guardian_image_blur_radius": 12,
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
