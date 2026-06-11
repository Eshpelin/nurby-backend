"""Phase 4. system-initiated cluster naming over Telegram.

When a new face or body cluster crosses a sightings threshold the
backend DMs a household admin a thumbnail with buttons. Tapping
"Same as <Aisha>" links the cluster to the existing Person inline.
Tapping "Name as..." opens a single-step dialog. the user's next
text reply becomes the new Person's display name.

This module is intentionally split out of ``telegram_poller`` because
the initiator side is invoked from perception code (re-id sweeper, new
cluster paths) while the dialog state machine lives in the poller.
Both share the ``telegram_dialogs`` table and the message-index
helpers in :mod:`services.notify.telegram`.

Picking the recipient channel.
* Prefer any ``shared_with_household=true`` channel.
* Else fall back to the first paired channel in the system.
* Else no-op (we never spam a half-configured deployment).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from services.notify.telegram import (
    CALLBACK_DATA_MAX,
    TelegramError,
    send_message_guarded,
    send_photo_guarded,
    sign_callback,
    store_message_index,
)
from shared.crypto import InvalidToken, decrypt_secret
from shared.database import async_session
from shared.models import (
    BodyCluster,
    FaceCluster,
    Person,
    TelegramChannel,
    TelegramDialog,
)

logger = logging.getLogger("nurby.notify.cluster_naming_telegram")

# How long the in-chat naming dialog stays open before we ignore the
# next text reply. The user can always tap a button on the original
# message to re-open the prompt.
_DIALOG_TTL_SECONDS = 15 * 60


async def _pick_recipient_channel() -> TelegramChannel | None:
    """Pick the channel that should receive a cluster-naming prompt.

    Household-shared paired channels win. Otherwise we fall back to
    the first paired channel in the DB so single-user installs keep
    working without flipping the share toggle on.
    """
    async with async_session() as db:
        result = await db.execute(
            select(TelegramChannel)
            .where(TelegramChannel.enabled.is_(True))
            .where(TelegramChannel.shared_with_household.is_(True))
            .where(TelegramChannel.chat_id.isnot(None))
            .order_by(TelegramChannel.created_at.asc())
            .limit(1)
        )
        ch = result.scalars().first()
        if ch is not None:
            return ch
        result = await db.execute(
            select(TelegramChannel)
            .where(TelegramChannel.enabled.is_(True))
            .where(TelegramChannel.chat_id.isnot(None))
            .order_by(TelegramChannel.created_at.asc())
            .limit(1)
        )
        return result.scalars().first()


async def _recent_named_persons(db, limit: int = 3) -> list[Person]:
    """Return the most recently created Person rows for the "Same as
    <Aisha>" buttons. Keeps the button row short. Telegram's
    callback_data is 64 bytes per button and we still need ``a, c, p``
    inside the JSON payload."""
    result = await db.execute(
        select(Person).order_by(Person.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


def _build_naming_keyboard(
    *, cluster_id: uuid.UUID, kind: str, candidates: list[Person]
) -> dict:
    """Build the inline keyboard for a cluster-naming prompt.

    Row 1. Up to 3 "Same as <Name>" callback buttons.
    Row 2. "Name as..." (opens text dialog) + "Skip".
    Callback payload keys. ``a`` action, ``c`` cluster id, ``k`` kind,
    optional ``p`` person id. Kept short to fit Telegram's 64-byte cap.
    """
    rows: list[list[dict]] = []
    same_row: list[dict] = []
    for person in candidates[:3]:
        payload = {
            "a": "open_cluster",  # repurposed. internal handler resolves via 'k' + 'p'
            "c": str(cluster_id),
            "k": kind,
            "p": str(person.id),
        }
        signed = sign_callback(json.dumps(payload, separators=(",", ":")))
        if len(signed.encode("utf-8")) > CALLBACK_DATA_MAX:
            continue
        # Truncate aggressively so the row fits on narrow screens.
        label = (person.display_name or "Unknown")[:18]
        same_row.append({"text": f"= {label}", "callback_data": signed})
    if same_row:
        rows.append(same_row)

    name_payload = {"a": "name_cluster_telegram", "c": str(cluster_id), "k": kind}
    skip_payload = {"a": "open_cluster", "c": str(cluster_id), "k": kind, "p": "skip"}
    name_signed = sign_callback(json.dumps(name_payload, separators=(",", ":")))
    skip_signed = sign_callback(json.dumps(skip_payload, separators=(",", ":")))
    bottom = []
    if len(name_signed.encode("utf-8")) <= CALLBACK_DATA_MAX:
        bottom.append({"text": "✏ Name as...", "callback_data": name_signed})
    if len(skip_signed.encode("utf-8")) <= CALLBACK_DATA_MAX:
        bottom.append({"text": "Skip", "callback_data": skip_signed})
    if bottom:
        rows.append(bottom)
    return {"inline_keyboard": rows}


async def _request_naming(*, cluster_kind: str, cluster_id: uuid.UUID) -> bool:
    """Common dispatch for face + body cluster naming prompts."""
    channel = await _pick_recipient_channel()
    if channel is None:
        logger.info(
            "cluster naming. no household channel available for %s cluster=%s",
            cluster_kind, cluster_id,
        )
        return False

    try:
        token = decrypt_secret(channel.bot_token_enc)
    except InvalidToken:
        logger.warning(
            "cluster naming. channel %s has unreadable token", channel.id,
        )
        return False

    async with async_session() as db:
        candidates = await _recent_named_persons(db)
        if cluster_kind == "face":
            cluster = await db.get(FaceCluster, cluster_id)
        else:
            cluster = await db.get(BodyCluster, cluster_id)
        if cluster is None:
            return False
        thumb_path = cluster.sample_thumbnail_path
        auto_label = getattr(cluster, "auto_label_number", None)

    label_hint = f"Unknown {auto_label}" if auto_label else "Unknown"
    caption = (
        "Who is this?\n"
        f"<i>{cluster_kind.capitalize()} cluster {label_hint}</i>\n\n"
        "Tap a name below, or pick <b>Name as...</b> and reply with text."
    )
    reply_markup = _build_naming_keyboard(
        cluster_id=cluster_id, kind=cluster_kind, candidates=candidates,
    )

    chat_id = channel.chat_id
    sent_msg_id: int | None = None
    try:
        if thumb_path:
            result = await send_photo_guarded(
                token=token,
                channel_id=channel.id,
                chat_id=chat_id,
                photo=thumb_path,
                caption=caption,
                qps=float(channel.rate_limit_per_chat_qps or 1.0),
                burst=int(channel.rate_limit_per_chat_burst or 3),
                dedupe_window_seconds=0,  # cluster prompts must always reach the user
                media_quality=channel.media_quality or "high",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            if isinstance(result, str):
                # off / duplicate / rate-limited. Fall back to a text-
                # only message so the user still gets the prompt.
                logger.info(
                    "cluster naming send_photo_guarded returned sentinel=%s for cluster=%s",
                    result, cluster_id,
                )
                text_result = await send_message_guarded(
                    token=token,
                    channel_id=channel.id,
                    chat_id=chat_id,
                    text=caption,
                    qps=float(channel.rate_limit_per_chat_qps or 1.0),
                    burst=int(channel.rate_limit_per_chat_burst or 3),
                    dedupe_window_seconds=0,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                if isinstance(text_result, dict):
                    sent_msg_id = text_result.get("message_id")
            elif isinstance(result, dict):
                sent_msg_id = result.get("message_id")
        else:
            text_result = await send_message_guarded(
                token=token,
                channel_id=channel.id,
                chat_id=chat_id,
                text=caption,
                qps=float(channel.rate_limit_per_chat_qps or 1.0),
                burst=int(channel.rate_limit_per_chat_burst or 3),
                dedupe_window_seconds=0,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            if isinstance(text_result, dict):
                sent_msg_id = text_result.get("message_id")
    except TelegramError as exc:
        logger.warning(
            "cluster naming send failed channel=%s cluster=%s. %s",
            channel.id, cluster_id, exc,
        )
        return False

    if sent_msg_id is None:
        return False

    # Index the message + persist a dialog row so a subsequent text
    # reply (no button tap) can land on the same cluster.
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_DIALOG_TTL_SECONDS)
    async with async_session() as db:
        dialog = TelegramDialog(
            channel_id=channel.id,
            chat_id=str(chat_id),
            user_id=None,
            kind=f"name_{cluster_kind}_cluster",
            context={
                "cluster_id": str(cluster_id),
                "kind": cluster_kind,
            },
            awaiting="name_input",
            last_message_id=int(sent_msg_id),
            expires_at=expires_at,
        )
        db.add(dialog)
        # Mark the cluster as prompted so we don't loop forever.
        if cluster_kind == "face":
            cluster = await db.get(FaceCluster, cluster_id)
        else:
            cluster = await db.get(BodyCluster, cluster_id)
        if cluster is not None:
            cluster.naming_prompted_at = now
        await db.commit()

    await store_message_index(
        channel.id, int(sent_msg_id),
        {
            "kind": "cluster_naming",
            "cluster_kind": cluster_kind,
            "cluster_id": str(cluster_id),
        },
    )
    logger.info(
        "cluster naming prompt sent. channel=%s cluster=%s kind=%s msg=%s",
        channel.id, cluster_id, cluster_kind, sent_msg_id,
    )
    return True


async def request_face_cluster_naming(cluster_id) -> bool:
    """Send a Telegram prompt asking who an unknown face cluster is.

    Returns True when a message went out + a dialog row was persisted.
    False on missing channel, missing cluster, or any send failure.
    Safe to call concurrently for different clusters; the caller is
    expected to dedupe via ``FaceCluster.naming_prompted_at``.
    """
    if not isinstance(cluster_id, uuid.UUID):
        cluster_id = uuid.UUID(str(cluster_id))
    return await _request_naming(cluster_kind="face", cluster_id=cluster_id)


async def request_body_cluster_naming(cluster_id) -> bool:
    """Mirror of :func:`request_face_cluster_naming` for body clusters."""
    if not isinstance(cluster_id, uuid.UUID):
        cluster_id = uuid.UUID(str(cluster_id))
    return await _request_naming(cluster_kind="body", cluster_id=cluster_id)


__all__ = [
    "request_face_cluster_naming",
    "request_body_cluster_naming",
]
