# Nurby

Self-hostable, privacy-first AI camera surveillance you fully own. Point Nurby at any IP camera and a vision model of your choice (local or cloud), and it turns raw video into people, journeys, events, and answers. Ask it "where was the dog last night" in plain language, wire a rule that flashes a siren when a stranger appears after 10pm, and keep every frame on your own hardware.

Nurby is an **open-source, self-hosted CCTV and NVR platform**. a privacy-first **video management system (VMS)** with on-device AI. Run it on your own hardware as a free alternative to cloud security-camera subscriptions, and as an AI-native companion to projects like Frigate, Scrypted, MotionEye, Shinobi, and Blue Iris.

Privacy first. Provider agnostic. CPU friendly. Yours to run and modify.

```
"Where was Mom last seen?"  ->  Kitchen, today at 7:42pm (cross-camera journey)
"Anything unusual today?"   ->  Map-reduce summary of every observation
Stranger at the door 2am    ->  Email you + record a clip + sound an ESP32 buzzer
```

## What is Nurby?

Nurby is free, open-source software for recording and understanding your security cameras on your own server. It is a self-hosted network video recorder (NVR) and AI surveillance platform. It ingests RTSP and ONVIF IP cameras, detects objects and recognizes faces locally, captions scenes with a vision-language model, and lets you ask questions about your footage in plain language. Everything runs on hardware you control with Docker, so with a local model no video ever leaves your network. People use it as a private home-security camera system, a small-business CCTV setup, and a programmable surveillance platform with a REST API, webhooks, and physical alarm integrations.

## Why Nurby

- **You own the data.** Runs entirely on your hardware via Docker Compose. With a local model nothing ever leaves your network.
- **Bring your own brain.** Use a free local model through Ollama, or OpenAI, Anthropic, or Gemini. Swap them per camera with no restart.
- **It understands, not just detects.** YOLO finds objects, faces are recognized and grouped into people, and a vision model captions scenes. A built-in agent answers questions over all of it.
- **Automation that reaches the real world.** Rules can notify, email, call webhooks, sound physical alarms, and gate on a second AI confirmation before firing.
- **Programmable.** A documented REST API, long-lived API keys, signed webhooks, and an MCP server let you build on top of it.

## Real-world use cases

What people actually run Nurby for:

- **Front door and porch.** Know when a package is dropped, when a stranger lingers, or when a known face (a family member, a dog walker) arrives. Get an email with a clip and a link to the footage.
- **Baby and elder care.** A gentle "still moving" check on a crib or a room, and passive check-ins that do not spam you with alerts. Audio triggers catch a baby cry or a smoke alarm.
- **Pets and wildlife.** Recognize your own animals, log their activity, and trigger a deterrent (a siren or lights) when an unwanted animal shows up.
- **Intrusion and loitering.** Draw a zone on the live feed and alert when someone stays too long, or when an unknown face appears after hours. Chain a verify step so a second AI confirmation fires the siren only when it is real.
- **Find anything later.** Ask in plain language: "where was the dog last night", "show me the white van on the driveway this week", "anything unusual today". No scrubbing timelines.
- **Small business and farm.** Multi-camera coverage, license-plate reads on vehicles, daily digests of who and what was seen, and event logging you can export.
- **Build your own automations.** Every event can hit a webhook, so you can push alerts into your home automation, a chat app, a spreadsheet, or a tool like n8n. See [Automate with n8n](#automate-with-n8n).

These map to the building blocks below: detection, faces and people, zones and tripwires, audio events, rules with real-world actions, and natural-language search.

## Get Nurby running on your computer

New to this kind of software? This is the whole setup. You do not need to know Docker, Python, or databases. You copy four commands, wait once, and open a web page. It runs the same way on macOS, Windows, and Linux.

### Step 1. Install Docker Desktop

Docker is the one tool Nurby needs. It runs everything else for you (the database, the AI services, the web app) in the background so you do not install them one by one:

- Download and install **Docker Desktop** from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
- Open it once after installing and leave it running. You will see a whale icon in your menu bar or system tray when it is ready.

On Windows, accept the WSL 2 prompt if it appears. That is Docker setting itself up, and it is normal.

### Step 2. Download Nurby

Open a terminal (on Mac, the Terminal app. On Windows, PowerShell) and run:

```bash
git clone https://github.com/Eshpelin/nurby.git
cd nurby
```

No `git`? Install [Git](https://git-scm.com/downloads), or download the project as a ZIP from the green "Code" button on GitHub, unzip it, and `cd` into the folder.

### Step 3. Create your settings file

```bash
cp .env.example .env
```

This makes a `.env` file from the template. The defaults are fine for trying it on your own machine. You do not need to edit anything yet.

### Step 4. Fetch the AI models

```bash
bash scripts/fetch-models.sh
```

This downloads the detection, face, and license-plate models once (about 430 MB) so the perception service can bake them into its image. They are baked in rather than pulled at runtime so Nurby works offline and starts instantly, and so it runs on locked-down networks where the upstream model hosts are not reachable. Re-running is safe and skips anything already downloaded.

### Step 5. Start it

```bash
docker compose up --build
```

The first time, this downloads and assembles everything. It can take **5 to 15 minutes** and print a lot of text. That is expected, and only happens once. Later starts take seconds. When it settles and stops scrolling, Nurby is running. Leave this terminal window open while you use it.

### Step 6. Open Nurby

Open your web browser and go to **[http://localhost:4747](http://localhost:4747)**.

The first visit drops you straight in. no account wall, no forms. You pick how to start:

- **Show me some magic.** One click. Nurby adds a live demo camera, sets up a private local vision model if one is reachable, and lands you on the dashboard watching footage. Nothing leaves your machine.
- **Set it up myself.** A short guided flow. add your own camera (paste its RTSP or ONVIF link, the built-in brand guide covers 26 popular brands, or use your laptop or phone webcam), then optionally pick a vision model, local or cloud.

When you are ready, a **Secure your account** button in the top bar lets you set an email and password so only you can get back in. Until then you are signed in as a provisional owner.

That is it. You now have Nurby running. Open **Ask** and try a question, or build your first rule.

**Want AI scene descriptions with zero setup?** Detection, faces, and rules all work without a vision model. For plain-language captions and Ask Nurby, start the optional bundled local AI once:

```bash
docker compose --profile local-ai up -d ollama
```

Nurby detects it automatically, and Settings → AI Providers can deploy a model in one click. It stays opt-in so a plain `docker compose up` remains light.

### Stopping, starting, and resetting

- **Stop it.** Press `Ctrl+C` in the terminal, or run `docker compose down`.
- **Start it again.** `docker compose up` (no `--build` needed after the first time).
- **Update to the latest version.** `./scripts/update.sh`. See [Updating](#updating).
- **Start completely fresh.** `docker compose down -v` wipes all data and gives you a clean slate. This deletes everything, so only do it on purpose.

### If something does not work

| Problem | Fix |
|---|---|
| `docker: command not found` or "Cannot connect to the Docker daemon" | Docker Desktop is not installed or not running. Open it and wait for the whale icon, then retry. |
| "port is already allocated" | Another program is using a port Nurby needs (4747 or 4748). Quit that program, or change the port on the left side of the mapping in `docker-compose.yml`. |
| The first `up --build` seems stuck | It is downloading. Give it up to 15 minutes the first time. A fast internet connection helps. |
| The page at localhost:4747 will not load | Wait until the terminal stops scrolling and shows the services are up, then refresh. On Windows make sure Docker is using WSL 2. |
| "I do not have an RTSP link for my camera" | Use the in-app brand guide when adding a camera, or start with your webcam to explore. |
| No AI model offered in setup | Install [Ollama](https://ollama.com/download) and start it, then click "Check again" in the model step, or paste a cloud provider API key. |

Want more control (custom passwords, HTTPS, a public address)? See [Configuration](#configuration) and [Requirements](#requirements) below.

## Feature highlights

### Cameras and ingestion
- Multi-protocol cameras. RTSP, HTTP MJPEG, HTTP snapshot, HLS, USB, file, and a phone or laptop webcam as a camera.
- ONVIF auto-discovery with network scanning, plus USB and local device probing.
- A guided camera-brand cheat sheet (26 brands) that shows where to find each vendor's RTSP/ONVIF URL during setup.
- Smart recording per camera. Continuous, motion, object, or clip with pre/post buffers.
- Retention policies enforced automatically, by time or by size, with thumbnail cleanup.

### Perception and reasoning
- YOLO detection with a curated 17-model catalog (yolov8, yolo11, yolo-world open-vocabulary, OIV7 600-class, RT-DETR).
- Dynamic class vocabulary per camera sourced from whichever model is active, not a hardcoded list.
- Face detection and recognition with 512-dim embeddings in pgvector, auto-clustering of unknown faces, and body re-identification.
- Vision-model scene captions with a CPU-friendly pipeline. A CLIP zero-shot gate, perceptual-hash dedupe, a Redis backlog with priority lanes, and late-frame flagging keep it responsive on modest hardware.
- License plate OCR on vehicle crops, audio events (baby cry, dog bark, glass break, smoke alarm), and motion-zone masking.
- Privacy post-processing. Per-person blur and NudeNet nudity blur.

### People, journeys, and nicknames
- Named person profiles with relationship tags, consent tracking, and per-person privacy blur.
- Household nicknames. Call your mother "Mom" and your daughter "Lee" and that is what shows up everywhere, while identity stays canonical under the hood.
- Cross-camera journeys. A subject's sightings are stitched into a single timeline across cameras, with co-presence and transitions.

### Ask Nurby (agentic Q&A)
- Ask questions in plain language and get grounded, cited answers from your footage.
- A tool-use agent drives read-only tools over observations, journeys, people, events, and relationships, with a map-reduce summarizer for long windows.
- Per-user daily token and cost budgets, streamed over WebSocket.
- An MCP server exposes the read tools so external agents (Claude Desktop and others) can query Nurby with a scoped token.

### Rules and automation
- A full-page rule builder with a drag-to-reorder action chain, a live plain-language preview, and a dry-run plus historical-replay tester.
- Trigger types. Object detected, face recognized, unknown face, motion, audio event, loitering, line cross (tripwire), and more, with an inline canvas geometry editor that draws zones on the live feed.
- Conditions for camera scope, schedule, and confidence, with cooldowns to prevent spam.
- Action chain. Webhook, API call, in-app notify, email, Telegram, broadcast, an AI verify gate that can stop the chain, and a VLM call whose output later actions can reference.
- Physical device presets. Pick an ESP32 buzzer, ESP8266 relay lights, or a Raspberry Pi speaker or siren, and Nurby fills the webhook and links you the receiver script to flash.

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

A four-layer pipeline runs as services in one Docker Compose stack:

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
|  Layer 4. Agent. Tool-use Q&A, summarizer, budgets      |
+---------------------------------------------------------+
|  Layer 3. Events. Rules, verify gates, webhooks (HMAC), |
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
- About 4 GB RAM free for a small setup. More if you run larger local vision models.
- A vision model. Either Ollama on the host for fully local inference, or an API key for OpenAI, Anthropic, or Gemini.
- For local development. Python 3.11+, Node.js 20+, and PostgreSQL 15+ with the pgvector extension.

GPU is optional. The perception pipeline is tuned to run on CPU.

## Ports and addresses

The setup walkthrough is in [Get Nurby running on your computer](#get-nurby-running-on-your-computer) above. For reference, the default compose file exposes the stack on these host ports:

| Service        | URL                          |
|----------------|------------------------------|
| Frontend (app) | http://localhost:4747        |
| API            | http://localhost:4748        |
| API docs       | http://localhost:4748/docs   |
| WebRTC (WHEP)  | http://localhost:8889        |
| HLS            | http://localhost:8888        |
| RTSP           | rtsp://localhost:8554        |
| Postgres       | localhost:5433               |
| Redis          | localhost:6379               |

Running a local model with Docker. One-click Ollama deploy pulls models on the machine that runs the API, so when the API runs in Docker, install Ollama on the host and Nurby auto-detects it at `http://host.docker.internal:11434`. You can also set `OLLAMA_BASE_URL` to point anywhere on your network.

## Configuration

Copy `.env.example` to `.env` and adjust. Key variables:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Database password (compose wires it into `DATABASE_URL`). |
| `DATABASE_URL` | Async Postgres DSN. |
| `REDIS_URL` | Redis connection for streams and queues. |
| `JWT_SECRET` | Signing secret for auth tokens. Set a strong value for any real deployment. |
| `RECORDINGS_PATH`, `THUMBNAILS_PATH` | Where clips and thumbnails are stored. |
| `OLLAMA_BASE_URL` | Override where Nurby looks for Ollama. |
| `SMTP_*` | SMTP host, port, user, password, and from-address for email actions. |
| `PUBLIC_BASE_URL` | Public URL used to build clip and event links in alerts. |
| `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_WEBRTC_URL` | Frontend endpoints for local development outside Docker. |

Runtime settings such as timezone, blur defaults, and digest options live in the database and are editable from the Settings page.

## Local development

Backend:

```bash
pip install -e ".[dev]"
alembic upgrade head
uvicorn services.api.main:app --reload   # serves on :8000
```

Frontend:

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

## Updating

Nurby checks GitHub for new releases and shows an "Update available"
banner in Settings. To update, run one command on the host:

```bash
./scripts/update.sh
```

It pulls the latest code, rebuilds, and restarts. Migrations run
automatically on startup. An optional in-app one-click update button is
available too. See [docs/updating.md](docs/updating.md).

Prefer not to build on a low-power box? Every release publishes prebuilt
images to the GitHub Container Registry, so you can `docker compose pull`
and `docker compose up -d` instead of building. See
[docs/releasing.md](docs/releasing.md).

## Automate with n8n

[n8n](https://n8n.io) is a free, self-hostable automation tool. Nurby plugs into it both ways with no custom code.

**Nurby to n8n (react to events).** In n8n, add a Webhook node and copy its URL. In Nurby, add a webhook action to a rule, or a standing subscriber under Rules, and paste that URL. Now every matching alert arrives in n8n as JSON (camera, event, detections, and a `recording_url` link to the clip), and you can route it anywhere: a Slack or Telegram message, a Google Sheet, a smart-home action, a phone call.

**n8n to Nurby (drive Nurby).** In n8n, add an HTTP Request node pointed at the Nurby API with an API key in the `Authorization: Bearer` header. Now an n8n workflow can fetch events, list recordings, query people, or create rules on a schedule or in response to anything else in your stack.

Set a signing secret on the Nurby side and n8n can verify the `X-Nurby-Signature` HMAC so it only acts on genuine Nurby alerts. Full walkthrough in [docs/integrations/n8n.md](docs/integrations/n8n.md).

## Database migrations

```bash
alembic upgrade head                                  # apply pending migrations
alembic revision --autogenerate -m "describe change"  # after model changes
```

In Docker the API applies migrations automatically on startup.

## Project structure

```
nurby/
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

## FAQ

**Is Nurby free and open source?**
Yes. Nurby is free and open source under the AGPL-3.0 license. There is no paid tier, no account, and no cloud lock-in. You self-host it.

**Is Nurby a Frigate alternative?**
Nurby covers similar ground to Frigate, Scrypted, Shinobi, MotionEye, and Blue Iris (recording IP cameras, object detection, alerts) and adds AI on top. Faces and people, cross-camera journeys, vision-language scene understanding, and plain-language questions about your footage. You can run it instead of or alongside them.

**Does it work fully offline and keep my video private?**
Yes. With a local model via Ollama, all detection and reasoning happen on your hardware and no video leaves your network. Cloud vision models are optional.

**What cameras work with Nurby?**
Any RTSP or ONVIF IP camera, plus HTTP MJPEG and snapshot cameras, HLS streams, USB cameras, and even a phone or laptop webcam. An in-app guide covers 26 popular camera brands.

**Do I need a GPU?**
No. The perception pipeline is tuned to run on CPU. A GPU helps with larger local vision models but is not required.

**What hardware do I need?**
A machine that runs Docker with roughly 4 GB of free RAM for a small setup. More for bigger local models. It runs on a NAS, a mini PC, an old laptop, or a home server.

**How do I install it?**
Install Docker Desktop, clone the repo, and run `docker compose up --build`, then open http://localhost:4747. See [Get Nurby running on your computer](#get-nurby-running-on-your-computer).

**Can I build on top of it?**
Yes. Nurby has a documented REST API, long-lived API keys, HMAC-signed webhooks, an MCP server for AI agents, and physical-device alert integrations (Arduino, ESP32, Raspberry Pi).

## Keywords

Open source CCTV, self-hosted NVR, network video recorder, video management system (VMS), home security camera software, AI surveillance, computer vision security cameras, RTSP and ONVIF recorder, privacy-first surveillance, self-hosted home security, Frigate alternative, Scrypted alternative, Blue Iris alternative, local AI camera monitoring, face recognition security camera, smart home security, Docker security camera server.

> Maintainer note. GitHub ranks repositories by the "About" description and Topics, not just the README. Set a keyword-rich About description and add Topics such as `cctv`, `nvr`, `surveillance`, `security-camera`, `self-hosted`, `home-security`, `computer-vision`, `object-detection`, `face-recognition`, `rtsp`, `onvif`, `ai`, `privacy`, `docker`, and `vms` in the repository settings.

## Contributing

Contributions are welcome. A good loop is:

1. Fork and branch from `main`.
2. Make focused changes with tests. Run `python -m pytest -q` and, for frontend work, `cd frontend && npm run build`.
3. Open a pull request describing the change and how you verified it.

By contributing you agree your contributions are licensed under the project license below.

## License

Nurby is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). See [LICENSE](LICENSE).

In short, you are free to use, run, study, modify, and share Nurby. If you run a modified version as a network service, you must make your modified source available to its users under the same license. This keeps Nurby and its derivatives open.
