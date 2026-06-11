"""Telegram notification channel management routes.

All endpoints are scoped to the authenticated user. Phase 1 covers
CRUD, the guided pairing flow, and a single-shot test send. Phase 2+
will add inline button acks, photo attachments, and household-shared
channels.

Security notes.
    - Bot tokens are accepted only as inbound JSON payloads. They are
      Fernet-encrypted via :mod:`shared.crypto` before persistence and
      are never returned in responses.
    - Pairing nonces are 16 random bytes (hex), stored in Redis with a
      5 minute TTL keyed by ``nurby:tg_pair:<nonce>`` and consumed by
      :mod:`services.notify.telegram_poller`.
    - ``GET /channels/{id}`` and ``GET /channels`` filter strictly by
      ``current_user.id`` so users cannot enumerate other users' bots.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.notify.telegram import TelegramAPI, TelegramError
from services.notify.telegram_poller import store_pair_nonce
from shared.auth import get_current_user
from shared.config import settings
from shared.crypto import InvalidToken, decrypt_secret, encrypt_secret
from shared.database import get_db
from shared.models import Rule, TelegramChannel, User
from shared.schemas import (
    TelegramChannelCreate,
    TelegramChannelResponse,
    TelegramChannelUpdate,
    TelegramDeliveryUpdate,
    TelegramPairInitResponse,
    TelegramTestResponse,
    TelegramWebhookInfoResponse,
)

router = APIRouter()
logger = logging.getLogger("nurby.api.telegram")

PAIR_TTL_SECONDS = 300


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _pairing_status(ch: TelegramChannel) -> str:
    """Derive the client-facing pairing status string."""
    if not ch.enabled:
        return "disabled"
    if ch.paired_at is None or not ch.chat_id:
        return "pending"
    if ch.last_test_ok is False and (ch.last_error or "").lower().startswith(("forbidden", "blocked", "403")):
        return "blocked"
    if ch.last_test_ok is False:
        return "error"
    return "paired"


def _serialize(
    ch: TelegramChannel,
    *,
    current_user_id: uuid.UUID | None = None,
    owner_display_name: str | None = None,
) -> dict:
    """Project a TelegramChannel row into the response shape.

    ``current_user_id`` flips the computed ``owned_by_me`` flag. The
    Phase 4 list endpoint passes it so a non-owner sees a shared
    channel with ``owned_by_me=false`` and a friendly
    ``owner_display_name``.
    """
    owned = current_user_id is None or ch.user_id == current_user_id
    return {
        "id": ch.id,
        "label": ch.label,
        "bot_username": ch.bot_username,
        "chat_id": ch.chat_id,
        "chat_title": ch.chat_title,
        "chat_type": ch.chat_type,
        "default_silent": ch.default_silent,
        "enabled": ch.enabled,
        "paired_at": ch.paired_at,
        "last_test_at": ch.last_test_at,
        "last_test_ok": ch.last_test_ok,
        "last_error": ch.last_error,
        "pairing_status": _pairing_status(ch),
        # Phase 3 fields. webhook_secret is intentionally never exposed.
        "delivery_mode": getattr(ch, "delivery_mode", None) or "long_poll",
        "webhook_url": getattr(ch, "webhook_url", None),
        "media_quality": getattr(ch, "media_quality", None) or "high",
        "rate_limit_per_chat_qps": float(getattr(ch, "rate_limit_per_chat_qps", 1.0) or 1.0),
        "rate_limit_per_chat_burst": int(getattr(ch, "rate_limit_per_chat_burst", 3) or 3),
        "dedupe_window_seconds": int(getattr(ch, "dedupe_window_seconds", 30) or 30),
        # Phase 4 household sharing fields.
        "shared_with_household": bool(getattr(ch, "shared_with_household", False)),
        "share_permissions": getattr(ch, "share_permissions", None) or "use",
        "owned_by_me": owned,
        "owner_display_name": owner_display_name,
        "created_at": ch.created_at,
    }


async def _load_channel(
    channel_id: uuid.UUID, user: User, db: AsyncSession
) -> TelegramChannel:
    """Load a channel that the caller owns. Used for owner-only ops."""
    ch = await db.get(TelegramChannel, channel_id)
    if ch is None or ch.user_id != user.id:
        raise HTTPException(status_code=404, detail="Channel not found")
    return ch


async def _load_channel_visible(
    channel_id: uuid.UUID, user: User, db: AsyncSession
) -> TelegramChannel:
    """Load a channel the caller can at least see (owner OR household).

    Returns the row when the caller is the owner OR the channel has
    ``shared_with_household=True``. 404 otherwise so we don't leak
    existence to outsiders.
    """
    ch = await db.get(TelegramChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.user_id == user.id:
        return ch
    if bool(getattr(ch, "shared_with_household", False)):
        return ch
    raise HTTPException(status_code=404, detail="Channel not found")


async def _owner_name_map(
    db: AsyncSession, owner_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Resolve user_id -> display_name for the channel list response."""
    if not owner_ids:
        return {}
    unique = list({uid for uid in owner_ids if uid is not None})
    if not unique:
        return {}
    result = await db.execute(select(User).where(User.id.in_(unique)))
    out: dict[uuid.UUID, str] = {}
    for u in result.scalars().all():
        out[u.id] = u.display_name or u.email or "someone"
    return out


@router.post("/channels", response_model=TelegramChannelResponse, status_code=201)
async def create_channel(
    body: TelegramChannelCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate bot token via getMe, then persist an unpaired channel."""
    token = body.bot_token.strip()
    if ":" not in token:
        raise HTTPException(status_code=400, detail="Bot token format looks wrong. Expected `<id>:<hash>`.")

    try:
        me = await TelegramAPI.get_me(token)
    except TelegramError as exc:
        raise HTTPException(status_code=400, detail=exc.description or "Telegram rejected the token")

    bot_username = me.get("username") or ""
    enc = encrypt_secret(token)

    ch = TelegramChannel(
        user_id=current_user.id,
        label=body.label.strip(),
        bot_token_enc=enc,
        bot_username=bot_username,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return _serialize(ch, current_user_id=current_user.id)


@router.get("/channels", response_model=list[TelegramChannelResponse])
async def list_channels(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owned + household-shared channels.

    Phase 4. A user sees their own channels plus any other user's
    channel that flipped ``shared_with_household`` on. The response
    distinguishes owner via ``owned_by_me`` so the UI can hide
    destructive controls for non-owners.
    """
    from sqlalchemy import or_ as sa_or

    result = await db.execute(
        select(TelegramChannel)
        .where(
            sa_or(
                TelegramChannel.user_id == current_user.id,
                TelegramChannel.shared_with_household.is_(True),
            )
        )
        .order_by(TelegramChannel.created_at.desc())
    )
    rows = list(result.scalars().all())
    name_map = await _owner_name_map(db, [c.user_id for c in rows])
    return [
        _serialize(
            c,
            current_user_id=current_user.id,
            owner_display_name=name_map.get(c.user_id),
        )
        for c in rows
    ]


@router.get("/channels/{channel_id}", response_model=TelegramChannelResponse)
async def get_channel(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await _load_channel_visible(channel_id, current_user, db)
    name_map = await _owner_name_map(db, [ch.user_id])
    return _serialize(
        ch,
        current_user_id=current_user.id,
        owner_display_name=name_map.get(ch.user_id),
    )


@router.patch("/channels/{channel_id}", response_model=TelegramChannelResponse)
async def update_channel(
    channel_id: uuid.UUID,
    body: TelegramChannelUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await _load_channel(channel_id, current_user, db)
    data = body.model_dump(exclude_unset=True)
    if "label" in data and data["label"]:
        ch.label = data["label"].strip()
    if "default_silent" in data and data["default_silent"] is not None:
        ch.default_silent = bool(data["default_silent"])
    if "enabled" in data and data["enabled"] is not None:
        ch.enabled = bool(data["enabled"])
        # If user is re-enabling a previously-blocked channel, clear the
        # stale error so the status pill flips back to paired (the next
        # successful send confirms; a failed test will reset it).
        if ch.enabled and ch.last_test_ok is False:
            ch.last_error = None
    # Phase 3 settings. delivery_mode is NOT writable here. it lives
    # behind POST /channels/{id}/delivery so we can call setWebhook /
    # deleteWebhook atomically with the row update.
    if "media_quality" in data and data["media_quality"] is not None:
        ch.media_quality = data["media_quality"]
    if "rate_limit_per_chat_qps" in data and data["rate_limit_per_chat_qps"] is not None:
        ch.rate_limit_per_chat_qps = float(data["rate_limit_per_chat_qps"])
    if "rate_limit_per_chat_burst" in data and data["rate_limit_per_chat_burst"] is not None:
        ch.rate_limit_per_chat_burst = int(data["rate_limit_per_chat_burst"])
    if "dedupe_window_seconds" in data and data["dedupe_window_seconds"] is not None:
        ch.dedupe_window_seconds = int(data["dedupe_window_seconds"])
    # Phase 4 household sharing. Owner-only because _load_channel
    # already enforces ownership.
    if "shared_with_household" in data and data["shared_with_household"] is not None:
        ch.shared_with_household = bool(data["shared_with_household"])
    if "share_permissions" in data and data["share_permissions"] is not None:
        ch.share_permissions = data["share_permissions"]
    await db.commit()
    await db.refresh(ch)
    return _serialize(ch, current_user_id=current_user.id)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Phase 4. Deleting a shared channel by a non-owner is forbidden
    # so a household member can't quietly nuke another user's bot.
    ch = await db.get(TelegramChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.user_id != current_user.id:
        if bool(getattr(ch, "shared_with_household", False)):
            raise HTTPException(
                status_code=403,
                detail="Only the owner can delete a shared channel. Ask them to revoke sharing first.",
            )
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(ch)
    await db.commit()


@router.get("/channels/{channel_id}/rule-usage")
async def channel_rule_usage(
    channel_id: uuid.UUID,
    scope: str = "all",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Count rules whose action chain references this channel.

    Phase 4. Household-shared channels can be picked by any user so
    the default count crosses owners. ``?scope=mine`` keeps the
    pre-Phase-4 behaviour for callers that want to gate their own
    rule-builder UI. Rules don't carry an owner column today so the
    scope filter is a no-op until ownership lands; documented here so
    the endpoint contract is stable.
    """
    ch = await _load_channel_visible(channel_id, current_user, db)
    target = str(ch.id)
    result = await db.execute(select(Rule))
    count = 0
    for rule in result.scalars().all():
        acts = rule.actions
        if isinstance(acts, dict):
            acts = [acts]
        if not isinstance(acts, list):
            continue
        for a in acts:
            if isinstance(a, dict) and a.get("type") == "telegram" and str(a.get("channel_id") or "") == target:
                count += 1
                break
    return {"rule_count": count, "scope": scope}


@router.post("/channels/{channel_id}/pair-init", response_model=TelegramPairInitResponse)
async def pair_init(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint a fresh single-use pairing nonce and return a deep link."""
    ch = await _load_channel(channel_id, current_user, db)
    if not ch.bot_username:
        raise HTTPException(status_code=400, detail="Bot username is missing. Re-create the channel.")

    nonce = secrets.token_hex(16)
    redis = _redis()
    try:
        await store_pair_nonce(redis, nonce, str(ch.id), ttl=PAIR_TTL_SECONDS)
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass

    deep_link = f"https://t.me/{ch.bot_username}?start={nonce}"
    return TelegramPairInitResponse(
        nonce=nonce,
        deep_link=deep_link,
        qr_payload=deep_link,
        expires_in_seconds=PAIR_TTL_SECONDS,
    )


@router.post("/channels/{channel_id}/test", response_model=TelegramTestResponse)
async def test_channel(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Phase 4. Test send respects share_permissions. Owner always
    # allowed. Non-owner allowed only when the channel is shared AND
    # share_permissions='use_and_test'. Plain 'use' channels can be
    # referenced in rules but not test-fired by non-owners.
    ch = await db.get(TelegramChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.user_id != current_user.id:
        if not bool(getattr(ch, "shared_with_household", False)):
            raise HTTPException(status_code=404, detail="Channel not found")
        if (getattr(ch, "share_permissions", None) or "use") != "use_and_test":
            raise HTTPException(
                status_code=403,
                detail="The owner of this channel has not granted test-send permission.",
            )
    if not ch.chat_id or not ch.paired_at:
        raise HTTPException(status_code=400, detail="Channel is not paired yet")
    try:
        token = decrypt_secret(ch.bot_token_enc)
    except InvalidToken:
        raise HTTPException(status_code=500, detail="Bot token is unreadable. Replace it and re-pair.")

    text = f"🐾 Test notification from Nurby. <b>{_escape_html(ch.label)}</b>"
    try:
        result = await TelegramAPI.send_message(
            token, ch.chat_id, text,
            parse_mode="HTML",
            disable_notification=ch.default_silent,
        )
        ch.last_test_at = datetime.now(timezone.utc)
        ch.last_test_ok = True
        ch.last_error = None
        await db.commit()
        return TelegramTestResponse(ok=True, message_id=int(result.get("message_id") or 0))
    except TelegramError as exc:
        ch.last_test_at = datetime.now(timezone.utc)
        ch.last_test_ok = False
        ch.last_error = exc.description[:500]
        if exc.is_forbidden:
            # Surface blocked status. Keep enabled=True so the user can
            # retry via the Re-enable button; pairing_status() reads
            # last_error to render "Blocked".
            pass
        await db.commit()
        return TelegramTestResponse(ok=False, error=exc.description)


# ---------------------------------------------------------------------------
# Phase 3. delivery-mode switch + webhook receiver + diagnostics.
# ---------------------------------------------------------------------------


# In-process leaky bucket for the webhook receiver. 50 webhooks/sec
# per channel returns 429 thereafter. Prevents abuse if the secret
# leaks AND someone wants to DoS a single channel; per-channel scope
# means a noisy channel cannot starve the others.
_WEBHOOK_RATE_PER_CHANNEL_PER_SEC = 50
_webhook_buckets: dict[str, list[float]] = {}


def _webhook_allow(channel_id: str) -> bool:
    import time as _time

    now = _time.monotonic()
    arr = _webhook_buckets.setdefault(channel_id, [])
    # Drop samples older than 1 second.
    cutoff = now - 1.0
    while arr and arr[0] < cutoff:
        arr.pop(0)
    if len(arr) >= _WEBHOOK_RATE_PER_CHANNEL_PER_SEC:
        return False
    arr.append(now)
    return True


def _webhook_url_for(channel_id) -> str | None:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/telegram/webhook/{channel_id}"


async def _probe_backend_health() -> tuple[bool | None, str | None]:
    """HTTP-GET ``public_base_url/api/health`` from this process so we
    can surface "Backend not reachable" before the user finds out the
    hard way (Telegram silently shelving updates).
    """
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return None, "public_base_url is not set"
    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/health")
        if resp.status_code >= 400:
            return False, f"GET {base}/api/health returned {resp.status_code}"
        return True, None
    except Exception as exc:  # pragma: no cover. network-shape
        return False, f"GET {base}/api/health failed. {exc}"


@router.post("/channels/{channel_id}/delivery", response_model=TelegramChannelResponse)
async def set_delivery_mode(
    channel_id: uuid.UUID,
    body: TelegramDeliveryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Atomically flip a channel between long-poll and webhook
    delivery. On switch to webhook we mint a fresh secret + call
    setWebhook; on switch back we call deleteWebhook so updates start
    flowing through getUpdates again on the next poll-manager
    reconcile.
    """
    ch = await _load_channel(channel_id, current_user, db)
    try:
        token = decrypt_secret(ch.bot_token_enc)
    except InvalidToken:
        raise HTTPException(status_code=500, detail="Bot token is unreadable. Replace it and re-pair.")

    if body.mode == "long_poll":
        try:
            await TelegramAPI.delete_webhook(
                token, drop_pending_updates=bool(body.drop_pending_updates)
            )
        except TelegramError as exc:
            # Surface but don't fail. the row flip is what matters; the
            # next poller reconcile clears any leftover webhook anyway.
            logger.warning("deleteWebhook failed for channel=%s. %s", ch.id, exc)
        ch.delivery_mode = "long_poll"
        ch.webhook_secret = None
        ch.webhook_url = None
        await db.commit()
        await db.refresh(ch)
        return _serialize(ch)

    # mode == "webhook"
    if not settings.public_base_url:
        raise HTTPException(
            status_code=400,
            detail="Set public base URL in Settings → System before enabling webhook delivery.",
        )

    new_secret = secrets.token_hex(32)
    url = _webhook_url_for(ch.id)
    assert url is not None  # guarded by public_base_url check above
    try:
        await TelegramAPI.set_webhook(
            token,
            url=url,
            secret=new_secret,
            allowed_updates=["message", "callback_query"],
        )
    except TelegramError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.description or "Telegram rejected the webhook URL",
        )

    ch.delivery_mode = "webhook"
    ch.webhook_secret = new_secret
    ch.webhook_url = url
    await db.commit()
    await db.refresh(ch)
    return _serialize(ch)


@router.get("/channels/{channel_id}/webhook-info", response_model=TelegramWebhookInfoResponse)
async def get_webhook_info_route(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Passthrough of Telegram getWebhookInfo + a backend reachability
    probe so the settings UI can render the full diagnostic state
    (URL, pending updates, last error, backend probe)."""
    ch = await _load_channel(channel_id, current_user, db)
    try:
        token = decrypt_secret(ch.bot_token_enc)
    except InvalidToken:
        raise HTTPException(status_code=500, detail="Bot token is unreadable. Replace it and re-pair.")

    try:
        info = await TelegramAPI.get_webhook_info(token)
    except TelegramError as exc:
        raise HTTPException(status_code=400, detail=exc.description or "Telegram getWebhookInfo failed")

    backend_ok, backend_err = await _probe_backend_health()

    return {
        "url": info.get("url") or None,
        "has_custom_certificate": bool(info.get("has_custom_certificate") or False),
        "pending_update_count": int(info.get("pending_update_count") or 0),
        "last_error_date": info.get("last_error_date"),
        "last_error_message": info.get("last_error_message"),
        "ip_address": info.get("ip_address"),
        "max_connections": info.get("max_connections"),
        "backend_reachable": backend_ok,
        "backend_probe_error": backend_err,
    }


@router.post("/channels/{channel_id}/refresh-webhook", response_model=TelegramChannelResponse)
async def refresh_webhook(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-call setWebhook for a channel already in webhook mode. used
    after public_base_url changes so the registered URL catches up.
    Re-uses the existing secret to avoid invalidating in-flight
    deliveries Telegram may be retrying."""
    ch = await _load_channel(channel_id, current_user, db)
    if (ch.delivery_mode or "long_poll") != "webhook":
        raise HTTPException(status_code=400, detail="Channel is not in webhook mode.")
    if not settings.public_base_url:
        raise HTTPException(
            status_code=400,
            detail="Set public base URL in Settings → System before refreshing webhook.",
        )
    try:
        token = decrypt_secret(ch.bot_token_enc)
    except InvalidToken:
        raise HTTPException(status_code=500, detail="Bot token is unreadable. Replace it and re-pair.")

    new_url = _webhook_url_for(ch.id)
    secret = ch.webhook_secret or secrets.token_hex(32)
    try:
        await TelegramAPI.set_webhook(
            token,
            url=new_url,
            secret=secret,
            allowed_updates=["message", "callback_query"],
        )
    except TelegramError as exc:
        raise HTTPException(status_code=400, detail=exc.description or "setWebhook failed")
    ch.webhook_url = new_url
    ch.webhook_secret = secret
    await db.commit()
    await db.refresh(ch)
    return _serialize(ch)


@router.post("/webhook/{channel_id}")
async def telegram_webhook_receiver(
    channel_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Telegram delivers updates here when ``delivery_mode='webhook'``.

    Verifies the shared secret in the
    ``X-Telegram-Bot-Api-Secret-Token`` header with a constant-time
    compare. Returns 200 immediately and processes the update in a
    BackgroundTask so we never block more than ~100ms (Telegram
    retries on slow webhook responses, which would cascade into
    duplicate deliveries).

    Rate-limited to 50 updates/sec per channel as an abuse guard.
    """
    # Local imports keep the routes module's top-level import set
    # focused. Request/BackgroundTasks are FastAPI dependencies but
    # we want to avoid a top-level reshuffle.
    import hmac as _hmac

    async with async_session() as db:
        ch = await db.get(TelegramChannel, channel_id)
        if ch is None or not ch.webhook_secret:
            # Don't reveal whether the channel exists. Telegram never
            # exposes 401 to end-users either; a constant 401 makes
            # secret-guessing useless.
            raise HTTPException(status_code=401, detail="unauthorized")
        expected = ch.webhook_secret

    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token") or ""
    if not _hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="unauthorized")

    if not _webhook_allow(str(channel_id)):
        raise HTTPException(status_code=429, detail="webhook rate limit")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="invalid update shape")

    # Defer the actual dispatch so Telegram gets the 200 OK fast.
    from services.notify.telegram_poller import handle_update as _handle_update

    background_tasks.add_task(_handle_update, str(channel_id), update)
    return {"ok": True}


@router.post("/channels/{channel_id}/test-webhook-delivery")
async def test_webhook_delivery(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-test that loops back through our own public URL.

    We HTTP-GET ``public_base_url + /api/health`` from this backend
    to confirm the public URL is reachable from outside. setWebhook
    can succeed against an unreachable URL (Telegram doesn't probe)
    so this round-trip is the only honest reachability check we can
    do without staging a real /start.
    """
    ch = await _load_channel(channel_id, current_user, db)
    if (ch.delivery_mode or "long_poll") != "webhook":
        raise HTTPException(status_code=400, detail="Channel is not in webhook mode.")
    ok, err = await _probe_backend_health()
    return {"ok": bool(ok), "error": err, "probed_url": (settings.public_base_url or "").rstrip("/") + "/api/health" if settings.public_base_url else None}


def _escape_html(value: str) -> str:
    """Minimal HTML escape for Telegram HTML parse mode."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Re-exported for use by the action executor in services/events/actions.py
__all__ = ["router", "_escape_html"]
