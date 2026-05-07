"""Provider-agnostic text-only LLM call.

Mirrors the VLM client's HTTP plumbing but skips image encoding.
Used by the summarizer (window recaps) and the conversation finalizer
(audio rollups). Centralized here so adding a provider lifts both.

Returns ``None`` on any error so callers can no-op gracefully without
sprinkling try/except blocks.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from shared.models import Provider

logger = logging.getLogger("nurby.perception.text_llm")


_http: Optional[httpx.AsyncClient] = None


async def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=60.0)
    return _http


async def call_text(
    provider: Provider,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 400,
) -> str | None:
    """Single-shot text completion against any supported provider."""
    http = await _client()
    kind = provider.kind
    try:
        if kind == "openai":
            model = provider.default_model or "gpt-4o-mini"
            resp = await http.post(
                f"{provider.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        if kind == "anthropic":
            model = provider.default_model or "claude-sonnet-4-20250514"
            resp = await http.post(
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
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        if kind == "google":
            model = provider.default_model or "gemini-1.5-flash"
            resp = await http.post(
                f"{provider.base_url}/v1beta/models/{model}:generateContent",
                params={"key": provider.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens},
                },
            )
            resp.raise_for_status()
            cands = resp.json().get("candidates") or []
            if cands and cands[0].get("content", {}).get("parts"):
                return cands[0]["content"]["parts"][0].get("text")
            return None
        if kind == "ollama":
            model = provider.default_model or "gemma3:4b"
            resp = await http.post(
                f"{provider.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": f"{system_prompt}\n\n{user_prompt}",
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response")
    except httpx.HTTPError:
        logger.exception("text LLM call failed provider=%s", kind)
        return None
    logger.warning("unknown provider kind for text call: %s", kind)
    return None
