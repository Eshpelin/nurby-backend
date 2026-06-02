# Nurby + n8n

[n8n](https://n8n.io) is a free, self-hostable workflow automation tool. Nurby connects to it in both directions without any custom code. This guide shows both.

## 1. Send Nurby events into n8n

Use this to react to alerts. send a Slack or Telegram message, append a row to a sheet, flip a smart plug, start a phone call.

1. In n8n, create a new workflow and add a **Webhook** node. Set it to `POST` and copy the **Production URL** it gives you.
2. In Nurby, open **Rules**. You have two choices.
   - Add a **webhook action** to a specific rule, and paste the n8n URL. Fires only for that rule.
   - Or open **Webhook subscribers** and add the n8n URL there. Receives every fired event.
3. (Recommended) Set a **signing secret** on the Nurby side. n8n can then verify the `X-Nurby-Signature` header so it only acts on real Nurby alerts (see step 5).
4. Fire a test event in Nurby. n8n receives JSON like this.

```json
{
  "event_id": "uuid",
  "rule_name": "Stranger at the front door",
  "camera_name": "Front Door",
  "timestamp": "2026-06-01T22:41:09+00:00",
  "vlm_description": "A person is at the front door.",
  "object_detections": { "objects": [ { "label": "person", "confidence": 0.91 } ] },
  "recording_url": "https://your-host/api/recordings/<id>/stream",
  "thumbnail_url": "https://your-host/.../thumb.jpg"
}
```

5. Add the nodes you want after the Webhook node. For example a **Telegram** node using `{{$json.rule_name}}` and `{{$json.recording_url}}`.

### Verifying the signature in n8n (optional)

Add a **Code** node right after the Webhook node.

```javascript
const crypto = require('crypto');
const secret = 'the-secret-you-set-in-nurby';
const body = JSON.stringify($json.body); // raw body from the webhook node
const sig = 'sha256=' + crypto.createHmac('sha256', secret).update(body).digest('hex');
if (sig !== $json.headers['x-nurby-signature']) {
  throw new Error('Bad signature, not from Nurby');
}
return $input.all();
```

## 2. Drive Nurby from n8n

Use this to have an n8n workflow read or change Nurby. on a schedule, or in response to anything else in your stack.

1. In Nurby, open **Settings** and create an **API key**. Copy it once (it starts with `nrb_`).
2. In n8n, add an **HTTP Request** node.
   - Method and URL, for example `GET https://your-host/api/events/history?limit=20`.
   - Add a header `Authorization` with value `Bearer nrb_your_key`.
3. Now n8n can call any Nurby endpoint. some useful ones.

| Goal | Request |
|---|---|
| Recent events | `GET /api/events/history?from=...&label=person` |
| A person's sightings | `GET /api/persons/activity/{id}` |
| Cross-camera journeys | `GET /api/journeys?subject_key=Mom` |
| Download a clip | `GET /api/recordings/{id}/download` |
| Create a rule | `POST /api/rules` |

The full API is documented at `/docs` on your Nurby server, and in [docs/api.md](../api.md).

## Tips

- Keep both n8n and Nurby on your local network for a fully private, offline automation loop.
- For "do X every morning" style flows, use an n8n **Schedule** trigger plus an HTTP Request node into the Nurby API.
- For "when something happens in Nurby, do X" flows, use the Webhook approach in section 1.
