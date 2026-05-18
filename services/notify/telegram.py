"""Async Telegram Bot API client.

Single shared ``httpx.AsyncClient`` per process. Methods raise
:class:`TelegramError` carrying the upstream ``error_code`` and
``description`` on non-OK responses so callers can branch on common
failures (e.g. 403 for blocked bot).

Phase 1 surface. ``get_me``, ``send_message``, ``get_updates``. Photo
upload, callback queries, and webhook mode are intentionally left for
later phases. A simple 30 messages/sec semaphore per token guards
against Telegram's documented global rate limit.

Extension points for Phase 2+:
    - ``send_photo`` will mirror ``send_message`` but POST multipart.
    - ``reply_markup`` already plumbed through send_message for inline
      keyboards once callback handling lands.
    - Webhook mode would replace the long-poller; client itself is mode
      agnostic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger("nurby.notify.telegram")

_API_BASE = "https://api.telegram.org"

# Module-level shared client. Reused across all bots.
_client: httpx.AsyncClient | None = None
# Per-token send semaphores so we never exceed 30 messages/sec/bot.
_send_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # Default timeout for short calls. getUpdates overrides per request.
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    return _client


async def shutdown_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _semaphore_for(token: str) -> asyncio.Semaphore:
    sem = _send_semaphores.get(token)
    if sem is None:
        # Telegram's global per-bot limit is ~30 messages/sec.
        sem = asyncio.Semaphore(30)
        _send_semaphores[token] = sem
    return sem


class TelegramError(Exception):
    """Wraps a non-OK Telegram Bot API response."""

    def __init__(self, error_code: int, description: str, method: str = "") -> None:
        self.error_code = error_code
        self.description = description
        self.method = method
        super().__init__(f"{method} -> {error_code}. {description}")

    @property
    def is_forbidden(self) -> bool:
        """403 covers `bot was blocked by the user`, `chat not found`,
        and `user is deactivated`. Callers use this to flip a channel
        into blocked/disabled state."""
        return self.error_code == 403


class TelegramAPI:
    """Stateless facade. Methods take a token explicitly so the same
    client is reused across many bot tokens without re-initialization.
    """

    @staticmethod
    async def _post(token: str, method: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        url = f"{_API_BASE}/bot{token}/{method}"
        client = _get_client()
        try:
            resp = await client.post(url, json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TelegramError(0, f"timeout. {exc}", method) from exc
        except httpx.RequestError as exc:
            raise TelegramError(0, f"network. {exc}", method) from exc

        # Telegram always returns JSON even on errors.
        try:
            data = resp.json()
        except ValueError as exc:
            raise TelegramError(resp.status_code, f"non-json reply. {exc}", method) from exc

        if not data.get("ok"):
            code = int(data.get("error_code") or resp.status_code or 0)
            desc = str(data.get("description") or "unknown error")
            raise TelegramError(code, desc, method)
        return data.get("result") or {}

    @classmethod
    async def get_me(cls, token: str) -> dict[str, Any]:
        return await cls._post(token, "getMe", {}, timeout=10.0)

    @classmethod
    async def send_message(
        cls,
        token: str,
        chat_id: str | int,
        text: str,
        parse_mode: str | None = "HTML",
        disable_notification: bool = False,
        disable_web_page_preview: bool = True,
        reply_markup: dict | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": disable_notification,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        sem = _semaphore_for(token)
        async with sem:
            return await cls._post(token, "sendMessage", payload, timeout=10.0)

    @classmethod
    async def get_updates(
        cls,
        token: str,
        offset: int | None = None,
        timeout: int = 25,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        # The HTTP timeout has to exceed the long-poll timeout.
        result = await cls._post(token, "getUpdates", payload, timeout=timeout + 5)
        if isinstance(result, list):
            return result
        return []


__all__ = ["TelegramAPI", "TelegramError", "shutdown_client"]
