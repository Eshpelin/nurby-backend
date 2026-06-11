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
from datetime import datetime, timezone

from sqlalchemy import select

from services.api.ws import broadcast as ws_broadcast
from services.perception.conversation_clip import (
    build_clip_for_conversation,
    patch_conversation_clip,
)
from services.perception.text_llm import call_text
from services.perception.token_budget import (
    resolve_output_cap,
    trim_sections_to_budget,
)
from services.perception.vlm import get_active_provider
from services.search.embeddings import generate_embedding, get_embedding_provider
from shared.database import async_session
from shared.models import Camera, Conversation, Provider, Transcript

logger = logging.getLogger("nurby.perception.conversation")


CONVERSATION_SYSTEM_PROMPT = (
    "You are a security camera analyst. You receive a transcript of a"
    " short conversation captured by one camera. The transcript was"
    " produced by automatic speech recognition so it may contain"
    " filler words, missing punctuation, broken capitalization, and"
    " disfluencies.\n\n"
    "Return strict JSON with exactly two string fields.\n"
    '  "summary": one concise sentence describing what was said and'
    " the apparent purpose of the exchange. Always write the summary"
    " in English regardless of the spoken language.\n"
    '  "cleaned": the same conversation with fillers removed (um, uh,'
    " like, you know), punctuation normalized, capitalization fixed,"
    " and obvious ASR errors corrected when context makes them clear."
    " Preserve speaker turns on separate lines when speakers differ."
    " Keep the cleaned text in the original spoken language."
    " Do NOT invent words that were not said. Do NOT translate the"
    " cleaned field.\n\n"
    "If the speech is too garbled or trivial to summarize meaningfully,"
    ' return {"summary": "SKIP", "cleaned": ""}.'
    " Output JSON only. No prose, no markdown fences."
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
        cleaned_text: str | None = None
        if cam.conversation_summary_enabled and len(tx_rows) >= int(
            cam.conversation_min_messages_for_summary or 2
        ):
            provider = await self._resolve_provider(cam)
            if provider is not None:
                raw = await self._call_summary(provider, tx_rows)
                summary_text, cleaned_text = self._parse_summary_response(raw)
                if summary_text:
                    await self._patch_summary(
                        conv_id=conv_id,
                        summary_text=summary_text,
                        cleaned_text=cleaned_text,
                        provider_name=provider.name,
                    )

        # Best-effort. Build a single mp4 clip covering the
        # conversation window. Skipped silently when ffmpeg is missing
        # or the camera has no overlapping recordings on disk.
        clip_built = False
        try:
            window_start = tx_rows[0].started_at
            window_end = tx_rows[-1].ended_at
            built = await build_clip_for_conversation(
                conversation_id=conv_id,
                camera_id=cam.id,
                started_at=window_start,
                ended_at=window_end,
            )
            if built is not None:
                clip_path, dur_ms = built
                await patch_conversation_clip(conv_id, clip_path, dur_ms)
                clip_built = True
        except Exception:
            logger.exception("clip build failed conv=%s", conv_id)

        # Native-audio analysis. no-op unless a supports_audio provider is
        # configured (see docs/native-audio-conversation-design.md). when it
        # runs, it adds tone / speaker-count / non-verbal cues the transcript
        # cannot carry, without touching the text summary.
        if clip_built:
            try:
                from services.perception.audio_conversation_analyzer import (
                    analyze_conversation_audio,
                )
                from services.perception.vlm import get_active_provider
                aprov = await get_active_provider()
                transcript_text = " ".join(t.text for t in tx_rows if t.text)
                audio_result = await analyze_conversation_audio(
                    clip_path, transcript_text, aprov
                )
                if audio_result:
                    await self._patch_audio_analysis(conv_id, audio_result, aprov)
            except Exception:
                logger.debug("audio analysis hook failed conv=%s", conv_id, exc_info=True)

        try:
            await self._broadcast(
                {
                    "type": "conversation_finalized",
                    "conversation_id": str(conv_id),
                    "camera_id": str(cam.id),
                    "ended_at": ended_at.isoformat(),
                    "transcript_count": len(tx_rows),
                    "summary_text": summary_text,
                    "cleaned_text": cleaned_text,
                    "has_clip": clip_built,
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
        # Render each transcript line as its own section so older lines
        # drop first when the input token cap is tight. Speaker tag is
        # included so the VLM can tell turns apart even when speaker
        # attribution is missing.
        line_sections: list[tuple[str, str]] = []
        for i, t in enumerate(tx_rows):
            ts = t.started_at.strftime("%H:%M:%S")
            speaker = (
                f"person:{str(t.speaker_person_id)[:8]}"
                if t.speaker_person_id
                else "speaker"
            )
            text = (t.text or "").strip().replace("\n", " ")
            if text:
                line_sections.append((f"line_{i}", f"[{ts}] {speaker}: {text}"))
        if not line_sections:
            return None
        instruction = (
            "Summarize the following short conversation. If the"
            " conversation is too trivial or garbled to be useful,"
            ' return {"summary": "SKIP", "cleaned": ""}.'
        )
        # Keep instruction last (highest priority). Older lines drop
        # first.
        sections = list(line_sections) + [("instruction", instruction)]
        input_cap = getattr(provider, "max_input_tokens", None)
        sections = trim_sections_to_budget(sections, input_cap)
        prompt = "\n".join(text for _, text in sections)
        output_cap = resolve_output_cap(
            getattr(provider, "max_output_tokens", None),
        )
        return await call_text(
            provider=provider,
            system_prompt=CONVERSATION_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=output_cap,
        )

    @staticmethod
    def _parse_summary_response(raw: str | None) -> tuple[str | None, str | None]:
        """Tolerant JSON parse. Strips markdown fences and stray prose,
        falls back to using the whole response as the summary when the
        model ignores the JSON instruction.

        Returns (summary, cleaned). Either may be None on SKIP / parse
        failure.
        """
        import json
        import re

        if not raw:
            return None, None
        text = raw.strip()
        # Strip ```json fences if a model wrapped output anyway.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = text.rstrip("`").rstrip()
            if text.endswith("```"):
                text = text[:-3].rstrip()
        # Try strict JSON first.
        summary: str | None = None
        cleaned: str | None = None
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                summary = (obj.get("summary") or "").strip() or None
                cleaned = (obj.get("cleaned") or "").strip() or None
        except json.JSONDecodeError:
            # Carve a JSON object out of the response if there is one.
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict):
                        summary = (obj.get("summary") or "").strip() or None
                        cleaned = (obj.get("cleaned") or "").strip() or None
                except json.JSONDecodeError:
                    pass
        if summary is None and cleaned is None:
            # Model ignored the schema. Use the whole response as the
            # summary so the user still sees something useful.
            summary = text or None
        if summary and summary.upper().startswith("SKIP"):
            return None, None
        return summary, cleaned

    async def _patch_audio_analysis(self, conv_id: uuid.UUID, result: dict, provider) -> None:
        """Store native-audio-derived conversation fields. Never overwrites
        the text summary."""
        try:
            async with async_session() as db:
                row = await db.get(Conversation, conv_id)
                if row is None:
                    return
                row.audio_speaker_count = result.get("speaker_count")
                row.audio_tone = result.get("tone")
                row.audio_non_verbal = result.get("non_verbal")
                row.audio_gist = result.get("gist")
                row.audio_analyzed_by = (
                    f"{getattr(provider, 'name', '')}/{getattr(provider, 'default_model', '')}"
                )[:64]
                await db.commit()
            logger.info("audio analysis conv=%s tone=%s speakers=%s",
                        conv_id, result.get("tone"), result.get("speaker_count"))
        except Exception:
            logger.debug("audio analysis patch failed conv=%s", conv_id, exc_info=True)

    async def _patch_summary(
        self,
        conv_id: uuid.UUID,
        summary_text: str,
        cleaned_text: str | None,
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
                row.cleaned_text = cleaned_text
                row.summary_provider_name = provider_name
                row.embedding = embedding
                await db.commit()
        except Exception:
            logger.exception("conversation summary patch failed id=%s", conv_id)
