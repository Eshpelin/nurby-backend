# Nurby REST API

Nurby exposes a JSON REST API for fetching cameras, observations,
events, people, and journeys. Every route is auto-documented at
`/docs` (Swagger UI) and `/openapi.json`.

## Authentication

Two credential types work in the `Authorization: Bearer` header:

- User JWT. obtained from `POST /api/auth/login`. Short-lived. Good for
  the web app.
- API key. Long-lived `nrb_...` token for scripts and integrations.
  Preferred for programmatic access.

### Create an API key

```bash
# Log in once to get a JWT.
TOKEN=$(curl -s -X POST http://localhost:4748/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"yourpassword"}' \
  | jq -r .access_token)

# Mint a key. The plaintext is returned once. Store it now.
curl -s -X POST http://localhost:4748/api/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"home-automation","scope":"read"}'
# -> { "id": "...", "prefix": "nrb_AbC12", "key": "nrb_AbC12...full...", ... }
```

Use the key from then on:

```bash
KEY="nrb_AbC12...full..."
curl -s http://localhost:4748/api/observations \
  -H "Authorization: Bearer $KEY"
```

Manage keys with `GET /api/api-keys` (list) and `DELETE /api/api-keys/{id}`
(revoke). A key carries a `read` or `write` scope and an optional
`expires_at`.

## Reading data

All list endpoints page with `limit` (max 200) and `offset`.

### Observations

`GET /api/observations`

| Param | Meaning |
|---|---|
| `camera_id` | Only this camera |
| `from`, `to` | Time window, ISO 8601 |
| `person_id` | Observations naming this person |
| `label` | Observations carrying this YOLO label |

```bash
curl -s "http://localhost:4748/api/observations?label=person&from=2026-06-01T00:00:00Z" \
  -H "Authorization: Bearer $KEY"
```

### Events

`GET /api/events/history`

| Param | Meaning |
|---|---|
| `rule_id` | Events fired by this rule |
| `camera_id` | Events on this camera |
| `status` | Action status (success, failed, pending) |
| `from`, `to` | Time window, ISO 8601 |
| `person_id` | Events whose observation names this person |
| `label` | Events whose observation carries this label |

Each event carries `recording_id` when footage was found. Fetch the clip
with `GET /api/recordings/{recording_id}/stream` or `/download`.

### People

- `GET /api/persons` list people (with `nickname`).
- `GET /api/persons/activity/summary` sighting counts per person.
- `GET /api/persons/activity/{person_id}` a person's observation timeline.

### Journeys

`GET /api/journeys`

| Param | Meaning |
|---|---|
| `subject_kind` | `person` or `object` |
| `subject_key` | Name signature, e.g. `Mom` |
| `from`, `to` | Time window, ISO 8601 |
| `finalized` | Only closed or open journeys |

A journey groups a subject's sightings across cameras into segments.
`GET /api/journeys/{id}` returns the full segment list.

### Recordings

- `GET /api/recordings` browse segments.
- `GET /api/recordings/{id}/stream` stream the clip.
- `GET /api/recordings/{id}/download` download the file.

## Errors

Standard HTTP codes. `401` for a missing, invalid, revoked, or expired
credential. `403` when a viewer lacks camera access. `404` for unknown
ids. Bodies are `{ "detail": "..." }`.
