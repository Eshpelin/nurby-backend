# Nurby Webhooks

Nurby pushes alerts to your own services and devices over HTTP. There
are two ways to receive them:

- Per-rule webhook action. Add a `webhook` or `api_call` action to a
  rule. Fires only when that rule fires.
- Standing subscription. Register a URL once and receive every fired
  event (optionally filtered by rule or camera).

## The payload

Unless you set a custom `payload_template`, Nurby POSTs this JSON.

```json
{
  "event_id": "uuid",
  "rule_id": "uuid",
  "rule_name": "Front door at night",
  "camera_id": "uuid",
  "camera_name": "Front Door",
  "timestamp": "2026-06-01T22:41:09+00:00",
  "motion_score": 0.0,
  "object_detections": { "objects": [ { "label": "person", "confidence": 0.91 } ] },
  "person_detections": { "faces": [ { "person_name": "Mom" } ] },
  "vlm_description": "A person is at the front door.",
  "observation_id": "uuid",
  "recording_id": "uuid-or-empty",
  "recording_url": "https://your-host/api/recordings/<id>/stream",
  "thumbnail_url": "https://your-host/.../thumb.jpg",
  "event_url": "https://your-host/rules?event=<id>"
}
```

`recording_url` is the direct link to the footage clip covering the
alert. It is populated when a recording covers the observation. To fetch
it programmatically use the API key in the Authorization header.

## Template variables

For a custom `payload_template` (or any templated field like the `url`),
these variables interpolate with `{var}` or `{{var}}`.

`event_id`, `event_url`, `rule_id`, `rule_name`, `camera_id`,
`camera_name`, `timestamp`, `timestamp_local`, `motion_score`,
`object_detections`, `person_detections`, `description`,
`detections_summary`, `confidence`, `observation_id`, `recording_id`,
`recording_url`, `thumbnail_url`.

Example template:

```json
{ "text": "{rule_name} on {camera_name} at {timestamp_local}",
  "clip": "{recording_url}" }
```

## Signature verification (HMAC)

Set a `secret` on the action or subscription and Nurby signs the exact
request body. The header is `X-Nurby-Signature: sha256=<hex>`. Recompute
it over the raw bytes you receive and compare.

Python receiver example:

```python
import hashlib, hmac
def verify(raw_body: bytes, header: str, secret: str) -> bool:
    want = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, want)
```

## Delivery and retries

Nurby retries on timeout, connection error, and 5xx with exponential
backoff (three attempts). A 4xx is treated as a permanent misconfig and
is not retried. Keep your receiver fast and return 2xx quickly. Do any
slow work in the background.

## Standing subscriptions

Admin-managed at `/api/webhook-subscriptions`.

```bash
curl -s -X POST http://localhost:4748/api/webhook-subscriptions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{
        "name": "home-hub",
        "url": "http://192.168.1.50:9000/nurby",
        "secret": "a-long-random-string",
        "camera_ids": []
      }'
```

Leave `rule_ids` and `camera_ids` empty to receive all events. Each
subscription records `last_delivery_at` and `last_status`.

## Auth header on webhooks

`webhook` and `api_call` actions also support an outbound `auth` block
(bearer, api_key, or basic) so you can authenticate to a third-party
endpoint. This is separate from the HMAC signature, which proves the
message came from Nurby.
