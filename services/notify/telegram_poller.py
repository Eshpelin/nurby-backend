"""Background long-poll workers for paired Telegram bots.

One asyncio task per enabled bot token. The manager scans the
``telegram_channels`` table every 30 seconds and starts, stops, or
restarts tasks to track the desired state. Each worker calls
``getUpdates`` with a 25 second long-poll, persists its update offset
cursor in Redis so restarts resume cleanly, and routes incoming
messages.

Phase 1 routed ``/start <nonce>`` and ``/pair <nonce>`` only. Phase 2
extends the allowed_updates filter to include ``callback_query`` and
dispatches inline-button presses into a small handler set
(``ack`` | ``mute_event`` | ``snooze_rule`` | ``open``). Each handler
acknowledges the callback with :meth:`TelegramAPI.answer_callback_query`
within the 15 second Telegram window even when the internal DB write
fails, so the user's spinner always clears.

Phase 3+ will replace the long-poller with a webhook receiver. The
update payload shape is the same, so the handler set defined here will
keep working without modification.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select

from services.notify.telegram import (
    TelegramAPI,
    TelegramError,
    get_message_index,
    send_message_guarded,
    set_message_reaction,
    verify_callback,
)
from shared.config import settings
from shared.crypto import InvalidToken, decrypt_secret
from shared.database import async_session
from shared.models import (
    BodyCluster,
    Event,
    EventNote,
    FaceCluster,
    Person,
    Rule,
    TelegramChannel,
    TelegramDialog,
    User,
)

logger = logging.getLogger("nurby.notify.telegram_poller")

_OFFSET_KEY_PREFIX = "nurby:tg_offset:"
_PAIR_KEY_PREFIX = "nurby:tg_pair:"

# Phase 3. per-channel asyncio.Lock so concurrent webhook deliveries
# serialize chat-state mutations within a single channel without
# blocking unrelated channels. The long-poll worker is single-threaded
# per channel so the lock is a no-op there, but the webhook route
# may invoke handle_update from many concurrent BackgroundTasks.
_channel_locks: dict[str, asyncio.Lock] = {}


def _channel_lock(channel_id: str) -> asyncio.Lock:
    lock = _channel_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[channel_id] = lock
    return lock

# Bot menu hints. These don't wire actual slash command handlers
# (Phase 2 only needs the inline buttons). Showing them in the menu
# helps users discover the actions when buttons aren't visible in the
# chat history.
_BOT_COMMANDS = [
    ("ack", "Acknowledge latest alert"),
    ("mute", "Mute 10 minutes"),
    ("snooze", "Snooze rule 1 hour"),
    # Phase 4. Backup path for the reply-to-add-note feature.
    # Used when Redis lost the original alert's message index
    # (TTL expired or restart with a different Redis).
    ("notes", "Add a note to an event. /notes <event_id> <text>"),
]

# Allowed callback actions. Documented as an extension point. Phase 4
# household sharing + face-cluster naming will add new variants like
# ``name_cluster``; the verify -> dispatch path here keys off this
# tuple so a stray future deployment cannot replay an unknown action.
_CALLBACK_ACTIONS = (
    "ack",
    "mute_event",
    "snooze_rule",
    "open",
    # Phase 4 cluster naming + ask-yes-no.
    "open_cluster",
    "name_cluster_telegram",
    "yn_yes",
    "yn_no",
)

# Hard cap on free-text annotation length to match the Telegram message
# size cap. Anything longer gets truncated + the user is told.
_NOTE_MAX_CHARS = 4096


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
        # Tracks (token, enabled, delivery_mode) so we detect token
        # rotation OR a delivery-mode flip between long_poll/webhook.
        self._signatures: dict[str, tuple[str, bool, str]] = {}
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
        # Phase 3. signature also tracks delivery_mode so a flip
        # between long_poll <-> webhook restarts/stops the task.
        desired: dict[str, tuple[str, bool, str]] = {}
        async with async_session() as db:
            result = await db.execute(select(TelegramChannel))
            for ch in result.scalars().all():
                if not ch.enabled:
                    continue
                # Phase 3. webhook-mode channels are owned by the
                # /api/telegram/webhook/{id} route. skip them here so
                # we never compete with Telegram's webhook delivery.
                if (ch.delivery_mode or "long_poll") != "long_poll":
                    continue
                try:
                    token = decrypt_secret(ch.bot_token_enc)
                except InvalidToken:
                    logger.warning(
                        "Telegram channel %s has unreadable token (jwt_secret rotated?)", ch.id
                    )
                    continue
                desired[str(ch.id)] = (token, ch.enabled, ch.delivery_mode or "long_poll")

        # Stop tasks whose channel was disabled, deleted, switched to
        # webhook, or whose token rotated.
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
            token, _enabled, _mode = sig
            self._signatures[channel_id] = sig
            self._tasks[channel_id] = asyncio.create_task(
                self._worker(channel_id, token), name=f"tg-poller-{channel_id[:8]}"
            )

    async def _worker(self, channel_id: str, token: str) -> None:
        """Long-poll loop for a single bot."""
        redis = await self._redis_client()

        # Phase 3. if a webhook was registered in a prior run (or by a
        # sibling process) Telegram refuses getUpdates with 409. Clear
        # any stale registration before starting the poll loop. This
        # avoids the silent-no-updates trap users hit after toggling
        # from webhook back to long-poll while the row already had
        # delivery_mode='long_poll' but Telegram still held the URL.
        try:
            info = await TelegramAPI.get_webhook_info(token)
            if info.get("url"):
                logger.info(
                    "telegram channel=%s clearing stale webhook %s before long-poll",
                    channel_id, info.get("url"),
                )
                await TelegramAPI.delete_webhook(token, drop_pending_updates=False)
        except TelegramError as exc:
            logger.warning(
                "telegram channel=%s could not clear webhook before poll. %s",
                channel_id, exc,
            )
        except Exception:
            logger.debug(
                "telegram channel=%s getWebhookInfo raised", channel_id, exc_info=True,
            )

        try:
            raw = await redis.get(offset_key(channel_id))
            offset = int(raw) + 1 if raw else None
        except Exception:
            offset = None

        logger.info("telegram poller starting for channel=%s", channel_id)
        # Best-effort bot menu setup. Non-fatal because not every
        # Telegram bot supports setMyCommands the same way (e.g. shared
        # group bots can return BAD_REQUEST when called from a worker
        # that didn't create them).
        try:
            await TelegramAPI.set_my_commands(token, _BOT_COMMANDS)
        except TelegramError as exc:
            logger.debug(
                "telegram channel=%s setMyCommands skipped. %s", channel_id, exc.description,
            )
        except Exception:
            logger.debug("telegram channel=%s setMyCommands raised", channel_id, exc_info=True)

        backoff = 1.0
        while not self._stop.is_set():
            try:
                updates = await TelegramAPI.get_updates(
                    token,
                    offset=offset,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
                backoff = 1.0
                for update in updates:
                    try:
                        await self.handle_update(channel_id, update)
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

    async def handle_update(self, channel_id: str, update: dict) -> None:
        """Public entry point used by both the long-poll worker and the
        Phase 3 webhook route. Wraps the dispatch in a per-channel
        :class:`asyncio.Lock` so concurrent webhook deliveries serialize
        chat-state mutations within a single channel without blocking
        unrelated channels.
        """
        async with _channel_lock(channel_id):
            await self._handle_update(channel_id, update)

    async def _handle_update(self, channel_id: str, update: dict) -> None:
        callback = update.get("callback_query")
        if callback:
            await self._handle_callback_query(channel_id, callback)
            return
        message = update.get("message")
        if not message:
            return

        # Phase 4. Reply-to-add-note. A user replying to a Telegram
        # alert attaches free text to the originating event. Must be
        # handled before the generic text branch so a reply that
        # happens to start with "/" is still treated as a note.
        reply_to = message.get("reply_to_message")
        if reply_to and reply_to.get("message_id"):
            handled = await self._handle_reply(channel_id, message, reply_to)
            if handled:
                return

        text = (message.get("text") or "").strip()
        if not text:
            # Phase 4 edge case. Replying with a photo / sticker is
            # only meaningful as a note if it carries a reply_to we
            # could not match above; otherwise drop silently.
            return

        # Match slash commands first so `/start <nonce>`, `/pair
        # <nonce>` and the new `/notes <event_id> <text>` keep working
        # without colliding with dialog text input.
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower() if parts else ""

        if cmd in ("/start", "/pair"):
            nonce = parts[1].strip() if len(parts) > 1 else ""
            if not nonce:
                return
            await self._try_pair(channel_id, message, nonce)
            return

        if cmd == "/notes":
            tail = parts[1].strip() if len(parts) > 1 else ""
            await self._handle_notes_command(channel_id, message, tail)
            return

        # Phase 4. Dialog text input. The cluster-naming flow parks an
        # awaiting='name_input' row; the user's next plain text reply
        # in the same chat lands here.
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            consumed = await self._handle_dialog_text(channel_id, str(chat_id), message, text)
            if consumed:
                return

        logger.debug("telegram channel=%s ignoring text. %r", channel_id, text[:80])

    async def _handle_callback_query(self, channel_id: str, callback: dict) -> None:
        """Dispatch an inline-button press.

        Always answers the callback within the 15s Telegram window so
        the user's spinner clears, even if internal DB work fails.
        Returns silently on any verification or ownership failure; the
        user sees a generic "expired" alert in those cases.

        Concurrency. when two users press Ack on a forwarded message,
        the second press finds ``acked_at`` already set and answers
        with "Already acknowledged by <name>" without overwriting the
        first. mute_event + snooze_rule are last-writer-wins by
        design; the user intent is "extend the silence", not "reserve
        the first slot".
        """
        cb_id = str(callback.get("id") or "")
        if not cb_id:
            return
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")

        # Load the bot token + verify the inbound chat matches the
        # channel binding. A leaked button replayed in a different
        # chat (e.g. user forwarded the message to a public group) is
        # rejected here even before HMAC verification.
        async with async_session() as db:
            ch = await db.get(TelegramChannel, _to_uuid(channel_id))
            if ch is None or not ch.bot_token_enc:
                return
            if ch.chat_id and chat_id is not None and str(chat_id) != str(ch.chat_id):
                logger.warning(
                    "telegram callback rejected. channel=%s chat mismatch (%s vs %s)",
                    channel_id, chat_id, ch.chat_id,
                )
                try:
                    token_only = decrypt_secret(ch.bot_token_enc)
                    await _safe_answer(
                        token_only, cb_id,
                        text="This alert is not bound to this chat.",
                        show_alert=True,
                    )
                except InvalidToken:
                    pass
                return
            try:
                token = decrypt_secret(ch.bot_token_enc)
            except InvalidToken:
                return
            owner_user_id = ch.user_id

        payload_str = verify_callback(data)
        if not payload_str:
            await _safe_answer(
                token, cb_id,
                text="This alert is no longer valid. Open Nurby on the web to acknowledge.",
                show_alert=True,
            )
            return
        try:
            payload = json.loads(payload_str)
        except Exception:
            await _safe_answer(token, cb_id, text="Malformed alert payload.", show_alert=True)
            return

        action = str(payload.get("a") or "")
        if action not in _CALLBACK_ACTIONS:
            await _safe_answer(token, cb_id, text="Unknown action.", show_alert=True)
            return

        if action == "open":
            # open is delivered as a URL button by the action executor
            # so reaching the callback path means a stale button or a
            # client that downgraded the URL. No state change.
            await _safe_answer(token, cb_id, text="Opening Nurby…")
            return

        event_id_raw = payload.get("e")
        rule_id_raw = payload.get("r")
        try:
            event_id = _to_uuid(event_id_raw) if event_id_raw else None
        except Exception:
            event_id = None
        try:
            rule_id = _to_uuid(rule_id_raw) if rule_id_raw else None
        except Exception:
            rule_id = None
        duration = int(payload.get("d") or 0)

        # Hand off to the per-action branch. Each branch is wrapped in
        # try/except so we still answerCallbackQuery on errors.
        text_reply: str | None = None
        show_alert = False
        new_markup: dict | None = None
        try:
            if action == "ack":
                text_reply, new_markup = await self._do_ack(event_id, owner_user_id)
            elif action == "mute_event":
                text_reply, new_markup = await self._do_mute_event(event_id, duration or 600)
            elif action == "snooze_rule":
                text_reply, new_markup = await self._do_snooze_rule(rule_id, duration or 3600)
            elif action == "open_cluster":
                # Phase 4. "Same as <Name>" + Skip variants both ride
                # this action. Payload carries cluster id under 'c',
                # cluster kind under 'k', and either a person id 'p'
                # or the sentinel 'skip'.
                cluster_id_raw = payload.get("c")
                cluster_kind = str(payload.get("k") or "")
                person_choice = payload.get("p")
                text_reply, new_markup = await self._do_link_cluster(
                    cluster_id_raw, cluster_kind, person_choice,
                )
            elif action == "name_cluster_telegram":
                # Phase 4. "Name as..." button. Switches the open
                # dialog row's awaiting state so the user's next text
                # reply lands in ``_handle_dialog_text``.
                cluster_id_raw = payload.get("c")
                cluster_kind = str(payload.get("k") or "")
                text_reply, new_markup = await self._do_open_name_dialog(
                    channel_id, chat_id, message_id, cluster_id_raw, cluster_kind,
                )
            elif action in ("yn_yes", "yn_no"):
                # Phase 4 stretch. Capture the user's yes/no answer as
                # a note. Follow-up rule action routing is intentionally
                # not wired here. see commit message + summary for the
                # hand-off comment.
                text_reply, new_markup = await self._do_record_yn(
                    event_id, action == "yn_yes",
                )
        except Exception:
            logger.exception("telegram callback handler failed channel=%s action=%s", channel_id, action)
            text_reply = "Could not update Nurby. Try again from the web UI."
            show_alert = True

        # Step 1. Clear the spinner first (must be within 15s).
        await _safe_answer(token, cb_id, text=text_reply, show_alert=show_alert)

        # Step 2. Best-effort markup rewrite so the buttons reflect
        # the new state. Missing message_id (e.g. send failed earlier)
        # silently skips this step.
        if new_markup is not None and chat_id is not None and message_id is not None:
            try:
                await TelegramAPI.edit_message_reply_markup(
                    token, chat_id, int(message_id), new_markup,
                )
            except TelegramError as exc:
                logger.debug(
                    "telegram editMessageReplyMarkup failed channel=%s. %s",
                    channel_id, exc.description,
                )
            except Exception:
                logger.debug(
                    "telegram editMessageReplyMarkup raised channel=%s", channel_id, exc_info=True,
                )

    async def _do_ack(self, event_id, owner_user_id) -> tuple[str, dict]:
        """Set the ack triad on the event. If already acked, reply with
        the prior acker's name instead of overwriting (second-presser
        gets feedback)."""
        if event_id is None:
            return ("This alert is missing an event id.", _markup_disabled("⚠ Invalid"))
        async with async_session() as db:
            event = await db.get(Event, event_id)
            if event is None:
                return ("Event no longer exists.", _markup_disabled("⚠ Gone"))
            if event.acked_at is not None:
                prior_name = await _user_display(db, event.acked_by_user_id)
                return (
                    f"Already acknowledged by {prior_name}.",
                    _markup_disabled(f"✓ Acknowledged by {prior_name}"),
                )
            event.acked_at = datetime.now(timezone.utc)
            event.acked_by_user_id = owner_user_id
            event.acked_via = "telegram"
            # Mirror to the legacy column so existing dashboards keep
            # showing the acknowledgement.
            event.acknowledged_at = event.acked_at
            await db.commit()
            ack_name = await _user_display(db, owner_user_id)
        return (
            f"Acknowledged by {ack_name}.",
            _markup_disabled(f"✓ Acknowledged by {ack_name}"),
        )

    async def _do_mute_event(self, event_id, duration_seconds: int) -> tuple[str, dict]:
        if event_id is None:
            return ("This alert is missing an event id.", _markup_disabled("⚠ Invalid"))
        until = datetime.now(timezone.utc) + _timedelta_seconds(duration_seconds)
        async with async_session() as db:
            event = await db.get(Event, event_id)
            if event is None:
                return ("Event no longer exists.", _markup_disabled("⚠ Gone"))
            event.muted_until = until
            await db.commit()
        when = until.astimezone().strftime("%H:%M")
        return (f"Muted until {when}.", _markup_disabled(f"🔕 Muted until {when}"))

    async def _do_snooze_rule(self, rule_id, duration_seconds: int) -> tuple[str, dict]:
        if rule_id is None:
            return ("This alert is missing a rule id.", _markup_disabled("⚠ Invalid"))
        until = datetime.now(timezone.utc) + _timedelta_seconds(duration_seconds)
        async with async_session() as db:
            rule = await db.get(Rule, rule_id)
            if rule is None:
                return ("Rule no longer exists.", _markup_disabled("⚠ Gone"))
            rule.snoozed_until = until
            await db.commit()
        when = until.astimezone().strftime("%H:%M")
        return (
            f"Rule snoozed until {when}.",
            _markup_disabled(f"💤 Rule snoozed until {when}"),
        )

    # ------------------------------------------------------------------
    # Phase 4. reply-to-add-note + dialog text + slash /notes backup.
    # ------------------------------------------------------------------

    async def _handle_reply(self, channel_id: str, message: dict, reply_to: dict) -> bool:
        """Attach a free-text reply as an EventNote.

        Resolves the originating Event via the Redis message-index.
        Edge cases handled.
        * Reply to a non-alert message (e.g. a cluster prompt). we
          treat the reply as dialog text input by returning False so
          ``_handle_dialog_text`` gets the next pass.
        * Index expired. tell the user about ``/notes`` so they can
          still annotate manually.
        * Different chat than the original alert. reject so a
          forwarded-alert reply in a private chat doesn't leak the
          note into the wrong household event.
        * Non-text reply (photo, sticker). ask politely for text.
        """
        reply_msg_id = int(reply_to.get("message_id") or 0)
        if not reply_msg_id:
            return False

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return False

        channel_uuid = _to_uuid(channel_id)
        index = await get_message_index(channel_uuid, reply_msg_id)
        if index is None:
            # Could not resolve the original message. Surface the
            # backup path so the user keeps moving.
            await self._send_followup(
                channel_id, chat_id,
                "I can't link this reply to an alert (it may have expired). "
                "Use <code>/notes &lt;event_id&gt; your text</code> to attach manually.",
            )
            return True

        kind = index.get("kind")
        if kind == "cluster_naming":
            # Cluster-naming prompt reply. Fall through so the dialog
            # state machine picks it up rather than the note path.
            return False
        if kind != "alert":
            return False

        # Validate the chat matches the channel's bound chat. This is
        # the "forwarded alert" edge case.
        async with async_session() as db:
            ch = await db.get(TelegramChannel, channel_uuid)
            if ch is None:
                return True
            if ch.chat_id and str(chat_id) != str(ch.chat_id):
                try:
                    token_only = decrypt_secret(ch.bot_token_enc)
                    await TelegramAPI.send_message(
                        token_only, chat_id,
                        f"Replies must be in the original chat <i>{(ch.chat_title or 'Nurby')[:64]}</i>.",
                        parse_mode="HTML",
                    )
                except (InvalidToken, TelegramError):
                    pass
                return True

        raw_text = message.get("text") or message.get("caption") or ""
        text_value = raw_text.strip()
        if not text_value:
            await self._send_followup(
                channel_id, chat_id,
                "I can only attach text notes. Please reply to the alert with a short text.",
            )
            return True

        truncated = False
        if len(text_value) > _NOTE_MAX_CHARS:
            text_value = text_value[:_NOTE_MAX_CHARS]
            truncated = True

        event_id_raw = index.get("event_id")
        try:
            event_id = _to_uuid(event_id_raw) if event_id_raw else None
        except Exception:
            event_id = None
        if event_id is None:
            await self._send_followup(
                channel_id, chat_id, "I can't find the event this reply belongs to.",
            )
            return True

        from_user = message.get("from") or {}
        from_msg_id = int(message.get("message_id") or 0) or None

        async with async_session() as db:
            event = await db.get(Event, event_id)
            if event is None:
                await self._send_followup(
                    channel_id, chat_id, "That alert's event no longer exists.",
                )
                return True
            # Author resolution. Owner-replied alerts attribute to the
            # channel owner. We don't have a real Telegram->User table
            # so the channel owner is the best signal for the
            # household member who replied.
            ch = await db.get(TelegramChannel, channel_uuid)
            author_user_id = ch.user_id if ch is not None else None

            note = EventNote(
                event_id=event_id,
                author_user_id=author_user_id,
                source="telegram",
                text=text_value,
                telegram_message_id=from_msg_id,
            )
            db.add(note)
            await db.commit()

        # Confirm to the user. A reaction emoji on the original
        # reply when available + a short "Noted." text fallback so
        # bots without reactions still feel responsive.
        try:
            token = await self._channel_token(channel_id)
            if token and from_msg_id:
                await set_message_reaction(token, chat_id, from_msg_id, "\U0001F44D")
        except Exception:
            logger.debug("set_message_reaction raised", exc_info=True)

        suffix = " (truncated to 4096 chars)" if truncated else ""
        await self._send_followup(
            channel_id, chat_id, f"Noted.{suffix}",
        )
        return True

    async def _handle_notes_command(
        self, channel_id: str, message: dict, tail: str,
    ) -> None:
        """Backup path for attaching a note when the reply index is gone.

        Usage. ``/notes <event_id> <text>``. The event_id may be any
        UUID-shaped string we already minted; users typically copy it
        from the web UI's event detail page.
        """
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        if not tail:
            await self._send_followup(
                channel_id, chat_id,
                "Usage. <code>/notes &lt;event_id&gt; your text</code>",
            )
            return
        parts = tail.split(maxsplit=1)
        event_id_raw = parts[0]
        text_value = (parts[1] if len(parts) > 1 else "").strip()
        if not text_value:
            await self._send_followup(
                channel_id, chat_id,
                "Add the note text after the event id. e.g. <code>/notes 1234... false alarm</code>",
            )
            return
        try:
            event_id = _to_uuid(event_id_raw)
        except Exception:
            await self._send_followup(
                channel_id, chat_id, "That event id is not a valid UUID.",
            )
            return

        truncated = len(text_value) > _NOTE_MAX_CHARS
        if truncated:
            text_value = text_value[:_NOTE_MAX_CHARS]

        channel_uuid = _to_uuid(channel_id)
        async with async_session() as db:
            event = await db.get(Event, event_id)
            if event is None:
                await self._send_followup(
                    channel_id, chat_id, "No event with that id exists.",
                )
                return
            ch = await db.get(TelegramChannel, channel_uuid)
            author_user_id = ch.user_id if ch is not None else None
            note = EventNote(
                event_id=event_id,
                author_user_id=author_user_id,
                source="telegram",
                text=text_value,
                telegram_message_id=int(message.get("message_id") or 0) or None,
            )
            db.add(note)
            await db.commit()

        suffix = " (truncated to 4096 chars)" if truncated else ""
        await self._send_followup(
            channel_id, chat_id, f"Note attached to event.{suffix}",
        )

    async def _handle_dialog_text(
        self, channel_id: str, chat_id: str, message: dict, text: str,
    ) -> bool:
        """Consume a plain text message as input for an open dialog.

        Returns True when the message landed on a dialog (whether the
        downstream action succeeded or not). False when no dialog was
        open so the outer dispatcher can move on.
        """
        from datetime import datetime, timezone

        channel_uuid = _to_uuid(channel_id)
        now = datetime.now(timezone.utc)
        async with async_session() as db:
            result = await db.execute(
                select(TelegramDialog)
                .where(TelegramDialog.channel_id == channel_uuid)
                .where(TelegramDialog.chat_id == str(chat_id))
                .where(TelegramDialog.awaiting == "name_input")
                .order_by(TelegramDialog.created_at.desc())
                .limit(1)
            )
            dialog = result.scalars().first()
            if dialog is None:
                return False
            if dialog.expires_at <= now:
                # Stale dialog. Clear it and tell the user.
                await db.delete(dialog)
                await db.commit()
                await self._send_followup(
                    channel_id, chat_id,
                    "That naming prompt expired. Tap the photo's buttons to start again.",
                )
                return True

            ctx = dialog.context or {}
            cluster_kind = str(ctx.get("kind") or "")
            cluster_id_raw = ctx.get("cluster_id")
            try:
                cluster_id = _to_uuid(cluster_id_raw) if cluster_id_raw else None
            except Exception:
                cluster_id = None
            if cluster_id is None or cluster_kind not in ("face", "body"):
                await db.delete(dialog)
                await db.commit()
                return True

            display_name = text.strip()[:255]
            if not display_name:
                await self._send_followup(
                    channel_id, chat_id,
                    "Please reply with a non-empty name, or tap Skip.",
                )
                return True

            # Uniqueness check. Reuse persons route helper for the
            # friendly 409 message.
            try:
                from services.api.routes.persons import _ensure_unique_display_name
                await _ensure_unique_display_name(db, display_name)
            except Exception as exc:
                detail = getattr(exc, "detail", None) or str(exc)
                await self._send_followup(
                    channel_id, chat_id,
                    f"Could not save that name. {detail[:200]}",
                )
                return True

            # Mutate cluster + new Person under the channel lock so
            # two concurrent presses on the same prompt don't double-
            # create persons. The lock is already held by the outer
            # ``handle_update`` so this DB section is serialized.
            if cluster_kind == "face":
                cluster = await db.get(FaceCluster, cluster_id)
            else:
                cluster = await db.get(BodyCluster, cluster_id)
            if cluster is None:
                await db.delete(dialog)
                await db.commit()
                await self._send_followup(
                    channel_id, chat_id,
                    "That cluster was removed before naming completed.",
                )
                return True
            if cluster.status != "pending":
                await db.delete(dialog)
                await db.commit()
                await self._send_followup(
                    channel_id, chat_id,
                    "Already named by someone else. Future sightings will use the existing label.",
                )
                return True

            person = Person(
                display_name=display_name,
                consent_given=True,
                photo_path=cluster.sample_thumbnail_path,
            )
            db.add(person)
            await db.flush()
            cluster.person_id = person.id
            cluster.status = "named"
            if cluster_kind == "body":
                cluster.confidence = "confirmed"
            await db.delete(dialog)
            await db.commit()

        await self._send_followup(
            channel_id, chat_id,
            f"Saved as <b>{display_name}</b>. Future sightings will be labeled.",
        )
        return True

    async def _channel_token(self, channel_id: str) -> str | None:
        async with async_session() as db:
            ch = await db.get(TelegramChannel, _to_uuid(channel_id))
            if ch is None:
                return None
            try:
                return decrypt_secret(ch.bot_token_enc)
            except InvalidToken:
                return None

    async def _send_followup(self, channel_id: str, chat_id, text: str) -> None:
        """Send a short confirmation back into the chat through the
        guarded send wrapper so it respects the per-chat rate limit.
        Errors are logged but never bubble up to the caller."""
        async with async_session() as db:
            ch = await db.get(TelegramChannel, _to_uuid(channel_id))
            if ch is None:
                return
            try:
                token = decrypt_secret(ch.bot_token_enc)
            except InvalidToken:
                return
            qps = float(ch.rate_limit_per_chat_qps or 1.0)
            burst = int(ch.rate_limit_per_chat_burst or 3)
        try:
            await send_message_guarded(
                token=token,
                channel_id=_to_uuid(channel_id),
                chat_id=chat_id,
                text=text,
                qps=qps,
                burst=burst,
                dedupe_window_seconds=0,
                parse_mode="HTML",
            )
        except TelegramError as exc:
            logger.debug("telegram followup send failed. %s", exc)
        except Exception:
            logger.debug("telegram followup send raised", exc_info=True)

    async def _do_link_cluster(
        self, cluster_id_raw, cluster_kind: str, person_choice,
    ) -> tuple[str, dict]:
        """Handle "Same as <Name>" + Skip button presses on a cluster
        naming prompt. ``person_choice`` is either a UUID string or
        the literal 'skip' for the dismissal button."""
        if not cluster_id_raw or cluster_kind not in ("face", "body"):
            return ("Invalid cluster prompt.", _markup_disabled("⚠ Invalid"))
        try:
            cluster_id = _to_uuid(cluster_id_raw)
        except Exception:
            return ("Invalid cluster id.", _markup_disabled("⚠ Invalid"))

        async with async_session() as db:
            if cluster_kind == "face":
                cluster = await db.get(FaceCluster, cluster_id)
            else:
                cluster = await db.get(BodyCluster, cluster_id)
            if cluster is None:
                return ("Cluster no longer exists.", _markup_disabled("⚠ Gone"))
            if cluster.status != "pending":
                # Concurrency. Another presser won the race. Tell the
                # second user but don't overwrite the existing link.
                existing_name = "someone"
                if cluster.person_id is not None:
                    person = await db.get(Person, cluster.person_id)
                    if person is not None:
                        existing_name = person.display_name
                return (
                    f"Already named by {existing_name}.",
                    _markup_disabled(f"✓ {existing_name}"),
                )

            if person_choice == "skip" or person_choice is None:
                cluster.status = "ignored"
                # Best-effort dialog cleanup so a stray text reply
                # doesn't try to name an ignored cluster.
                from sqlalchemy import delete as _delete
                await db.execute(
                    _delete(TelegramDialog).where(
                        TelegramDialog.context.op("->>")("cluster_id") == str(cluster_id)
                    )
                )
                await db.commit()
                return ("OK, ignored for now.", _markup_disabled("Skipped"))

            try:
                person_id = _to_uuid(person_choice)
            except Exception:
                return ("Invalid person id.", _markup_disabled("⚠ Invalid"))
            person = await db.get(Person, person_id)
            if person is None:
                return ("That person no longer exists.", _markup_disabled("⚠ Gone"))

            cluster.person_id = person.id
            cluster.status = "named"
            if cluster_kind == "body":
                cluster.confidence = "confirmed"
            from sqlalchemy import delete as _delete
            await db.execute(
                _delete(TelegramDialog).where(
                    TelegramDialog.context.op("->>")("cluster_id") == str(cluster_id)
                )
            )
            await db.commit()
            link_name = person.display_name

        return (
            f"Linked to {link_name}.",
            _markup_disabled(f"✓ Linked to {link_name}"),
        )

    async def _do_open_name_dialog(
        self,
        channel_id: str,
        chat_id,
        message_id,
        cluster_id_raw,
        cluster_kind: str,
    ) -> tuple[str, dict | None]:
        """User tapped "Name as...". Bump the dialog row's expiry +
        attach the user as the driver so the next text reply lands.
        Returns no markup change because the original photo's buttons
        stay useful (the user may still tap Skip).
        """
        from datetime import datetime, timedelta, timezone

        if not cluster_id_raw or cluster_kind not in ("face", "body"):
            return ("Invalid cluster prompt.", None)
        try:
            cluster_id = _to_uuid(cluster_id_raw)
        except Exception:
            return ("Invalid cluster id.", None)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=15)
        channel_uuid = _to_uuid(channel_id)
        async with async_session() as db:
            result = await db.execute(
                select(TelegramDialog)
                .where(TelegramDialog.channel_id == channel_uuid)
                .where(TelegramDialog.chat_id == str(chat_id))
                .where(TelegramDialog.awaiting == "name_input")
                .order_by(TelegramDialog.created_at.desc())
                .limit(1)
            )
            dialog = result.scalars().first()
            if dialog is None:
                # Initiator never persisted one (e.g. dialog was
                # cleaned up) – recreate so the user's reply is
                # actually consumed.
                dialog = TelegramDialog(
                    channel_id=channel_uuid,
                    chat_id=str(chat_id),
                    kind=f"name_{cluster_kind}_cluster",
                    context={"cluster_id": str(cluster_id), "kind": cluster_kind},
                    awaiting="name_input",
                    last_message_id=int(message_id) if message_id else None,
                    expires_at=expires_at,
                )
                db.add(dialog)
            else:
                dialog.expires_at = expires_at
                dialog.context = {"cluster_id": str(cluster_id), "kind": cluster_kind}
                dialog.kind = f"name_{cluster_kind}_cluster"
            await db.commit()

        return (
            "Reply with the name (next 15 min).",
            None,
        )

    async def _do_record_yn(self, event_id, yes: bool) -> tuple[str, dict]:
        """Phase 4 stretch. Stash a yes/no answer onto the event as a
        note + return a confirmation. Wiring the answer back into a
        rule's follow-up action chain is left for a follow-up commit
        because it requires a Rule.actions descriptor change that
        bleeds into the rule builder UI.
        """
        if event_id is None:
            return ("This prompt is missing an event id.", _markup_disabled("⚠ Invalid"))
        label = "Yes" if yes else "No"
        async with async_session() as db:
            event = await db.get(Event, event_id)
            if event is None:
                return ("Event no longer exists.", _markup_disabled("⚠ Gone"))
            note = EventNote(
                event_id=event_id,
                source="telegram",
                text=label,
            )
            db.add(note)
            await db.commit()
        return (f"Recorded. {label}", _markup_disabled(f"✓ {label}"))

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


def _timedelta_seconds(seconds: int):
    from datetime import timedelta
    # Clamp to a sane window. 1 minute to 24 hours. Anything outside
    # is treated as a bad client and snapped to the default 10 min.
    if seconds < 60 or seconds > 24 * 3600:
        seconds = 600
    return timedelta(seconds=seconds)


def _markup_disabled(label: str) -> dict:
    """Build a one-button inline keyboard with a dead callback_data.
    Telegram requires *some* callback_data on each button; we use a
    constant string that the verify path rejects, so a future press
    triggers the generic "no longer valid" message instead of a stale
    handler."""
    return {"inline_keyboard": [[{"text": label, "callback_data": "__noop__"}]]}


async def _user_display(db, user_id) -> str:
    if user_id is None:
        return "someone"
    try:
        user = await db.get(User, user_id)
    except Exception:
        user = None
    if user is None:
        return "someone"
    return user.display_name or user.email or "someone"


# ── Phase 3. module-level handle_update for the webhook route ──
#
# The webhook receiver in services/api/routes/telegram.py needs to
# dispatch updates through the exact same code path as the long-poll
# worker. We expose a module-level singleton manager + thin shim so
# the receiver doesn't have to care about manager construction.
#
# Phase 4 will add new branches inside _handle_update (text-reply
# handling for note-taking, face-cluster naming). Those will be
# reachable from BOTH the poll worker AND the webhook route without
# touching this shim.

_singleton_manager: TelegramPollerManager | None = None


def _get_singleton_manager() -> TelegramPollerManager:
    global _singleton_manager
    if _singleton_manager is None:
        _singleton_manager = TelegramPollerManager()
    return _singleton_manager


async def handle_update(channel_id: str, update: dict) -> None:
    """Dispatch a single Telegram Update through the same handler set
    as the long-poll worker. Safe to call concurrently for the same
    or different channels."""
    await _get_singleton_manager().handle_update(channel_id, update)


async def _safe_answer(token: str, cb_id: str, text: str | None = None, show_alert: bool = False) -> None:
    """Wrap :meth:`TelegramAPI.answer_callback_query` so any failure is
    swallowed. We must always try to clear the spinner, but a failure
    here must not crash the worker.
    """
    try:
        await TelegramAPI.answer_callback_query(token, cb_id, text=text, show_alert=show_alert)
    except TelegramError as exc:
        logger.debug("telegram answerCallbackQuery error. %s", exc)
    except Exception:
        logger.debug("telegram answerCallbackQuery raised", exc_info=True)
