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
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user
from shared.config import settings
from shared.crypto import InvalidToken, decrypt_secret, encrypt_secret
from shared.database import get_db
from shared.models import Rule, TelegramChannel, User
from shared.schemas import (
    TelegramChannelCreate,
    TelegramChannelResponse,
    TelegramChannelUpdate,
    TelegramPairInitResponse,
    TelegramTestResponse,
)
from services.notify.telegram import TelegramAPI, TelegramError
from services.notify.telegram_poller import store_pair_nonce

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


def _serialize(ch: TelegramChannel) -> dict:
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
        "created_at": ch.created_at,
    }


async def _load_channel(
    channel_id: uuid.UUID, user: User, db: AsyncSession
) -> TelegramChannel:
    ch = await db.get(TelegramChannel, channel_id)
    if ch is None or ch.user_id != user.id:
        raise HTTPException(status_code=404, detail="Channel not found")
    return ch


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
    return _serialize(ch)


@router.get("/channels", response_model=list[TelegramChannelResponse])
async def list_channels(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TelegramChannel)
        .where(TelegramChannel.user_id == current_user.id)
        .order_by(TelegramChannel.created_at.desc())
    )
    return [_serialize(c) for c in result.scalars().all()]


@router.get("/channels/{channel_id}", response_model=TelegramChannelResponse)
async def get_channel(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await _load_channel(channel_id, current_user, db)
    return _serialize(ch)


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
    await db.commit()
    await db.refresh(ch)
    return _serialize(ch)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await _load_channel(channel_id, current_user, db)
    await db.delete(ch)
    await db.commit()


@router.get("/channels/{channel_id}/rule-usage")
async def channel_rule_usage(
    channel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Count rules whose action chain references this channel. Used by
    the frontend delete confirmation so the user can see the blast
    radius before tearing down a paired chat.
    """
    ch = await _load_channel(channel_id, current_user, db)
    # Rules aren't owner-scoped today (Phase 1 limitation). We scan all
    # rules for any `telegram` action targeting this channel id.
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
    return {"rule_count": count}


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
    ch = await _load_channel(channel_id, current_user, db)
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


def _escape_html(value: str) -> str:
    """Minimal HTML escape for Telegram HTML parse mode."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Re-exported for use by the action executor in services/events/actions.py
__all__ = ["router", "_escape_html"]
