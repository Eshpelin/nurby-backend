"""
VLM (Vision Language Model) client. Sends frames to a configured
vision model and returns natural language scene descriptions.

Supports multiple providers through a unified interface.
    openai       Any OpenAI-compatible API (OpenAI, Gemini, Together,
                 Groq, Fireworks, Mistral, DeepSeek, LMStudio, vLLM)
    anthropic    Anthropic native API (Claude)
    google       Google Gemini native API
    ollama       Ollama local models (moondream, llava, etc.)
"""

import asyncio
import base64
import logging

import cv2
import httpx
import numpy as np

from shared.database import async_session
from shared.models import Provider
from sqlalchemy import select

logger = logging.getLogger("nurby.perception.vlm")

SYSTEM_PROMPT = (
    "You are a security camera AI assistant. Describe what you see in this camera frame "
    "in 1-2 concise sentences. Focus on people, vehicles, animals, and any unusual activity. "
    "Be specific about locations, actions, and counts. If nothing notable is happening, "
    "say so briefly."
)


async def get_active_provider() -> Provider | None:
    """Fetch the first active VLM provider from the database."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Provider).where(Provider.active == True).limit(1)
            )
            return result.scalar_one_or_none()
    except Exception:
        logger.exception("Failed to fetch active VLM provider")
        return None


class VLMClient:
    def __init__(self):
        self._http = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def describe(
        self,
        frame: np.ndarray,
        detections: list[dict],
        provider: Provider,
        system_prompt: str | None = None,
        max_tokens: int = 200,
        heard_text: str | None = None,
        extra_context: str | None = None,
    ) -> str | None:
        """Send frame to VLM and get a scene description.

        system_prompt: per-camera prompt override. Falls back to default SYSTEM_PROMPT.
        max_tokens: per-camera token limit.
        heard_text: recent transcript text overlapping this frame's window.
            When present, the prompt asks the VLM to fuse audio + visual
            context. Avoids the post-hoc re-enrichment round-trip.
        extra_context: pre-formatted multimodal context block (face
            recognition results, license plate OCR, camera location,
            etc). The pipeline assembles this from specialist models
            so the VLM does not have to re-derive identity or text from
            pixels.
        """
        try:
            prompt = system_prompt or SYSTEM_PROMPT

            # Encode frame as base64 JPEG
            _, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            b64_image = base64.b64encode(jpeg_buf.tobytes()).decode("utf-8")

            # Build context from detections. Skip license_plate here.
            # Plate text is surfaced through extra_context in plain
            # English so the VLM does not see a useless "license_plate"
            # label without the OCR string attached.
            detection_context = ""
            if detections:
                det_parts = [
                    f"{d['label']} ({d['confidence']:.0%})"
                    for d in detections
                    if d.get("label") != "license_plate"
                ]
                if det_parts:
                    detection_context = (
                        f" Objects detected by YOLO: {', '.join(det_parts)}."
                    )

            extra_block = ""
            if extra_context and extra_context.strip():
                trimmed = extra_context.strip()
                if len(trimmed) > 600:
                    trimmed = trimmed[:600].rstrip() + "..."
                extra_block = f" {trimmed}"

            heard_context = ""
            if heard_text and heard_text.strip():
                # Cap at a sane length so we never blow the prompt budget on
                # a long monologue. The VLM only needs the gist.
                snippet = heard_text.strip()
                if len(snippet) > 400:
                    snippet = snippet[:400].rstrip() + "..."
                heard_context = (
                    f' Heard during this scene: "{snippet}".'
                    " Incorporate the speech into your description when it"
                    " clarifies who is speaking, what is happening, or the"
                    " mood. If the speech is unrelated to the visible scene,"
                    " ignore it."
                )

            user_prompt = (
                f"Describe this security camera frame."
                f"{detection_context}{extra_block}{heard_context}"
                " Use the identity, plate, and location facts above as"
                " ground truth. Do not contradict them or re-guess from"
                " pixels."
            )

            if provider.kind == "openai":
                return await self._call_openai(b64_image, user_prompt, provider, prompt, max_tokens)
            elif provider.kind == "anthropic":
                return await self._call_anthropic(b64_image, user_prompt, provider, prompt, max_tokens)
            elif provider.kind == "google":
                return await self._call_google(b64_image, user_prompt, provider, prompt, max_tokens)
            elif provider.kind == "ollama":
                return await self._call_ollama(b64_image, user_prompt, provider, prompt, max_tokens)
            else:
                logger.warning("Unknown provider kind: %s", provider.kind)
                return None

        except Exception:
            logger.exception("VLM call failed for provider %s", provider.name)
            return None

    async def _call_openai(self, b64_image: str, prompt: str, provider: Provider, system_prompt: str = SYSTEM_PROMPT, max_tokens: int = 200) -> str | None:
        http = await self._get_http()
        model = provider.default_model or "gpt-4o-mini"

        response = await http.post(
            f"{provider.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {provider.api_key}"},
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_image}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def _call_anthropic(self, b64_image: str, prompt: str, provider: Provider, system_prompt: str = SYSTEM_PROMPT, max_tokens: int = 200) -> str | None:
        http = await self._get_http()
        model = provider.default_model or "claude-sonnet-4-20250514"

        response = await http.post(
            f"{provider.base_url}/v1/messages",
            headers={
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64_image,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]

    async def _call_google(self, b64_image: str, prompt: str, provider: Provider, system_prompt: str = SYSTEM_PROMPT, max_tokens: int = 200) -> str | None:
        """Call Google Gemini native API (generativelanguage.googleapis.com)."""
        http = await self._get_http()
        model = provider.default_model or "gemini-2.0-flash"

        response = await http.post(
            f"{provider.base_url}/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": provider.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inlineData": {
                                    "mimeType": "image/jpeg",
                                    "data": b64_image,
                                },
                            },
                        ],
                    },
                ],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    async def _call_ollama(self, b64_image: str, prompt: str, provider: Provider, system_prompt: str = SYSTEM_PROMPT, max_tokens: int = 200) -> str | None:
        http = await self._get_http()
        model = provider.default_model or "moondream"

        response = await http.post(
            f"{provider.base_url}/api/generate",
            json={
                "model": model,
                "prompt": f"{system_prompt}\n\n{prompt}",
                "images": [b64_image],
                "stream": False,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None
