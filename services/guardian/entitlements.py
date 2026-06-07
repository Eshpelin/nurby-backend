"""Pure entitlement, delay, and throttle logic for Guardian by Nurby.

Every gate the product promises (brief section 24.12) is decided here, with
no database or framework imports, so it is exhaustively unit-testable. The API
layer resolves a ``GuardianLink`` row and the relevant settings, then calls
these helpers. Callers pass plain objects; tests pass ``SimpleNamespace``.

Decisions implemented:
- Free data is always delayed 30 min. ``live_presence`` removes the delay.
- Free tier serves at most 1 image/hour. ``live_video`` lifts the cap.
- Tiers (full | summary | alerts_only) gate which capabilities exist at all.
- ``premium`` unlocks recap + smart search. ``audio`` unlocks audio signals.
- Reveal-confidence floors only ever ratchet up, never down.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol

# Capability identifiers used across the API + MCP surfaces.
CAP_STATUS = "status"
CAP_TIMELINE = "timeline"
CAP_IMAGE = "image"
CAP_LIVE_VIDEO = "live_video"
CAP_AUDIO = "audio"
CAP_RECAP = "recap"
CAP_SEARCH = "search"

# Alert kinds a guardian can opt in/out of (within the facility-allowed set).
ALERT_KINDS = (
    "arrived",
    "departed",
    "picked_up",
    "entered_zone",
    "left_zone",
    "not_seen",
)

# Defaults for a freshly granted link: the high-value, low-risk green pair on.
DEFAULT_ALERT_PREFS = {
    "arrived": True,
    "departed": True,
    "picked_up": True,
    "entered_zone": False,
    "left_zone": False,
    "not_seen": False,
}

_TIERS = ("full", "summary", "alerts_only")

# Which capabilities each tier may ever access (before entitlement flags).
_TIER_CAPS: dict[str, set[str]] = {
    "full": {CAP_STATUS, CAP_TIMELINE, CAP_IMAGE, CAP_LIVE_VIDEO, CAP_AUDIO, CAP_RECAP, CAP_SEARCH},
    "summary": {CAP_STATUS, CAP_TIMELINE, CAP_IMAGE, CAP_AUDIO, CAP_RECAP, CAP_SEARCH},
    "alerts_only": set(),
}

# Capabilities that additionally require a paid entitlement flag on the link.
_CAP_REQUIRES_FLAG: dict[str, str] = {
    CAP_LIVE_VIDEO: "live_video",
    CAP_AUDIO: "audio",
    CAP_RECAP: "premium",
    CAP_SEARCH: "premium",
}


class LinkLike(Protocol):
    tier: str
    premium: bool
    live_presence: bool
    live_video: bool
    audio: bool
    is_primary_parent: bool
    revoked_at: datetime | None
    expires_at: datetime | None
    reveal_min_confidence: float | None
    last_image_served_at: datetime | None


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def is_active(link: LinkLike, now: datetime | None = None) -> bool:
    """A link grants access only while not revoked and not expired."""
    now = _now(now)
    if getattr(link, "revoked_at", None) is not None:
        return False
    exp = getattr(link, "expires_at", None)
    if exp is not None and exp <= now:
        return False
    return True


def effective_delay_seconds(link: LinkLike, free_delay_seconds: int) -> int:
    """0 when the link holds live_presence, else the configured free delay."""
    return 0 if getattr(link, "live_presence", False) else max(0, int(free_delay_seconds))


def cutoff_time(link: LinkLike, free_delay_seconds: int, now: datetime | None = None) -> datetime:
    """The newest timestamp this link may observe. Data after this is hidden
    from a non-paid guardian. Paid (live_presence) links see up to ``now``."""
    now = _now(now)
    return now - timedelta(seconds=effective_delay_seconds(link, free_delay_seconds))


def can_view(link: LinkLike, capability: str) -> bool:
    """True when the link's tier permits the capability and any required paid
    entitlement flag is set. Does not check active/expiry. callers gate that
    separately so they can return a clean 403 vs 410."""
    tier = getattr(link, "tier", "full")
    allowed = _TIER_CAPS.get(tier, set())
    if capability not in allowed:
        return False
    flag = _CAP_REQUIRES_FLAG.get(capability)
    if flag is not None and not getattr(link, flag, False):
        return False
    return True


def image_allowed(
    link: LinkLike, free_image_interval_seconds: int, now: datetime | None = None
) -> bool:
    """Free tier may receive at most one image per interval. ``live_video``
    lifts the cap entirely. The throttle is keyed on ``last_image_served_at``."""
    if getattr(link, "live_video", False):
        return True
    now = _now(now)
    last = getattr(link, "last_image_served_at", None)
    if last is None:
        return True
    return (now - last).total_seconds() >= max(0, int(free_image_interval_seconds))


def seconds_until_next_image(
    link: LinkLike, free_image_interval_seconds: int, now: datetime | None = None
) -> int:
    """How long until the next free image is allowed. 0 when one is allowed now."""
    if image_allowed(link, free_image_interval_seconds, now):
        return 0
    now = _now(now)
    last = getattr(link, "last_image_served_at")
    elapsed = (now - last).total_seconds()
    return max(0, int(free_image_interval_seconds - elapsed))


def reveal_threshold(
    link: LinkLike,
    *,
    system_default: float,
    facility_floor: float | None = None,
    camera_floor: float | None = None,
) -> float:
    """The face-reveal confidence floor for this link. Floors only ratchet up.
    A parent override may raise the bar but never drop below the facility or
    system floor. Reveal fails to blur, never fails to expose."""
    floors = [system_default]
    for f in (facility_floor, camera_floor, getattr(link, "reveal_min_confidence", None)):
        if f is not None:
            floors.append(float(f))
    return max(floors)


def extra_guardians_unlocked(
    links_on_person: Iterable[LinkLike], now: datetime | None = None
) -> bool:
    """Extra guardians are free when at least one active primary-parent link on
    the same person holds any paid entitlement (brief section 24.8)."""
    paid_flags = ("premium", "live_presence", "live_video", "audio")
    for ln in links_on_person:
        if not is_active(ln, now):
            continue
        if not getattr(ln, "is_primary_parent", False):
            continue
        if any(getattr(ln, f, False) for f in paid_flags):
            return True
    return False


def alert_enabled(link: LinkLike, kind: str) -> bool:
    """Whether the guardian opted into a given alert kind. Falls back to the
    sensible defaults when the link has no stored prefs yet."""
    prefs = getattr(link, "alert_prefs", None) or DEFAULT_ALERT_PREFS
    return bool(prefs.get(kind, DEFAULT_ALERT_PREFS.get(kind, False)))


def sanitize_alert_prefs(prefs: dict | None, allowed: Iterable[str] | None = None) -> dict:
    """Coerce a submitted prefs dict to known keys + booleans, optionally
    restricted to the facility-allowed alert set."""
    allowed_set = set(allowed) if allowed is not None else set(ALERT_KINDS)
    out = dict(DEFAULT_ALERT_PREFS)
    for k in ALERT_KINDS:
        if k not in allowed_set:
            out[k] = False
            continue
        if prefs and k in prefs:
            out[k] = bool(prefs[k])
    return out


def entitlement_summary(link: LinkLike) -> dict:
    """Flat view of what this link can do, for the UI upsell + MCP responses."""
    return {
        "tier": getattr(link, "tier", "full"),
        "delayed": not getattr(link, "live_presence", False),
        "premium": bool(getattr(link, "premium", False)),
        "live_presence": bool(getattr(link, "live_presence", False)),
        "live_video": bool(getattr(link, "live_video", False)),
        "audio": bool(getattr(link, "audio", False)),
        "can": {
            cap: can_view(link, cap)
            for cap in (
                CAP_STATUS, CAP_TIMELINE, CAP_IMAGE, CAP_LIVE_VIDEO,
                CAP_AUDIO, CAP_RECAP, CAP_SEARCH,
            )
        },
    }
