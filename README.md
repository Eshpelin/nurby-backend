# Nurby

Self-hostable, privacy-first AI camera surveillance you fully own. Point Nurby at any IP camera and a vision model of your choice (local or cloud), and it turns raw video into people, journeys, events, and answers. Ask it "where was the dog last night" in plain language, wire a rule that flashes a siren when a stranger appears after 10pm, and keep every frame on your own hardware.

Privacy first. Provider agnostic. CPU friendly. Yours to run and modify.

```
"Where was Mom last seen?"  ->  Kitchen, today at 7:42pm (cross-camera journey)
"Anything unusual today?"   ->  Map-reduce summary of every observation
Stranger at the door 2am    ->  Email you + record a clip + sound an ESP32 buzzer
```

## Why Nurby

- **You own the data.** Runs entirely on your hardware via Docker Compose. With a local model nothing ever leaves your network.
- **Bring your own brain.** Use a free local model through Ollama, or OpenAI, Anthropic, or Gemini. Swap them per camera with no restart.
- **It understands, not just detects.** YOLO finds objects, faces are recognized and grouped into people, and a vision model captions scenes. A built-in agent answers questions over all of it.
- **Automation that reaches the real world.** Rules can notify, email, call webhooks, sound physical alarms, and gate on a second AI confirmation before firing.
- **Programmable.** A documented REST API, long-lived API keys, signed webhooks, and an MCP server let you build on top of it.

## Feature highlights

### Cameras and ingestion
- Multi-protocol cameras. RTSP, HTTP MJPEG, HTTP snapshot, HLS, USB, file, and a phone or laptop webcam as a camera.
- ONVIF auto-discovery with network scanning, plus USB and local device probing.
- A guided camera-brand cheat sheet (26 brands) that shows where to find each vendor's RTSP/ONVIF URL during setup.
- Smart recording per camera. continuous, motion, object, or clip with pre/post buffers.
- Retention policies enforced automatically, by time or by size, with thumbnail cleanup.

### Perception and reasoning
- YOLO detection with a curated 17-model catalog (yolov8, yolo11, yolo-world open-vocabulary, OIV7 600-class, RT-DETR).
- Dynamic class vocabulary per camera sourced from whichever model is active, not a hardcoded list.
- Face detection and recognition with 512-dim embeddings in pgvector, auto-clustering of unknown faces, and body re-identification.
- Vision-model scene captions with a CPU-friendly pipeline. a CLIP zero-shot gate, perceptual-hash dedupe, a Redis backlog with priority lanes, and late-frame flagging keep it responsive on modest hardware.
- License plate OCR on vehicle crops, audio events (baby cry, dog bark, glass break, smoke alarm), and motion-zone masking.
- Privacy post-processing. per-person blur and NudeNet nudity blur.

### People, journeys, and nicknames
- Named person profiles with relationship tags, consent tracking, and per-person privacy blur.
- Household nicknames. call your mother "Mom" and your daughter "Lee" and that is what shows up everywhere, while identity stays canonical under the hood.
- Cross-camera journeys. a subject's sightings are stitched into a single timeline across cameras, with co-presence and transitions.

### Ask Nurby (agentic Q&A)
- Ask questions in plain language and get grounded, cited answers from your footage.
- A tool-use agent drives read-only tools over observations, journeys, people, events, and relationships, with a map-reduce summarizer for long windows.
- Per-user daily token and cost budgets, streamed over WebSocket.
- An MCP server exposes the read tools so external agents (Claude Desktop and others) can query Nurby with a scoped token.

### Rules and automation
- A full-page rule builder with a drag-to-reorder action chain, a live plain-language preview, and a dry-run plus historical-replay tester.
- Trigger types. object detected, face recognized, unknown face, motion, audio event, loitering, line cross (tripwire), and more, with an inline canvas geometry editor that draws zones on the live feed.
- Conditions for camera scope, schedule, and confidence, with cooldowns to prevent spam.
- Action chain. webhook, API call, in-app notify, email, Telegram, broadcast, an AI verify gate that can stop the chain, and a VLM call whose output later actions can reference.
- Physical device presets. pick an ESP32 buzzer, ESP8266 relay lights, or a Raspberry Pi speaker or siren, and Nurby fills the webhook and links you the receiver script to flash.

### Integrations and API
- Programmatic REST API documented at `/docs` and `/openapi.json`, with read filters by time, person, label, and camera.
- Long-lived API keys (`nrb_...`) for scripts, scoped and revocable, alongside user JWTs.
- Outbound webhooks with HMAC-SHA256 body signing, automatic retries with backoff, and standing event subscriptions independent of any single rule. Every alert can carry a direct link to its footage clip.
- Email via SMTP and Telegram with inline acknowledge, mute, and snooze buttons.

### Operations
- Live dashboard with a camera grid, hover PTZ controls, an activity timeline, and a 24h digest with a people gallery.
- Natural-language search over observations via pgvector, plus keyword and regex fallbacks.
- Notification center, per-camera storage and retention views, dark and light themes with no flash on load.
- JWT auth with bcrypt, a first-run admin setup, and invite keys with per-camera access grants.

See the [docs](docs/) for deeper guides. [REST API](docs/api.md), [webhooks](docs/webhooks.md), [physical devices](docs/devices.md), [MCP server](docs/mcp.md), and the [agent design](docs/agent-design.md).

## Architecture

A four-layer pipeline runs as services in one Docker Compose stack.

```
+---------------------------------------------------------+
|  Frontend (Next.js 16 / React 19)                       |
|  Dashboard . People . Rules . Ask . Recordings . Settings|
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  API (FastAPI)             |  Streaming (MediaMTX)       |
|  REST + WebSocket + Auth   |  WebRTC . HLS . RTSP        |
|  API keys . MCP server     |                             |
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  Layer 4. Agent. tool-use Q&A, summarizer, budgets      |
+---------------------------------------------------------+
|  Layer 3. Events. rules, verify gates, webhooks (HMAC), |
|           email, Telegram, device alerts, digests       |
+---------------------------------------------------------+
|  Layer 2. Perception. YOLO, tracking, face + body re-id,|
|           VLM captions, plate OCR, audio, privacy blur  |
+---------------------------------------------------------+
|  Layer 1. Ingestion. RTSP decode, motion, recording,    |
|           clips, retention enforcement                  |
+---------------------------------------------------------+
         |                          |
    +----v-----+             +------v------+
    | Postgres |             |    Redis    |
    | pgvector |             |   Streams   |
    +----------+             +-------------+
```

## Requirements

- Docker and Docker Compose (the supported way to run the full stack).
- About 4 GB RAM free for a small setup. more if you run larger local vision models.
- A vision model. either Ollama on the host for fully local inference, or an API key for OpenAI, Anthropic, or Gemini.
- For local development. Python 3.11+, Node.js 20+, and PostgreSQL 15+ with the pgvector extension.

GPU is optional. The perception pipeline is tuned to run on CPU.

## Quick start

```bash
git clone https://github.com/Eshpelin/nurby-backend.git
cd nurby-backend
cp .env.example .env
docker compose up --build
```

With the default compose file the stack is exposed on these host ports.

| Service        | URL                          |
|----------------|------------------------------|
| Frontend       | http://localhost:4747        |
| API            | http://localhost:4748        |
| API docs       | http://localhost:4748/docs   |
| WebRTC (WHEP)  | http://localhost:8889        |
| HLS            | http://localhost:8888        |
| RTSP           | rtsp://localhost:8554        |
| Postgres       | localhost:5433               |
| Redis          | localhost:6379               |

Then open http://localhost:4747 and follow first-run setup.

### First-run setup

1. Open http://localhost:4747. a fresh install routes you to `/setup`.
2. Create the first admin account.
3. Pick a vision model. if Ollama is reachable (locally or on your Docker host) the onboarding detects it and lets you use an installed model in one click, or pull a RAM-appropriate one. Otherwise enter a provider API key.
4. Add your first camera. the brand cheat sheet helps you find the RTSP/ONVIF URL.
5. Choose a detection model on the camera page so the rule builder can source its class list.
6. Create a rule, or just open Ask and ask a question.

### Using a local model with Docker

One-click Ollama deploy pulls models on the machine that runs the API. When the API runs in Docker, install Ollama on the host and Nurby will auto-detect it at `http://host.docker.internal:11434`. You can also set `OLLAMA_BASE_URL` to point anywhere on your network.

## Configuration

Copy `.env.example` to `.env` and adjust. Key variables.

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Database password (compose wires it into `DATABASE_URL`). |
| `DATABASE_URL` | Async Postgres DSN. |
| `REDIS_URL` | Redis connection for streams and queues. |
| `JWT_SECRET` | Signing secret for auth tokens. set a strong value for any real deployment. |
| `RECORDINGS_PATH`, `THUMBNAILS_PATH` | Where clips and thumbnails are stored. |
| `OLLAMA_BASE_URL` | Override where Nurby looks for Ollama. |
| `SMTP_*` | SMTP host, port, user, password, and from-address for email actions. |
| `PUBLIC_BASE_URL` | Public URL used to build clip and event links in alerts. |
| `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_WEBRTC_URL` | Frontend endpoints for local development outside Docker. |

Runtime settings such as timezone, blur defaults, and digest options live in the database and are editable from the Settings page.

## Local development

Backend.

```bash
pip install -e ".[dev]"
alembic upgrade head
uvicorn services.api.main:app --reload   # serves on :8000
```

Frontend.

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev   # serves on :3000
```

Seed realistic demo data (cameras, people, observations, and real journeys built through the production aggregation path).

```bash
python3 scripts/seed_demo_data.py            # add demo data
python3 scripts/seed_demo_data.py --clean    # wipe and repopulate
```

## Tests

The backend ships a fast, deterministic suite that runs without a database or an LLM.

```bash
python -m pytest -q
```

The agentic Q&A surface has a separate 30-fixture eval suite (`tests/test_agent_eval.py`) run nightly in CI. See [docs/agent-eval.md](docs/agent-eval.md).

## Database migrations

```bash
alembic upgrade head                                  # apply pending migrations
alembic revision --autogenerate -m "describe change"  # after model changes
```

In Docker the API applies migrations automatically on startup.

## Project structure

```
nurby-backend/
+-- services/
|   +-- api/            FastAPI REST + WebSocket + auth + routes
|   +-- ingestion/      RTSP decode, motion, recording, retention
|   +-- perception/     YOLO, tracking, face + body re-id, VLM, audio, blur
|   +-- events/         rule engine, actions, webhooks, email, Telegram
|   +-- agent/          tool-use Q&A driver, tools, summarizer
|   +-- mcp/            MCP server exposing read tools
|   +-- search/         vector search, embeddings, digests
|   +-- digest/         background digest scheduler
|   +-- discovery/      ONVIF discovery and PTZ
+-- shared/             models, schemas, auth, config, database
+-- integrations/
|   +-- devices/        physical alert device presets + receiver scripts
+-- frontend/           Next.js app (dashboard, rules, ask, people, ...)
+-- alembic/            database migrations
+-- scripts/            demo + eval seed generators
+-- docs/               API, webhooks, devices, MCP, agent guides
+-- docker-compose.yml  full stack
```

## Contributing

Contributions are welcome. A good loop is.

1. Fork and branch from `main`.
2. Make focused changes with tests. run `python -m pytest -q` and, for frontend work, `cd frontend && npm run build`.
3. Open a pull request describing the change and how you verified it.

By contributing you agree your contributions are licensed under the project license below.

## License

Nurby is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). See [LICENSE](LICENSE).

In short, you are free to use, run, study, modify, and share Nurby. If you run a modified version as a network service, you must make your modified source available to its users under the same license. This keeps Nurby and its derivatives open.
