"""
Action executors for rule engine.

Each action type has its own executor. Actions receive observation
data, the matched rule, and the stored event ID.

Supported action types.
    webhook     POST to a URL with optional custom payload template and headers
    api_call    Full HTTP call with method, auth (Bearer/API key/Basic), custom payload
    broadcast   Push to WebSocket clients with optional custom payload
    notify      Store notification + broadcast via WebSocket
    email       Send email via SMTP with template subject and body
    vlm_call    Ask a VLM provider a question, optionally structured JSON output
    telegram    Send a chat message via a paired Telegram bot channel.
                Phase 1 is text only. Action shape.
                    {"type": "telegram", "channel_id": uuid, "template": str,
                     "include_thumbnail": bool, "silent": bool}
                Template variables available. {rule_name}, {camera_name},
                {timestamp_local}, {vlm_description}, {detections_summary},
                {observation_id}, {event_id}. Both {{var}} and {var}
                shorthand are accepted for parity with the notify action.
"""

import asyncio
import base64
import json
import logging
import os
import re
import uuid

import httpx

from shared.database import async_session
from shared.models import Event, Notification, Provider
from sqlalchemy import select

from services.events.templates import (
    ConditionError,
    render,
    safe_eval_condition,
)

logger = logging.getLogger("nurby.events.actions")

# Legacy single-segment template pattern kept for default payload builder.
_TEMPLATE_VAR = re.compile(r"\{\{(\w+)\}\}")

DEFAULT_VLM_SYSTEM = (
    "You are a security camera AI assistant. Describe what you see in this camera frame "
    "in 1-2 concise sentences. Focus on people, vehicles, animals, and any unusual activity."
)


def _build_template_context(
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
) -> dict:
    """Build nested context dict available for template interpolation."""
    vars_bag = observation_data.get("vars") or {}
    ctx = {
        "event_id": str(event_id),
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "camera_id": observation_data.get("camera_id", ""),
        "camera_name": observation_data.get("camera_name") or observation_data.get("camera_id", ""),
        "timestamp": observation_data.get("timestamp", ""),
        "timestamp_local": observation_data.get("timestamp_local")
        or _localize_timestamp(
            observation_data.get("timestamp"),
            observation_data.get("camera_timezone"),
        ),
        "motion_score": observation_data.get("motion_score", 0),
        "object_detections": observation_data.get("object_detections"),
        "person_detections": observation_data.get("person_detections"),
        "objects": observation_data.get("object_detections"),
        "faces": observation_data.get("person_detections"),
        "description": observation_data.get("vlm_description", ""),
        "vlm_description": observation_data.get("vlm_description", ""),
        "detections_summary": _summarize_detections(observation_data),
        "confidence": observation_data.get("confidence"),
        "observation_id": observation_data.get("observation_id", ""),
        "thumbnail_url": observation_data.get("thumbnail_url") or observation_data.get("thumbnail_path", ""),
        "thumbnail_path": observation_data.get("thumbnail_path", ""),
        "vars": vars_bag,
        "defaults": {"system": DEFAULT_VLM_SYSTEM},
    }
    return ctx


def _localize_timestamp(ts_value, tz_name: str | None) -> str:
    """Render an observation timestamp in the camera's timezone.

    Falls back to the raw value if parsing fails so templates never
    silently produce empty strings.
    """
    if not ts_value:
        return ""
    try:
        from datetime import datetime
        if isinstance(ts_value, datetime):
            dt = ts_value
        else:
            dt = datetime.fromisoformat(str(ts_value).replace("Z", "+00:00"))
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.astimezone(ZoneInfo(tz_name))
            except Exception:
                pass
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    except Exception:
        return str(ts_value)


def _summarize_detections(observation_data: dict) -> str:
    """Compact, human-friendly summary of what was seen. Used by the
    Telegram and email templates so users don't have to format
    detection dicts themselves."""
    pieces: list[str] = []
    objs = observation_data.get("object_detections") or {}
    olist = objs.get("objects") if isinstance(objs, dict) else None
    if olist:
        counts: dict[str, int] = {}
        for det in olist:
            label = str(det.get("label") or "object")
            counts[label] = counts.get(label, 0) + 1
        pieces.extend(f"{n} {label}" for label, n in sorted(counts.items()))
    faces = observation_data.get("person_detections") or {}
    if isinstance(faces, dict) and faces.get("count"):
        pieces.append(f"{faces['count']} face(s)")
    return ", ".join(pieces) if pieces else "no detections"


def _render_template(template, context: dict):
    """Legacy wrapper that now routes to the shared template engine."""
    return render(template, context, strict=False)


def _build_default_payload(context: dict) -> dict:
    return {
        "event_id": context["event_id"],
        "rule_id": context["rule_id"],
        "rule_name": context["rule_name"],
        "camera_id": context["camera_id"],
        "timestamp": context["timestamp"],
        "motion_score": context["motion_score"],
        "object_detections": context["object_detections"],
        "person_detections": context["person_detections"],
        "vlm_description": context["vlm_description"],
    }


def _apply_auth(headers: dict, auth_config: dict | None):
    if not auth_config:
        return
    auth_type = auth_config.get("type", "")
    if auth_type == "bearer":
        token = auth_config.get("token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key":
        header_name = auth_config.get("header", "X-API-Key")
        key = auth_config.get("key", "")
        if key:
            headers[header_name] = key
    elif auth_type == "basic":
        username = auth_config.get("username", "")
        password = auth_config.get("password", "")
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"


async def _update_event_status(
    event_id: uuid.UUID,
    action_type: str,
    status: str,
    error: str | None = None,
):
    try:
        async with async_session() as db:
            event = await db.get(Event, event_id)
            if event:
                event.action_type = action_type
                event.action_status = status
                event.action_error = error
                await db.commit()
    except Exception:
        logger.exception("Failed to update event %s status", event_id)


def _check_condition(action: dict, ctx: dict, rule_name: str) -> bool:
    expr = action.get("condition")
    if not expr:
        return True
    try:
        result = safe_eval_condition(expr, ctx)
    except ConditionError as exc:
        logger.warning("Bad condition in rule '%s'. %s", rule_name, exc)
        return False
    if not result:
        logger.debug("Rule '%s' action condition false. skipping", rule_name)
    return bool(result)


async def execute_action(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """Dispatch action to correct executor based on type."""
    action_type = action.get("type")

    ctx = _build_template_context(observation_data, rule, event_id)
    if not _check_condition(action, ctx, rule.name):
        return

    if action_type == "webhook":
        await _execute_webhook(action, observation_data, rule, event_id, ctx)
    elif action_type == "api_call":
        await _execute_api_call(action, observation_data, rule, event_id, ctx)
    elif action_type == "broadcast":
        await _execute_broadcast(action, observation_data, rule, event_id, ctx)
    elif action_type == "notify":
        await _execute_notify(action, observation_data, rule, event_id, ctx)
    elif action_type == "email":
        await _execute_email(action, observation_data, rule, event_id, ctx)
    elif action_type == "vlm_call":
        await _execute_vlm_call(action, observation_data, rule, event_id, ctx)
    elif action_type == "telegram":
        await _execute_telegram(action, observation_data, rule, event_id, ctx)
    else:
        logger.warning("Unknown action type '%s' in rule '%s'", action_type, rule.name)
        await _update_event_status(event_id, action_type or "unknown", "failed", f"Unknown action type '{action_type}'")


async def _execute_webhook(action, observation_data, rule, event_id, ctx):
    url_tpl = action.get("url")
    if not url_tpl:
        await _update_event_status(event_id, "webhook", "failed", "Missing 'url' in webhook action")
        return
    url = render(url_tpl, ctx)

    payload_template = action.get("payload_template")
    if payload_template:
        payload = render(payload_template, ctx)
    else:
        payload = _build_default_payload(ctx)

    headers = render(dict(action.get("headers", {})), ctx)
    headers.setdefault("Content-Type", "application/json")
    _apply_auth(headers, action.get("auth"))
    timeout = action.get("timeout", 10)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
            logger.info("Webhook fired for rule '%s' -> %s (status %d)", rule.name, url, resp.status_code)
            await _update_event_status(event_id, "webhook", "success")
        except httpx.TimeoutException:
            await _update_event_status(event_id, "webhook", "failed", f"Timeout connecting to {url}")
        except httpx.RequestError as exc:
            await _update_event_status(event_id, "webhook", "failed", str(exc))


async def _execute_api_call(action, observation_data, rule, event_id, ctx):
    url_tpl = action.get("url")
    if not url_tpl:
        await _update_event_status(event_id, "api_call", "failed", "Missing 'url' in api_call action")
        return
    url = render(url_tpl, ctx)
    method = render(action.get("method", "POST"), ctx).upper()

    payload = None
    payload_template = action.get("payload_template")
    if payload_template:
        payload = render(payload_template, ctx)
    elif method in ("POST", "PUT", "PATCH"):
        payload = _build_default_payload(ctx)

    headers = render(dict(action.get("headers", {})), ctx)
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")
    _apply_auth(headers, action.get("auth"))

    query_params = action.get("query_params")
    if query_params:
        query_params = render(query_params, ctx)

    timeout = action.get("timeout", 10)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method, url,
                json=payload if payload is not None else None,
                headers=headers, params=query_params, timeout=timeout,
            )
            logger.info("API call fired for rule '%s' -> %s %s (status %d)", rule.name, method, url, resp.status_code)
            await _update_event_status(event_id, "api_call", "success")
        except httpx.TimeoutException:
            await _update_event_status(event_id, "api_call", "failed", f"Timeout on {method} {url}")
        except httpx.RequestError as exc:
            await _update_event_status(event_id, "api_call", "failed", str(exc))


async def _execute_broadcast(action, observation_data, rule, event_id, ctx):
    from services.api.ws import broadcast

    payload_template = action.get("payload_template")
    if payload_template:
        message = render(payload_template, ctx)
        if isinstance(message, dict):
            message.setdefault("type", "event")
    else:
        message = {"type": "event", **_build_default_payload(ctx)}

    extra = action.get("extra_fields", {})
    if isinstance(message, dict) and extra:
        message.update(render(extra, ctx))

    try:
        await broadcast(message)
        await _update_event_status(event_id, "broadcast", "success")
    except Exception as exc:
        await _update_event_status(event_id, "broadcast", "failed", str(exc))


async def _execute_notify(action, observation_data, rule, event_id, ctx):
    from services.api.ws import broadcast

    template = action.get("message", "Rule '{{rule_name}}' triggered")
    # Back-compat. support {rule_name} style (single brace) plus {{rule_name}} style.
    legacy = template.replace("{rule_name}", rule.name).replace(
        "{camera_id}", observation_data.get("camera_id", "unknown")
    )
    message_text = render(legacy, ctx)
    title_text = render(action.get("title", ""), ctx) if action.get("title") else None

    try:
        async with async_session() as db:
            notif = Notification(
                message=message_text,
                severity=action.get("severity", "info"),
                rule_id=rule.id,
                camera_id=uuid.UUID(observation_data["camera_id"]) if observation_data.get("camera_id") else None,
                observation_id=uuid.UUID(observation_data["observation_id"]) if observation_data.get("observation_id") else None,
            )
            db.add(notif)
            await db.commit()
            await db.refresh(notif)
            notif_id = str(notif.id)
    except Exception:
        logger.exception("Failed to persist notification for rule '%s'", rule.name)
        notif_id = str(uuid.uuid4())

    notification = {
        "type": "notification",
        "id": notif_id,
        "event_id": str(event_id),
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "message": message_text,
        "severity": action.get("severity", "info"),
        "camera_id": observation_data.get("camera_id"),
        "timestamp": observation_data.get("timestamp"),
    }
    if title_text:
        notification["title"] = title_text

    try:
        await broadcast(notification)
        await _update_event_status(event_id, "notify", "success")
    except Exception as exc:
        await _update_event_status(event_id, "notify", "failed", str(exc))


async def _execute_email(action, observation_data, rule, event_id, ctx):
    from shared.config import settings
    from shared.email import send_email

    recipient_tpl = action.get("to")
    if not recipient_tpl:
        await _update_event_status(event_id, "email", "failed", "Missing 'to' in email action")
        return
    recipient = render(recipient_tpl, ctx)

    if not settings.smtp_host:
        await _update_event_status(event_id, "email", "failed", "SMTP not configured. Set SMTP_HOST in environment")
        return

    subject = render(action.get("subject", "Nurby alert. {{rule_name}}"), ctx)
    body = render(action.get("body", action.get("body_text", "Rule {{rule_name}} fired at {{timestamp}}")), ctx)

    try:
        await send_email(to=recipient, subject=subject, body=body)
        await _update_event_status(event_id, "email", "success")
    except Exception as exc:
        await _update_event_status(event_id, "email", "failed", str(exc))


# ── VLM call action ──

async def _get_provider_by_kind(kind: str) -> Provider | None:
    # Normalize "gemini" alias to "google" for the DB column kind.
    norm = "google" if kind == "gemini" else kind
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Provider).where(Provider.kind == norm, Provider.active == True).limit(1)
            )
            return result.scalar_one_or_none()
    except Exception:
        logger.exception("Failed to load VLM provider %s", kind)
        return None


def _load_thumbnail_b64(observation_data: dict) -> str | None:
    path = observation_data.get("thumbnail_path")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        logger.exception("Could not load thumbnail %s", path)
        return None


def _validate_json(obj, schema: dict | None) -> tuple[bool, str | None]:
    if not schema:
        return True, None
    try:
        import jsonschema  # type: ignore
    except ImportError:
        logger.warning("jsonschema not installed. skipping validation")
        return True, None
    try:
        jsonschema.validate(instance=obj, schema=schema)
        return True, None
    except jsonschema.ValidationError as exc:  # type: ignore
        return False, str(exc.message)


async def _vlm_openai(provider, model, system, user_prompt, image_b64, schema, timeout):
    body = {
        "model": model,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
        ],
    }
    if image_b64:
        body["messages"][1]["content"].append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"},
        })
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "rule_output", "schema": schema, "strict": True},
        }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{provider.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {provider.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _vlm_anthropic(provider, model, system, user_prompt, image_b64, schema, timeout):
    content = [{"type": "text", "text": user_prompt}]
    if image_b64:
        content.insert(0, {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
        })
    body = {
        "model": model,
        "max_tokens": 800,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    if schema:
        body["tools"] = [{"name": "rule_output", "description": "Return the structured result", "input_schema": schema}]
        body["tool_choice"] = {"type": "tool", "name": "rule_output"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{provider.base_url}/v1/messages",
            headers={
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        if schema:
            for block in data.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "rule_output":
                    return json.dumps(block.get("input", {}))
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return ""


async def _vlm_google(provider, model, system, user_prompt, image_b64, schema, timeout):
    parts = [{"text": user_prompt}]
    if image_b64:
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": image_b64}})
    gen_config = {"maxOutputTokens": 800}
    if schema:
        gen_config["responseMimeType"] = "application/json"
        gen_config["responseSchema"] = schema
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": parts}],
        "generationConfig": gen_config,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{provider.base_url}/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": provider.api_key},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def _vlm_ollama(provider, model, system, user_prompt, image_b64, schema, timeout):
    body = {
        "model": model,
        "prompt": user_prompt,
        "system": system,
        "stream": False,
    }
    if image_b64:
        body["images"] = [image_b64]
    if schema:
        body["format"] = schema
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{provider.base_url}/api/generate", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")


PROVIDER_SUPPORTS_SCHEMA = {"openai", "anthropic", "google", "gemini", "ollama"}


async def _call_vlm(provider_kind, provider, model, system, user_prompt, image_b64, schema, timeout):
    if provider_kind == "openai":
        return await _vlm_openai(provider, model, system, user_prompt, image_b64, schema, timeout)
    if provider_kind == "anthropic":
        return await _vlm_anthropic(provider, model, system, user_prompt, image_b64, schema, timeout)
    if provider_kind in ("google", "gemini"):
        return await _vlm_google(provider, model, system, user_prompt, image_b64, schema, timeout)
    if provider_kind == "ollama":
        return await _vlm_ollama(provider, model, system, user_prompt, image_b64, schema, timeout)
    raise RuntimeError(f"unsupported provider {provider_kind}")


def _extract_json(text: str) -> str:
    """Pull the first JSON object substring from a possibly messy reply."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


async def _execute_vlm_call(action, observation_data, rule, event_id, ctx):
    """Run a VLM query, optionally structured, and bind output to vars."""
    provider_kind = action.get("provider", "openai")
    model_tpl = action.get("model") or ""
    system_tpl = action.get("system") or "{{defaults.system}}"
    prompt_tpl = action.get("prompt") or "Describe the scene."
    attach_image = bool(action.get("attach_image", False))
    schema = action.get("response_schema")
    output_name = action.get("output")
    max_retries = int(action.get("max_retries", 1))
    on_error = action.get("on_error", "continue")
    fallback_value = action.get("fallback_value")
    timeout_ms = int(action.get("timeout_ms", 20000))
    timeout_s = max(1.0, timeout_ms / 1000.0)

    provider = await _get_provider_by_kind(provider_kind)
    if not provider:
        err = f"No active provider of kind {provider_kind}"
        await _update_event_status(event_id, "vlm_call", "failed", err)
        return _apply_vlm_error(observation_data, output_name, on_error, fallback_value, err)

    model = render(model_tpl, ctx) or provider.default_model or ""
    system = render(system_tpl, ctx)
    user_prompt = render(prompt_tpl, ctx)

    if schema and provider_kind not in PROVIDER_SUPPORTS_SCHEMA:
        user_prompt = (
            f"{user_prompt}\n\nReply with only JSON matching this schema. {json.dumps(schema)}"
        )

    image_b64 = _load_thumbnail_b64(observation_data) if attach_image else None

    last_error: str | None = None
    parsed = None
    raw_text = ""
    attempt_prompt = user_prompt

    for attempt in range(max_retries + 1):
        try:
            raw_text = await asyncio.wait_for(
                _call_vlm(provider_kind, provider, model, system, attempt_prompt, image_b64, schema, timeout_s),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            last_error = f"timeout after {timeout_ms}ms"
            continue
        except Exception as exc:
            last_error = f"provider error. {exc}"
            continue

        if not schema:
            parsed = raw_text
            last_error = None
            break

        try:
            parsed = json.loads(_extract_json(raw_text))
        except Exception as exc:
            last_error = f"json parse failed. {exc}"
            attempt_prompt = (
                f"{user_prompt}\n\nPrevious reply was not valid JSON. Error. {exc}. "
                f"Return only valid JSON matching the schema."
            )
            continue

        ok, err = _validate_json(parsed, schema)
        if ok:
            last_error = None
            break
        last_error = f"schema invalid. {err}"
        attempt_prompt = (
            f"{user_prompt}\n\nPrevious reply failed validation. Error. {err}. "
            f"Fix and return only valid JSON."
        )

    if last_error:
        logger.warning("vlm_call failed for rule '%s'. %s", rule.name, last_error)
        await _update_event_status(event_id, "vlm_call", "failed", last_error)
        return _apply_vlm_error(observation_data, output_name, on_error, fallback_value, last_error)

    if output_name:
        vars_bag = observation_data.setdefault("vars", {})
        vars_bag[output_name] = parsed

    await _update_event_status(event_id, "vlm_call", "success")


def _apply_vlm_error(observation_data, output_name, on_error, fallback_value, err_msg):
    if on_error == "fallback" and output_name:
        vars_bag = observation_data.setdefault("vars", {})
        vars_bag[output_name] = fallback_value
    if on_error == "stop":
        raise RuntimeError(f"vlm_call stopped chain. {err_msg}")
    # continue. no-op


# ── Telegram action ──

# Variables documented for the rule builder UI. Kept in sync with the
# frontend rule-builder chip list.
TELEGRAM_TEMPLATE_VARS = (
    "rule_name",
    "camera_name",
    "timestamp_local",
    "vlm_description",
    "detections_summary",
    "observation_id",
    "event_id",
)


def _expand_telegram_template(template: str, ctx: dict) -> str:
    """Render a template using both `{var}` and `{{var}}` styles.

    Mirrors the back-compat behaviour in the notify action so users
    who learned the single-brace shorthand in Notification templates
    don't have to learn a new dialect for Telegram."""
    legacy = template
    for key in TELEGRAM_TEMPLATE_VARS:
        legacy = legacy.replace("{" + key + "}", _stringify_ctx(ctx.get(key)))
    rendered = render(legacy, ctx, strict=False)
    return rendered if isinstance(rendered, str) else str(rendered)


def _stringify_ctx(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        try:
            return json.dumps(val, default=str)
        except Exception:
            return str(val)
    return str(val)


async def _execute_telegram(action, observation_data, rule, event_id, ctx):
    """Resolve a paired Telegram channel and send the rendered message."""
    from shared.crypto import InvalidToken, decrypt_secret
    from shared.models import TelegramChannel
    from services.notify.telegram import TelegramAPI, TelegramError

    channel_id_raw = action.get("channel_id")
    if not channel_id_raw:
        await _update_event_status(event_id, "telegram", "failed", "Missing channel_id")
        return

    try:
        channel_uuid = uuid.UUID(str(channel_id_raw))
    except (ValueError, TypeError):
        await _update_event_status(event_id, "telegram", "failed", "Invalid channel_id")
        return

    template = action.get("template") or "Rule {rule_name} fired on {camera_name}"
    silent = bool(action.get("silent"))
    include_thumbnail = bool(action.get("include_thumbnail"))
    if include_thumbnail:
        # Phase 1 is text only. We accept the flag for forward
        # compatibility but log so users see why the photo never lands.
        logger.warning(
            "telegram action for rule '%s' has include_thumbnail=true. "
            "Photo attachments arrive in Phase 2; sending text only.",
            rule.name,
        )

    async with async_session() as db:
        ch = await db.get(TelegramChannel, channel_uuid)
        if ch is None:
            await _update_event_status(event_id, "telegram", "failed", "Channel not found")
            return
        if not ch.enabled:
            await _update_event_status(event_id, "telegram", "failed", "Channel is disabled")
            return
        if ch.paired_at is None or not ch.chat_id:
            await _update_event_status(event_id, "telegram", "failed", "Channel is not paired")
            return
        try:
            token = decrypt_secret(ch.bot_token_enc)
        except InvalidToken:
            await _update_event_status(
                event_id, "telegram", "failed",
                "Bot token unreadable (jwt_secret rotated?). Replace it on the channel.",
            )
            return
        chat_id = ch.chat_id
        default_silent = bool(ch.default_silent)
        channel_label = ch.label

    text = _expand_telegram_template(template, ctx)
    if not text.strip():
        await _update_event_status(event_id, "telegram", "failed", "Rendered template is empty")
        return

    try:
        result = await TelegramAPI.send_message(
            token,
            chat_id,
            text,
            parse_mode="HTML",
            disable_notification=silent or default_silent,
        )
        logger.info(
            "Telegram sent for rule '%s' channel='%s' message_id=%s",
            rule.name, channel_label, result.get("message_id"),
        )
        await _update_event_status(event_id, "telegram", "success")
    except TelegramError as exc:
        await _update_event_status(event_id, "telegram", "failed", exc.description[:500])
        if exc.is_forbidden:
            # Bot blocked or chat gone. Disable channel + persist error
            # so the settings UI flips to "Blocked" and stops alerts
            # until the user re-enables.
            try:
                async with async_session() as db:
                    refreshed = await db.get(TelegramChannel, channel_uuid)
                    if refreshed is not None:
                        refreshed.enabled = False
                        refreshed.last_test_ok = False
                        refreshed.last_error = exc.description[:500]
                        await db.commit()
            except Exception:
                logger.exception("Failed to mark telegram channel %s blocked", channel_uuid)
