"""Finalizes open conversations after a quiet window.

Conversation rows open at the first transcript and extend on every
new transcript within ``conversation_gap_seconds``. This worker scans
non-finalized rows and closes any that have been quiet long enough,
optionally calling a VLM to write a one-paragraph recap.

The finalize step is what turns a rolling N-card audio stream into a
single timeline card with a real story. See ``write_path._assign_conversation``
for the open/extend half of the lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from services.api.ws import broadcast as ws_broadcast
from services.perception.text_llm import call_text
from services.perception.vlm import get_active_provider
from services.search.embeddings import generate_embedding, get_embedding_provider
from shared.database import async_session
from shared.models import Camera, Conversation, Provider, Transcript

logger = logging.getLogger("nurby.perception.conversation")


CONVERSATION_SYSTEM_PROMPT = (
    "You are a security camera analyst. You receive a transcript of a"
    " short conversation captured by one camera. Write a single concise"
    " sentence summarizing what was said and the apparent purpose of"
    " the exchange. If the speech is too garbled or trivial to"
    " summarize meaningfully, return the literal string SKIP."
)


class ConversationFinalizer:
    TICK_SECONDS = 10

    def __init__(self, broadcast_fn=ws_broadcast) -> None:
        self._broadcast = broadcast_fn
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("conversation finalizer started")
        try:
            while not self._stopping.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("finalizer tick failed")
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.TICK_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("conversation finalizer stopped")

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with async_session() as db:
            cams = (await db.execute(select(Camera))).scalars().all()
            cam_by_id = {c.id: c for c in cams}
            # Pull open conversations for any camera. Cap to a sane
            # batch so a slow VLM doesn't pile up backlog.
            open_rows = (
                await db.execute(
                    select(Conversation)
                    .where(Conversation.finalized.is_(False))
                    .order_by(Conversation.ended_at_provisional.asc())
                    .limit(50)
                )
            ).scalars().all()

        for conv in open_rows:
            cam = cam_by_id.get(conv.camera_id)
            if cam is None:
                # Camera deleted. Just close the row.
                await self._mark_finalized(conv.id, conv.ended_at_provisional)
                continue
            gap = max(5, int(cam.conversation_gap_seconds or 30))
            quiet_for = (now - conv.ended_at_provisional).total_seconds()
            if quiet_for < gap:
                continue
            await self._finalize(cam, conv.id)

    async def _mark_finalized(
        self, conv_id: uuid.UUID, ended_at: datetime
    ) -> None:
        async with async_session() as db:
            row = await db.get(Conversation, conv_id)
            if row is None:
                return
            row.finalized = True
            row.ended_at = ended_at
            await db.commit()

    async def _finalize(self, cam: Camera, conv_id: uuid.UUID) -> None:
        async with async_session() as db:
            conv = await db.get(Conversation, conv_id)
            if conv is None or conv.finalized:
                return
            tx_rows = (
                await db.execute(
                    select(Transcript)
                    .where(Transcript.conversation_id == conv_id)
                    .where(Transcript.filtered.is_(False))
                    .order_by(Transcript.started_at.asc())
                )
            ).scalars().all()

        if not tx_rows:
            await self._mark_finalized(conv_id, conv.ended_at_provisional)
            return

        # Mark finalized first so the WS event reflects truth even if
        # the VLM call fails. Summary backfills.
        ended_at = conv.ended_at_provisional
        async with async_session() as db:
            row = await db.get(Conversation, conv_id)
            if row is None:
                return
            row.finalized = True
            row.ended_at = ended_at
            speakers = self._aggregate_speakers(tx_rows)
            row.speakers_seen = speakers or None
            await db.commit()

        summary_text: str | None = None
        if cam.conversation_summary_enabled and len(tx_rows) >= int(
            cam.conversation_min_messages_for_summary or 2
        ):
            provider = await self._resolve_provider(cam)
            if provider is not None:
                summary_text = await self._call_summary(provider, tx_rows)
                if summary_text:
                    summary_text = summary_text.strip()
                if summary_text and summary_text.upper().startswith("SKIP"):
                    summary_text = None
                if summary_text:
                    await self._patch_summary(
                        conv_id=conv_id,
                        summary_text=summary_text,
                        provider_name=provider.name,
                    )

        try:
            await self._broadcast(
                {
                    "type": "conversation_finalized",
                    "conversation_id": str(conv_id),
                    "camera_id": str(cam.id),
                    "ended_at": ended_at.isoformat(),
                    "transcript_count": len(tx_rows),
                    "summary_text": summary_text,
                }
            )
        except Exception:
            logger.exception("conversation_finalized WS failed id=%s", conv_id)

    @staticmethod
    def _aggregate_speakers(tx_rows: list[Transcript]) -> list[dict]:
        seen: dict[str, dict] = {}
        for t in tx_rows:
            if not t.speaker_person_id:
                continue
            key = str(t.speaker_person_id)
            seen.setdefault(key, {"person_id": key, "utterances": 0})
            seen[key]["utterances"] += 1
        return list(seen.values())

    async def _resolve_provider(self, cam: Camera) -> Provider | None:
        # summary_provider_id -> vlm_provider_id -> system default.
        for pid in (cam.summary_provider_id, cam.vlm_provider_id):
            if not pid:
                continue
            try:
                async with async_session() as db:
                    p = await db.get(Provider, pid)
                    if p:
                        db.expunge(p)
                        return p
            except Exception:
                logger.exception("provider lookup failed")
        return await get_active_provider()

    async def _call_summary(
        self, provider: Provider, tx_rows: list[Transcript]
    ) -> str | None:
        # Render the conversation as a labeled transcript so the VLM can
        # tell turns apart even when speaker attribution is missing.
        lines = []
        for t in tx_rows:
            ts = t.started_at.strftime("%H:%M:%S")
            speaker = "speaker"
            if t.speaker_person_id:
                speaker = f"person:{str(t.speaker_person_id)[:8]}"
            text = (t.text or "").strip().replace("\n", " ")
            if text:
                lines.append(f"[{ts}] {speaker}: {text}")
        if not lines:
            return None
        prompt = (
            "Summarize the following short conversation in one concise"
            " sentence. If the conversation is too trivial or garbled to"
            " be useful, return SKIP.\n\n" + "\n".join(lines)
        )
        return await call_text(
            provider=provider,
            system_prompt=CONVERSATION_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=200,
        )

    async def _patch_summary(
        self,
        conv_id: uuid.UUID,
        summary_text: str,
        provider_name: str,
    ) -> None:
        try:
            embed_provider = await get_embedding_provider()
            embedding = await generate_embedding(summary_text, embed_provider)
        except Exception:
            embedding = None
        try:
            async with async_session() as db:
                row = await db.get(Conversation, conv_id)
                if row is None:
                    return
                row.summary_text = summary_text
                row.summary_provider_name = provider_name
                row.embedding = embedding
                await db.commit()
        except Exception:
            logger.exception("conversation summary patch failed id=%s", conv_id)
