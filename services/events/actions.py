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
"""

import base64
import json
import logging
import re
import uuid

import httpx

from shared.database import async_session
from shared.models import Event, Notification

logger = logging.getLogger("nurby.events.actions")

# Template variable pattern. Matches {{variable_name}}
_TEMPLATE_VAR = re.compile(r"\{\{(\w+)\}\}")


def _build_template_context(
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
) -> dict:
    """Build flat context dict available for template interpolation."""
    return {
        "event_id": str(event_id),
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "camera_id": observation_data.get("camera_id", ""),
        "timestamp": observation_data.get("timestamp", ""),
        "motion_score": observation_data.get("motion_score", 0),
        "object_detections": observation_data.get("object_detections"),
        "person_detections": observation_data.get("person_detections"),
        "vlm_description": observation_data.get("vlm_description", ""),
        "confidence": observation_data.get("confidence"),
        "observation_id": observation_data.get("observation_id", ""),
    }


def _render_template(template: dict | list | str, context: dict):
    """Recursively render template variables in a JSON structure.

    String values containing {{var}} get replaced with context values.
    If the entire string is a single {{var}} and the context value is
    not a string (dict, list, number), the raw value is returned
    preserving its type.
    """
    if isinstance(template, str):
        # Check if entire string is one variable reference
        match = _TEMPLATE_VAR.fullmatch(template.strip())
        if match:
            key = match.group(1)
            if key in context:
                return context[key]
            return template

        # Partial replacement. All variables become strings
        def replacer(m):
            key = m.group(1)
            val = context.get(key, m.group(0))
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            return str(val)

        return _TEMPLATE_VAR.sub(replacer, template)

    if isinstance(template, dict):
        return {k: _render_template(v, context) for k, v in template.items()}

    if isinstance(template, list):
        return [_render_template(item, context) for item in template]

    return template


def _build_default_payload(context: dict) -> dict:
    """Default payload when no template is specified."""
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
    """Apply authentication to request headers.

    Supported auth types.
        bearer      Adds Authorization: Bearer <token>
        api_key     Adds a custom header with API key value
        basic       Adds Authorization: Basic <base64(user:pass)>
    """
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
    """Update the Event record with action outcome."""
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


async def execute_action(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """Dispatch action to correct executor based on type."""
    action_type = action.get("type")

    if action_type == "webhook":
        await _execute_webhook(action, observation_data, rule, event_id)
    elif action_type == "api_call":
        await _execute_api_call(action, observation_data, rule, event_id)
    elif action_type == "broadcast":
        await _execute_broadcast(action, observation_data, rule, event_id)
    elif action_type == "notify":
        await _execute_notify(action, observation_data, rule, event_id)
    elif action_type == "email":
        await _execute_email(action, observation_data, rule, event_id)
    else:
        logger.warning("Unknown action type '%s' in rule '%s'", action_type, rule.name)
        await _update_event_status(event_id, action_type or "unknown", "failed", f"Unknown action type '{action_type}'")


async def _execute_webhook(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """POST observation data to webhook URL with optional custom payload."""
    url = action.get("url")
    if not url:
        logger.error("Webhook action missing 'url' in rule '%s'", rule.name)
        await _update_event_status(event_id, "webhook", "failed", "Missing 'url' in webhook action")
        return

    context = _build_template_context(observation_data, rule, event_id)

    # Use custom payload template if provided, otherwise default
    payload_template = action.get("payload_template")
    if payload_template:
        payload = _render_template(payload_template, context)
    else:
        payload = _build_default_payload(context)

    headers = dict(action.get("headers", {}))
    headers.setdefault("Content-Type", "application/json")

    # Apply auth if configured
    _apply_auth(headers, action.get("auth"))

    timeout = action.get("timeout", 10)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
            logger.info(
                "Webhook fired for rule '%s' -> %s (status %d)",
                rule.name, url, resp.status_code,
            )
            await _update_event_status(event_id, "webhook", "success")
        except httpx.TimeoutException:
            logger.error("Webhook timeout for rule '%s' -> %s", rule.name, url)
            await _update_event_status(event_id, "webhook", "failed", f"Timeout connecting to {url}")
        except httpx.RequestError as exc:
            logger.error("Webhook failed for rule '%s' -> %s. %s", rule.name, url, exc)
            await _update_event_status(event_id, "webhook", "failed", str(exc))


async def _execute_api_call(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """Make an HTTP request to an external API with full auth support.

    Config fields.
        url             Required. Target URL
        method          HTTP method (GET, POST, PUT, PATCH, DELETE). Default POST
        auth            Auth config dict (type + credentials)
        headers         Additional headers dict
        payload_template    Custom JSON payload with {{variable}} placeholders
        timeout         Request timeout in seconds. Default 10
        query_params    Optional dict of URL query parameters
    """
    url = action.get("url")
    if not url:
        logger.error("API call action missing 'url' in rule '%s'", rule.name)
        await _update_event_status(event_id, "api_call", "failed", "Missing 'url' in api_call action")
        return

    method = action.get("method", "POST").upper()
    context = _build_template_context(observation_data, rule, event_id)

    # Build payload
    payload = None
    payload_template = action.get("payload_template")
    if payload_template:
        payload = _render_template(payload_template, context)
    elif method in ("POST", "PUT", "PATCH"):
        payload = _build_default_payload(context)

    # Build headers
    headers = dict(action.get("headers", {}))
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")

    # Apply auth
    _apply_auth(headers, action.get("auth"))

    # Query params with template support
    query_params = action.get("query_params")
    if query_params:
        query_params = _render_template(query_params, context)

    timeout = action.get("timeout", 10)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method,
                url,
                json=payload if payload is not None else None,
                headers=headers,
                params=query_params,
                timeout=timeout,
            )
            logger.info(
                "API call fired for rule '%s' -> %s %s (status %d)",
                rule.name, method, url, resp.status_code,
            )
            await _update_event_status(event_id, "api_call", "success")
        except httpx.TimeoutException:
            logger.error("API call timeout for rule '%s' -> %s %s", rule.name, method, url)
            await _update_event_status(event_id, "api_call", "failed", f"Timeout on {method} {url}")
        except httpx.RequestError as exc:
            logger.error("API call failed for rule '%s' -> %s %s. %s", rule.name, method, url, exc)
            await _update_event_status(event_id, "api_call", "failed", str(exc))


async def _execute_broadcast(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """Push event to all connected WebSocket clients."""
    from services.api.ws import broadcast

    context = _build_template_context(observation_data, rule, event_id)

    # Use custom payload template if provided
    payload_template = action.get("payload_template")
    if payload_template:
        message = _render_template(payload_template, context)
        # Ensure type field exists for client routing
        if isinstance(message, dict):
            message.setdefault("type", "event")
    else:
        message = {
            "type": "event",
            **_build_default_payload(context),
        }

    # Merge extra fields from action config (backward compat)
    extra = action.get("extra_fields", {})
    if isinstance(message, dict) and extra:
        message.update(extra)

    try:
        await broadcast(message)
        logger.info("Broadcast event for rule '%s' to WebSocket clients", rule.name)
        await _update_event_status(event_id, "broadcast", "success")
    except Exception as exc:
        logger.error("Broadcast failed for rule '%s'. %s", rule.name, exc)
        await _update_event_status(event_id, "broadcast", "failed", str(exc))


async def _execute_notify(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """Store notification in DB and broadcast via WebSocket."""
    from services.api.ws import broadcast

    template = action.get("message", "Rule '{rule_name}' triggered")
    message_text = template.replace("{rule_name}", rule.name).replace(
        "{camera_id}", observation_data.get("camera_id", "unknown")
    )

    # Persist notification to database
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

    try:
        await broadcast(notification)
        logger.info("Notification for rule '%s'. %s", rule.name, message_text)
        await _update_event_status(event_id, "notify", "success")
    except Exception as exc:
        logger.error("Notification failed for rule '%s'. %s", rule.name, exc)
        await _update_event_status(event_id, "notify", "failed", str(exc))


async def _execute_email(
    action: dict,
    observation_data: dict,
    rule,
    event_id: uuid.UUID,
):
    """Send an email via SMTP with template subject and body."""
    from shared.config import settings
    from shared.email import send_email

    recipient = action.get("to")
    if not recipient:
        logger.error("Email action missing 'to' in rule '%s'", rule.name)
        await _update_event_status(event_id, "email", "failed", "Missing 'to' in email action")
        return

    if not settings.smtp_host:
        logger.error("SMTP not configured. Cannot send email for rule '%s'", rule.name)
        await _update_event_status(
            event_id, "email", "failed", "SMTP not configured. Set SMTP_HOST in environment"
        )
        return

    context = _build_template_context(observation_data, rule, event_id)

    subject_template = action.get("subject", "Nurby alert. {{rule_name}}")
    body_template = action.get("body", "Rule {{rule_name}} fired at {{timestamp}}")
    subject = _render_template(subject_template, context)
    body = _render_template(body_template, context)

    try:
        await send_email(to=recipient, subject=subject, body=body)
        logger.info("Email sent for rule '%s' to %s", rule.name, recipient)
        await _update_event_status(event_id, "email", "success")
    except Exception as exc:
        logger.error("Email failed for rule '%s' to %s. %s", rule.name, recipient, exc)
        await _update_event_status(event_id, "email", "failed", str(exc))
