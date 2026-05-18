"""Background long-poll workers for paired Telegram bots.

One asyncio task per enabled bot token. The manager scans the
``telegram_channels`` table every 30 seconds and starts, stops, or
restarts tasks to track the desired state. Each worker calls
``getUpdates`` with a 25 second long-poll, persists its update offset
cursor in Redis so restarts resume cleanly, and routes incoming
messages.

Phase 1 behaviour. only ``/start <nonce>`` and ``/pair <nonce>`` are
acted upon. The nonce is looked up in Redis under
``nurby:tg_pair:<nonce>``. When valid, the channel row gets the chat
binding written and the worker DMs a confirmation back. All other
updates are logged and dropped.

Phase 2+ will dispatch ``callback_query`` updates to an ack pipeline
and gate inline keyboards.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select

from shared.config import settings
from shared.crypto import InvalidToken, decrypt_secret
from shared.database import async_session
from shared.models import TelegramChannel

from services.notify.telegram import TelegramAPI, TelegramError

logger = logging.getLogger("nurby.notify.telegram_poller")

_OFFSET_KEY_PREFIX = "nurby:tg_offset:"
_PAIR_KEY_PREFIX = "nurby:tg_pair:"


def offset_key(channel_id) -> str:
    return f"{_OFFSET_KEY_PREFIX}{channel_id}"


def pair_key(nonce: str) -> str:
    return f"{_PAIR_KEY_PREFIX}{nonce}"


class TelegramPollerManager:
    """Owns one long-poll task per enabled, token-bearing channel.

    Tasks are keyed by channel id. The manager re-scans the DB on a
    fixed cadence so add/remove/rename/token-rotate all eventually
    converge without explicit signalling.
    """

    REFRESH_SECONDS = 30

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        # Tracks (token, enabled) so we can detect token rotation.
        self._signatures: dict[str, tuple[str, bool]] = {}
        self._stop = asyncio.Event()
        self._redis: aioredis.Redis | None = None

    async def _redis_client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    def stop(self) -> None:
        self._stop.set()
        for task in self._tasks.values():
            task.cancel()

    async def run(self) -> None:
        """Main supervisor loop. Cancellation-safe."""
        try:
            while not self._stop.is_set():
                try:
                    await self._reconcile()
                except Exception:
                    logger.exception("telegram poller reconcile failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.REFRESH_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            for task in list(self._tasks.values()):
                task.cancel()
            # Allow tasks to wind down so cancellation doesn't leak
            for task in list(self._tasks.values()):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            self._tasks.clear()
            if self._redis is not None:
                try:
                    await self._redis.aclose()
                except Exception:
                    pass

    async def _reconcile(self) -> None:
        """Bring the running task set in line with the DB."""
        desired: dict[str, tuple[str, bool]] = {}
        async with async_session() as db:
            result = await db.execute(select(TelegramChannel))
            for ch in result.scalars().all():
                if not ch.enabled:
                    continue
                try:
                    token = decrypt_secret(ch.bot_token_enc)
                except InvalidToken:
                    logger.warning(
                        "Telegram channel %s has unreadable token (jwt_secret rotated?)", ch.id
                    )
                    continue
                desired[str(ch.id)] = (token, ch.enabled)

        # Stop tasks whose channel was disabled, deleted, or whose token rotated.
        for channel_id, sig in list(self._signatures.items()):
            new_sig = desired.get(channel_id)
            if new_sig is None or new_sig != sig:
                task = self._tasks.pop(channel_id, None)
                if task is not None:
                    task.cancel()
                self._signatures.pop(channel_id, None)

        # Start tasks for newly enabled / new channels.
        for channel_id, sig in desired.items():
            if channel_id in self._tasks and not self._tasks[channel_id].done():
                continue
            token, _enabled = sig
            self._signatures[channel_id] = sig
            self._tasks[channel_id] = asyncio.create_task(
                self._worker(channel_id, token), name=f"tg-poller-{channel_id[:8]}"
            )

    async def _worker(self, channel_id: str, token: str) -> None:
        """Long-poll loop for a single bot."""
        redis = await self._redis_client()
        try:
            raw = await redis.get(offset_key(channel_id))
            offset = int(raw) + 1 if raw else None
        except Exception:
            offset = None

        logger.info("telegram poller starting for channel=%s", channel_id)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                updates = await TelegramAPI.get_updates(
                    token,
                    offset=offset,
                    timeout=25,
                    allowed_updates=["message"],
                )
                backoff = 1.0
                for update in updates:
                    try:
                        await self._handle_update(channel_id, update)
                    except Exception:
                        logger.exception(
                            "telegram poller failed handling update channel=%s", channel_id
                        )
                    uid = int(update.get("update_id", 0))
                    if uid:
                        offset = uid + 1
                        try:
                            await redis.set(offset_key(channel_id), str(uid))
                        except Exception:
                            pass
            except TelegramError as exc:
                if exc.error_code == 409:
                    # Another instance is polling, or webhook is set. Back off hard.
                    logger.warning(
                        "telegram channel=%s conflict (409). %s. backing off",
                        channel_id, exc.description,
                    )
                    await asyncio.sleep(60)
                    continue
                logger.warning(
                    "telegram channel=%s getUpdates error %s. %s",
                    channel_id, exc.error_code, exc.description,
                )
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("telegram channel=%s unexpected poller error", channel_id)
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)

    async def _handle_update(self, channel_id: str, update: dict) -> None:
        message = update.get("message")
        if not message:
            return
        text = (message.get("text") or "").strip()
        if not text:
            return
        # Match `/start <nonce>` or `/pair <nonce>`. Strip optional bot
        # suffix like `/start@MyBot`.
        parts = text.split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].split("@", 1)[0].lower()
        if cmd not in ("/start", "/pair"):
            logger.debug("telegram channel=%s ignoring text. %r", channel_id, text[:80])
            return
        nonce = parts[1].strip() if len(parts) > 1 else ""
        if not nonce:
            return
        await self._try_pair(channel_id, message, nonce)

    async def _try_pair(self, channel_id: str, message: dict, nonce: str) -> None:
        redis = await self._redis_client()
        try:
            bound_channel = await redis.get(pair_key(nonce))
        except Exception:
            bound_channel = None
        if not bound_channel:
            logger.info("telegram pair nonce=%s expired or unknown", nonce[:8])
            return
        if bound_channel != channel_id:
            logger.warning(
                "telegram pair nonce=%s issued for channel=%s but received on channel=%s",
                nonce[:8], bound_channel, channel_id,
            )
            return

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        chat_type = chat.get("type") or "private"

        async with async_session() as db:
            ch = await db.get(TelegramChannel, _to_uuid(channel_id))
            if ch is None:
                return
            ch.chat_id = str(chat_id)
            ch.chat_title = str(chat_title)[:255] if chat_title else None
            ch.chat_type = str(chat_type)[:16]
            ch.paired_at = datetime.now(timezone.utc)
            ch.last_error = None
            await db.commit()
            try:
                token = decrypt_secret(ch.bot_token_enc)
            except InvalidToken:
                token = None

        try:
            await redis.delete(pair_key(nonce))
        except Exception:
            pass

        if token:
            try:
                await TelegramAPI.send_message(
                    token,
                    chat_id,
                    "Paired with Nurby ✓\nThis chat will receive alerts from rules using this channel.",
                )
            except TelegramError as exc:
                logger.warning("telegram pair confirm failed channel=%s. %s", channel_id, exc)

        logger.info("telegram channel=%s paired with chat=%s (%s)", channel_id, chat_id, chat_type)


def _to_uuid(value):
    """Best-effort coerce a string id from Redis/handler payloads to a UUID."""
    import uuid

    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


# Convenience helper used by routes when issuing a fresh pair nonce.
async def store_pair_nonce(redis: aioredis.Redis, nonce: str, channel_id: str, ttl: int = 300) -> None:
    await redis.set(pair_key(nonce), channel_id, ex=ttl)


# Convenience helper. JSON-safe lookup used by tests.
def encode_for_test(update: dict) -> str:  # pragma: no cover. trivial
    return json.dumps(update)
