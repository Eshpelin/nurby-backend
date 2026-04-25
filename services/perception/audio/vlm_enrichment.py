"""VLM enrichment for an existing observation, given heard_text.

Pulls the observation's stored thumbnail (already on disk), feeds it
back to the active VLM with the transcript context, and patches
``vlm_description``. The enrichment is text-only on the prompt side
because we already know what was on screen at observation time.

If no thumbnail is available we still run a text-only completion that
augments the existing description with the heard speech. This keeps
audio-only timeline rows useful even on cameras where the VLM never
fired during the observation.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import cv2
import numpy as np

from services.perception.vlm import VLMClient, get_active_provider
from services.search.embeddings import generate_embedding, get_embedding_provider
from shared.database import async_session
from shared.models import Observation

logger = logging.getLogger("nurby.perception.audio.vlm_enrichment")

_ENRICH_SYSTEM_PROMPT = (
    "You are a security camera AI assistant. You are revising your "
    "previous description of a scene with newly available speech "
    "transcript context. Produce one or two concise sentences that "
    "integrate what was visible and what was heard. Do not list "
    "timestamps. Stay neutral and factual."
)


async def enrich_observation(observation_id: uuid.UUID, heard_text: str) -> None:
    """Patch the observation's vlm_description and embedding using the
    transcript heard during the observation."""
    if not heard_text:
        return

    provider = await get_active_provider()
    if provider is None:
        logger.debug("no active VLM provider. skipping enrichment for %s", observation_id)
        return

    async with async_session() as db:
        obs = await db.get(Observation, observation_id)
        if obs is None:
            return
        thumbnail_path = obs.thumbnail_path
        existing_desc = obs.vlm_description or ""

    frame = _load_frame(thumbnail_path)

    user_prompt = (
        f"Heard during this scene. {heard_text}\n\n"
        f"Previous description. {existing_desc or '(none)'}\n\n"
        "Revise the description to incorporate the heard speech."
    )

    client = VLMClient()
    try:
        if frame is not None:
            description = await client.describe(
                frame=frame,
                detections=[],
                provider=provider,
                system_prompt=_ENRICH_SYSTEM_PROMPT,
                max_tokens=200,
            )
            # The VLMClient's describe builds its own prompt from the
            # detection list. To inject heard_text we fall through to a
            # text-only call here.
            description = await _text_only(client, provider, user_prompt, _ENRICH_SYSTEM_PROMPT)
        else:
            description = await _text_only(client, provider, user_prompt, _ENRICH_SYSTEM_PROMPT)
    finally:
        await client.close()

    if not description:
        return

    async with async_session() as db:
        obs = await db.get(Observation, observation_id)
        if obs is None:
            return
        obs.vlm_description = description
        obs.vlm_provider = f"{provider.name} (audio-enriched)"
        await db.commit()

    # Refresh embedding so search sees the updated description.
    try:
        emb_provider = await get_embedding_provider()
        embedding = await generate_embedding(description, emb_provider)
        async with async_session() as db:
            obs = await db.get(Observation, observation_id)
            if obs:
                obs.description_embedding = embedding
                await db.commit()
    except Exception:
        logger.warning("embedding refresh failed for %s", observation_id)


def _load_frame(path: Optional[str]) -> Optional[np.ndarray]:
    if not path or not os.path.exists(path):
        return None
    try:
        img = cv2.imread(path)
        return img if img is not None else None
    except Exception:
        return None


async def _text_only(client: VLMClient, provider, user_prompt: str, system_prompt: str) -> Optional[str]:
    """Text-only VLM call. Sidesteps the image-required path by sending
    a 1x1 black PNG. All four supported providers accept this. Cleaner
    than threading a separate text-only branch through VLMClient.
    """
    # 1x1 black BGR pixel.
    black = np.zeros((1, 1, 3), dtype=np.uint8)
    return await client.describe(
        frame=black,
        detections=[],
        provider=provider,
        system_prompt=system_prompt + "\n\n" + user_prompt,
        max_tokens=200,
    )
