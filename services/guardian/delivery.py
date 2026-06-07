"""Per-guardian alert delivery.

Takes the recipient links the fan-out decided on and pushes the alert to each
guardian over the channels they have: a paired Telegram bot and/or email. All
delivery is best-effort and isolated per recipient and per channel, so one
broken channel never blocks the rest or raises into the caller.

The decision of *who* to notify lives in services.guardian.alerts. This module
only handles transport.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select

from shared.config import settings
from shared.models import TelegramChannel, User

logger = logging.getLogger("nurby.guardian.delivery")


async def _telegram_channels_for(db, user_id) -> list[TelegramChannel]:
    rows = (
        await db.execute(
            select(TelegramChannel).where(
                TelegramChannel.user_id == user_id,
                TelegramChannel.enabled.is_(True),
            )
        )
    ).scalars().all()
    return [c for c in rows if c.chat_id]


async def _send_telegram(channel: TelegramChannel, text: str, photo_path: str | None) -> bool:
    """Send to one paired channel. Returns True on a real send."""
    from shared.crypto import InvalidToken, decrypt_secret

    try:
        token = decrypt_secret(channel.bot_token_enc)
    except InvalidToken:
        logger.warning("guardian telegram: token undecryptable for channel %s", channel.id)
        return False

    from services.notify.telegram import send_message_guarded, send_photo_guarded

    common = dict(
        token=token,
        channel_id=channel.id,
        chat_id=channel.chat_id,
        qps=channel.rate_limit_per_chat_qps,
        burst=channel.rate_limit_per_chat_burst,
        dedupe_window_seconds=channel.dedupe_window_seconds,
    )
    try:
        if photo_path:
            await send_photo_guarded(
                photo=photo_path, caption=text, media_quality=channel.media_quality, **common
            )
        else:
            await send_message_guarded(text=text, **common)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("guardian telegram send failed for channel %s", channel.id)
        return False


async def _send_email(to: str, subject: str, body: str) -> bool:
    if not settings.smtp_host or not to:
        return False
    from shared.email import send_email

    try:
        await send_email(to=to, subject=subject, body=body)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("guardian email send failed for %s", to)
        return False


async def deliver_to_guardians(
    db,
    recipients: Iterable,
    *,
    message: str,
    subject: str | None = None,
    photo_path: str | None = None,
) -> dict:
    """Push ``message`` to each recipient link's guardian over Telegram + email.

    ``recipients`` are GuardianLink rows (already filtered by the fan-out).
    Returns counts of channels actually delivered. Never raises.
    """
    subject = subject or "Nurby Guardian"
    telegram_sent = 0
    email_sent = 0
    seen_users: set = set()

    for link in recipients:
        uid = getattr(link, "guardian_user_id", None)
        if uid is None or uid in seen_users:
            continue
        seen_users.add(uid)
        try:
            channels = await _telegram_channels_for(db, uid)
        except Exception:  # noqa: BLE001
            channels = []
        for ch in channels:
            if await _send_telegram(ch, message, photo_path):
                telegram_sent += 1
        # Email fallback/parallel path.
        try:
            user = await db.get(User, uid)
        except Exception:  # noqa: BLE001
            user = None
        if user is not None and getattr(user, "email", None):
            if await _send_email(user.email, subject, message):
                email_sent += 1

    return {
        "telegram_sent": telegram_sent,
        "email_sent": email_sent,
        "guardians": len(seen_users),
    }
