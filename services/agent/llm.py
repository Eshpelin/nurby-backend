"""Multi-provider tool-use LLM abstraction for the agent driver.

The driver always works in the canonical Anthropic-style message shape.
``messages`` is a list of ``{role, content}`` where ``content`` is either
a plain string or a list of typed blocks. Supported blocks.

* ``{"type": "text", "text": "..."}``
* ``{"type": "tool_use", "id": "...", "name": "...", "input": {...}}``
* ``{"type": "tool_result", "tool_use_id": "...", "content": "..."}``

``llm_call`` converts to the target provider's dialect, fires the
request, and returns a :class:`LLMResponse` regardless of provider. The
four supported provider kinds map as follows.

* ``anthropic`` / ``claude``  -> Anthropic Messages API native
* ``openai`` / ``gpt``        -> OpenAI Chat Completions tools
* ``gemini`` / ``google``     -> Gemini generateContent functionDeclarations
* ``ollama``                  -> Ollama /api/chat (OpenAI-ish, since Llama 3.1)

All four handle (a) text-only, (b) one tool_use, (c) multiple tool_uses
in one response. Providers whose response indicates no tool-use support
raise ``LLMProviderUnsupported``.

Streaming is implemented for text deltas only. Tool-use blocks are
returned at the end of the call. The ``stream_callback`` (if provided)
is awaited once per text delta.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from shared.models import Provider

logger = logging.getLogger("nurby.agent.llm")


class LLMProviderUnsupported(RuntimeError):
    """Raised when a Provider.kind cannot run tool-use."""


@dataclass
class LLMToolUse:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    stop_reason: str  # "tool_use" | "end_turn" | "max_tokens" | "stop"
    text: str
    tool_uses: list[LLMToolUse] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    provider_request_id: str | None = None


# ── Dialect helpers ──────────────────────────────────────────────────


def _normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in {"anthropic", "claude"}:
        return "anthropic"
    if k in {"openai", "gpt"}:
        return "openai"
    if k in {"gemini", "google"}:
        return "gemini"
    if k == "ollama":
        return "ollama"
    raise LLMProviderUnsupported(f"provider kind {kind!r} does not support tool-use in agent driver")


def _content_blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def _flatten_text(content: Any) -> str:
    parts: list[str] = []
    for blk in _content_blocks(content):
        if isinstance(blk, dict):
            if blk.get("type") == "text":
                parts.append(blk.get("text", ""))
            elif blk.get("type") == "tool_result":
                inner = blk.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "text":
                            parts.append(ib.get("text", ""))
    return "\n".join(p for p in parts if p)


# ── Anthropic ────────────────────────────────────────────────────────


async def _call_anthropic(
    provider: Provider,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    stream: bool,
    stream_callback: Callable[[str], Awaitable[None]] | None,
) -> LLMResponse:
    base = (provider.base_url or "https://api.anthropic.com").rstrip("/")
    url = f"{base}/v1/messages"
    headers = {
        "x-api-key": provider.api_key or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
    if stream:
        body["stream"] = True

    async with httpx.AsyncClient(timeout=120.0) as client:
        if not stream:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            text_parts: list[str] = []
            tool_uses: list[LLMToolUse] = []
            for blk in data.get("content", []):
                t = blk.get("type")
                if t == "text":
                    text_parts.append(blk.get("text", ""))
                elif t == "tool_use":
                    tool_uses.append(LLMToolUse(
                        id=blk.get("id") or str(uuid.uuid4()),
                        name=blk.get("name", ""),
                        arguments=blk.get("input") or {},
                    ))
            usage = data.get("usage") or {}
            return LLMResponse(
                stop_reason=data.get("stop_reason") or "end_turn",
                text="\n".join(text_parts),
                tool_uses=tool_uses,
                tokens_in=int(usage.get("input_tokens") or 0),
                tokens_out=int(usage.get("output_tokens") or 0),
                provider_request_id=data.get("id"),
            )
        # streaming SSE
        text_parts2: list[str] = []
        tool_uses2: list[LLMToolUse] = []
        partial_blocks: dict[int, dict] = {}
        stop_reason = "end_turn"
        tokens_in = 0
        tokens_out = 0
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    evt = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if etype == "content_block_start":
                    idx = evt.get("index", 0)
                    blk = evt.get("content_block") or {}
                    partial_blocks[idx] = {"type": blk.get("type"), "name": blk.get("name"),
                                            "id": blk.get("id"), "text": "", "input_json": ""}
                elif etype == "content_block_delta":
                    idx = evt.get("index", 0)
                    d = evt.get("delta") or {}
                    pb = partial_blocks.setdefault(idx, {"type": "text", "text": "", "input_json": ""})
                    if d.get("type") == "text_delta":
                        chunk = d.get("text", "")
                        pb["text"] += chunk
                        if stream_callback and chunk:
                            try:
                                await stream_callback(chunk)
                            except Exception:
                                logger.debug("stream_callback raised", exc_info=True)
                    elif d.get("type") == "input_json_delta":
                        pb["input_json"] += d.get("partial_json", "")
                elif etype == "content_block_stop":
                    idx = evt.get("index", 0)
                    pb = partial_blocks.get(idx)
                    if not pb:
                        continue
                    if pb.get("type") == "text":
                        text_parts2.append(pb.get("text", ""))
                    elif pb.get("type") == "tool_use":
                        try:
                            args = json.loads(pb.get("input_json") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        tool_uses2.append(LLMToolUse(
                            id=pb.get("id") or str(uuid.uuid4()),
                            name=pb.get("name", ""),
                            arguments=args,
                        ))
                elif etype == "message_delta":
                    sr = (evt.get("delta") or {}).get("stop_reason")
                    if sr:
                        stop_reason = sr
                    usage = evt.get("usage") or {}
                    tokens_out = int(usage.get("output_tokens") or tokens_out)
                elif etype == "message_start":
                    msg = evt.get("message") or {}
                    usage = msg.get("usage") or {}
                    tokens_in = int(usage.get("input_tokens") or 0)
        return LLMResponse(
            stop_reason=stop_reason,
            text="\n".join(text_parts2),
            tool_uses=tool_uses2,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


# ── OpenAI ────────────────────────────────────────────────────────────


def _messages_to_openai(system_prompt: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            blocks = _content_blocks(content)
            tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            text_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            if tool_results:
                for tr in tool_results:
                    payload = tr.get("content")
                    if isinstance(payload, list):
                        payload = _flatten_text(payload)
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id"),
                        "content": payload if isinstance(payload, str) else json.dumps(payload),
                    })
                if text_blocks:
                    out.append({"role": "user", "content": _flatten_text(text_blocks)})
            else:
                out.append({"role": "user", "content": _flatten_text(blocks) if not isinstance(content, str) else content})
        elif role == "assistant":
            blocks = _content_blocks(content)
            text_parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            tool_calls = []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_calls.append({
                        "id": b.get("id"),
                        "type": "function",
                        "function": {
                            "name": b.get("name"),
                            "arguments": json.dumps(b.get("input") or {}),
                        },
                    })
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        else:
            out.append({"role": role or "user", "content": content if isinstance(content, str) else _flatten_text(content)})
    return out


async def _call_openai_like(
    provider: Provider,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    stream: bool,
    stream_callback: Callable[[str], Awaitable[None]] | None,
    *,
    is_ollama: bool = False,
) -> LLMResponse:
    base = (provider.base_url or ("http://localhost:11434" if is_ollama else "https://api.openai.com")).rstrip("/")
    # Ollama exposes /api/chat with OpenAI-ish shape; we keep its dedicated
    # path so the driver works against bare ollama deploys without the
    # OpenAI-compat shim.
    if is_ollama:
        url = f"{base}/api/chat"
    else:
        url = f"{base}/v1/chat/completions"
    headers = {"content-type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    msgs = _messages_to_openai(system_prompt, messages)
    if is_ollama:
        # Ollama /api/chat quirks that differ from OpenAI Chat Completions.
        #  - Assistant turns with tool_calls must have content="" not None.
        #  - Tool reply turns use {"role":"tool","content":...} and Ollama
        #    drops tool_call_id. The model picks up tool linkage by order.
        #  - tool_calls[].function.arguments must be a JSON object, not a
        #    JSON-encoded string the way OpenAI accepts. Ollama errors
        #    "Value looks like object, but can't find closing '}' symbol"
        #    if you hand it a string.
        for m in msgs:
            if m.get("role") == "assistant" and m.get("content") is None:
                m["content"] = ""
            if m.get("role") == "tool":
                m.pop("tool_call_id", None)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            fn["arguments"] = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            fn["arguments"] = {}
                    tc.pop("id", None)
                    tc.pop("type", None)
    body: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        if not is_ollama:
            body["tool_choice"] = "auto"
    if is_ollama:
        body["stream"] = bool(stream)
    elif stream:
        body["stream"] = True

    async with httpx.AsyncClient(timeout=120.0) as client:
        if not stream or is_ollama:
            # Ollama streaming is NDJSON; we collapse it the same way as
            # the non-streaming path for simplicity since text-delta
            # callbacks for tool-use loops are nice-to-have not load-bearing.
            if is_ollama and stream:
                body["stream"] = False
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if is_ollama:
                # {"message": {"role": "assistant", "content": "...", "tool_calls": [...]}, ...}
                msg = data.get("message") or {}
                text = msg.get("content") or ""
                tcs = msg.get("tool_calls") or []
                tool_uses: list[LLMToolUse] = []
                for tc in tcs:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    tool_uses.append(LLMToolUse(
                        id=tc.get("id") or str(uuid.uuid4()),
                        name=fn.get("name", ""),
                        arguments=args or {},
                    ))
                stop_reason = "tool_use" if tool_uses else "end_turn"
                if stream_callback and text:
                    try:
                        await stream_callback(text)
                    except Exception:
                        pass
                return LLMResponse(
                    stop_reason=stop_reason,
                    text=text,
                    tool_uses=tool_uses,
                    tokens_in=int(data.get("prompt_eval_count") or 0),
                    tokens_out=int(data.get("eval_count") or 0),
                )
            # OpenAI non-stream
            choices = data.get("choices") or []
            if not choices:
                return LLMResponse(stop_reason="end_turn", text="")
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
            tool_uses: list[LLMToolUse] = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                tool_uses.append(LLMToolUse(
                    id=tc.get("id") or str(uuid.uuid4()),
                    name=fn.get("name", ""),
                    arguments=args or {},
                ))
            finish = choices[0].get("finish_reason") or "stop"
            stop_reason = "tool_use" if tool_uses else ("max_tokens" if finish == "length" else "end_turn")
            usage = data.get("usage") or {}
            return LLMResponse(
                stop_reason=stop_reason,
                text=text,
                tool_uses=tool_uses,
                tokens_in=int(usage.get("prompt_tokens") or 0),
                tokens_out=int(usage.get("completion_tokens") or 0),
                provider_request_id=data.get("id"),
            )
        # OpenAI streaming
        text_acc = ""
        tool_acc: dict[int, dict] = {}
        finish = "stop"
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]" or not payload:
                    continue
                try:
                    evt = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for ch in evt.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        chunk = delta["content"]
                        text_acc += chunk
                        if stream_callback and chunk:
                            try:
                                await stream_callback(chunk)
                            except Exception:
                                logger.debug("stream_callback raised", exc_info=True)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
        tool_uses: list[LLMToolUse] = []
        for slot in tool_acc.values():
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_uses.append(LLMToolUse(
                id=slot["id"] or str(uuid.uuid4()),
                name=slot["name"],
                arguments=args,
            ))
        stop_reason = "tool_use" if tool_uses else ("max_tokens" if finish == "length" else "end_turn")
        return LLMResponse(stop_reason=stop_reason, text=text_acc, tool_uses=tool_uses)


# ── Gemini ────────────────────────────────────────────────────────────


def _messages_to_gemini(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        g_role = "user" if role == "user" else "model"
        parts: list[dict] = []
        for blk in _content_blocks(content):
            if not isinstance(blk, dict):
                parts.append({"text": str(blk)})
                continue
            t = blk.get("type")
            if t == "text":
                parts.append({"text": blk.get("text", "")})
            elif t == "tool_use":
                parts.append({"functionCall": {"name": blk.get("name"), "args": blk.get("input") or {}}})
            elif t == "tool_result":
                payload = blk.get("content")
                if isinstance(payload, list):
                    payload = _flatten_text(payload)
                # Gemini expects a name on functionResponse; the agent
                # passes the tool name out-of-band via the tool_use_id
                # tagging convention. We fall back to the raw id.
                parts.append({
                    "functionResponse": {
                        "name": blk.get("tool_name") or blk.get("tool_use_id") or "tool",
                        "response": {"content": payload if isinstance(payload, str) else json.dumps(payload)},
                    }
                })
        out.append({"role": g_role, "parts": parts})
    return out


async def _call_gemini(
    provider: Provider,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    stream: bool,
    stream_callback: Callable[[str], Awaitable[None]] | None,
) -> LLMResponse:
    base = (provider.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
    suffix = ":streamGenerateContent" if stream else ":generateContent"
    url = f"{base}/v1beta/models/{model}{suffix}"
    headers = {"content-type": "application/json"}
    if provider.api_key:
        url = f"{url}?key={provider.api_key}"

    body: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": _messages_to_gemini(messages),
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if tools:
        body["tools"] = [{"functionDeclarations": tools}]

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Streaming returns a JSON array of GenerateContentResponse chunks.
        # We aggregate; streaming text deltas via stream_callback if so.
        text_acc = ""
        tool_uses: list[LLMToolUse] = []
        tokens_in = 0
        tokens_out = 0
        finish = "STOP"

        async def _ingest(chunk: dict) -> None:
            nonlocal text_acc, tokens_in, tokens_out, finish
            for cand in chunk.get("candidates") or []:
                fr = cand.get("finishReason")
                if fr:
                    finish = fr
                content = cand.get("content") or {}
                for part in content.get("parts") or []:
                    if "text" in part:
                        t = part["text"]
                        text_acc += t
                        if stream_callback and t:
                            try:
                                await stream_callback(t)
                            except Exception:
                                logger.debug("stream_callback raised", exc_info=True)
                    elif "functionCall" in part:
                        fc = part["functionCall"] or {}
                        tool_uses.append(LLMToolUse(
                            id=str(uuid.uuid4()),
                            name=fc.get("name", ""),
                            arguments=fc.get("args") or {},
                        ))
            usage = chunk.get("usageMetadata") or {}
            if usage:
                tokens_in = int(usage.get("promptTokenCount") or tokens_in)
                tokens_out = int(usage.get("candidatesTokenCount") or tokens_out)

        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            for ch in data:
                await _ingest(ch)
        else:
            await _ingest(data)
        stop_reason = "tool_use" if tool_uses else ("max_tokens" if finish == "MAX_TOKENS" else "end_turn")
        return LLMResponse(
            stop_reason=stop_reason,
            text=text_acc,
            tool_uses=tool_uses,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


# ── Public entry point ───────────────────────────────────────────────


async def llm_call(
    provider: Provider,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 2048,
    stream: bool = False,
    stream_callback: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResponse:
    """Single tool-use turn against any supported provider.

    The caller maintains the canonical Anthropic-style messages list and
    appends a new assistant message (text + tool_use blocks) on each
    response. Tool results are appended as user messages with
    ``tool_result`` blocks.
    """
    kind = _normalize_kind(provider.kind)
    if kind == "anthropic":
        return await _call_anthropic(provider, model, system_prompt, messages, tools, max_tokens, stream, stream_callback)
    if kind == "openai":
        return await _call_openai_like(provider, model, system_prompt, messages, tools, max_tokens, stream, stream_callback)
    if kind == "ollama":
        return await _call_openai_like(provider, model, system_prompt, messages, tools, max_tokens, stream, stream_callback, is_ollama=True)
    if kind == "gemini":
        return await _call_gemini(provider, model, system_prompt, messages, tools, max_tokens, stream, stream_callback)
    raise LLMProviderUnsupported(f"unhandled provider kind {kind!r}")


__all__ = [
    "LLMProviderUnsupported",
    "LLMResponse",
    "LLMToolUse",
    "llm_call",
]
