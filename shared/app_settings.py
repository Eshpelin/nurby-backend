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
