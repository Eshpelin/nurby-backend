# Nurby

AI camera monitoring platform that turns any existing IP camera into a context-aware sentry. Connect cameras, point Nurby at a vision language model (local or cloud), and define what to watch, who to recognize, what events trigger alerts, and how to search historical footage.

Privacy first. Developer friendly. Model agnostic.

## Architecture

Nurby uses a three-layer architecture with services running in a single Docker Compose stack.

```
+---------------------------------------------------------+
|  Frontend (Next.js)                                     |
|  Dashboard . People . Rules . Recordings . Settings     |
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  API (FastAPI)             |  Streaming (MediaMTX)      |
|  REST + WebSocket + Auth   |  WebRTC . HLS . RTSP       |
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  Layer 3. Events & Automation                           |
|  Rule evaluation . Notifications . Webhooks . Email     |
|  Digests . Action execution log                         |
+---------------------------------------------------------+
|  Layer 2. Perception & Reasoning                        |
|  YOLO detection . Tracking . Face recognition . VLM     |
|  Plate OCR . Audio events . NudeNet blur                |
+---------------------------------------------------------+
|  Layer 1. Ingestion                                     |
|  RTSP decode . Motion detection . Recording . Clips     |
|  Retention enforcement                                  |
+---------------------------------------------------------+
         |                          |
    +----v----+              +------v------+
    | Postgres |              |    Redis    |
    | pgvector |              |   Streams   |
    +---------+              +-------------+
```

## Features

### Camera Management
- Multi-protocol camera support (RTSP, HTTP MJPEG, HTTP snapshot, HLS, USB, file)
- ONVIF auto-discovery with network scanning
- USB and local device probing
- Per-camera configuration for detection, recording, and VLM settings
- Camera status logging and health monitoring

### Live Dashboard
- Real-time camera grid with configurable layouts (single, 2x2, 3x3)
- Hover PTZ controls overlaid on camera tiles
- Activity timeline with recordings, AI observations, and status events
- Collapsible filter sidebar for time range, event type, person, and object filters
- Live event ticker via WebSocket
- Camera-specific activity feeds with auto-refresh
- 24h digest with people and unknown-cluster gallery

### AI Perception Pipeline
- YOLO detection with multi-model support and consensus modes
- Curated detection model catalog covering yolov8, yolo11, yolo-world, oiv7, rt-detr families
- Dynamic class vocabulary per camera sourced from whichever model is active
- IoU tracker producing stable track IDs for inline geometry rules
- Face detection and recognition with 128-dim embeddings (pgvector)
- VLM integration for scene descriptions
- Automatic license plate detection via EasyOCR on vehicle crops
- Motion zone masking with include/exclude polygon regions
- Audio event detection (baby cry, dog bark, glass break, smoke alarm)
- VLM trigger conditions separated from rate limit (always, on detection)
- Description embedding generation for vector search
- Privacy post-processing with per-person blur and NudeNet-based nudity blur

### People Management
- Named person profiles with relationship tags (Family, Neighbor, Delivery, etc.)
- Face photo upload and embedding generation
- Activity feed with per-person observation timeline
- Sighting counters (1h, 24h, total) with auto-refresh
- Unknown face auto-clustering with merge suggestions
- Per-person privacy blur toggle
- Consent tracking per person

### PTZ Camera Control
- ONVIF SOAP-based pan/tilt/zoom via directional pad UI
- Adjustable speed control
- Preset positions with save and goto
- Dashboard tile overlay and dedicated panel on camera config page

### Smart Recording
- Recording modes per camera (always, motion-triggered, object-triggered)
- Configurable pre/post clip buffers
- Dedicated recordings browser with camera and date filters
- Download and streaming playback
- Retention policies enforced on a background loop (time-based or size-based)
- Thumbnail cleanup tied to retention evictions

### Rules Engine
- Visual rule builder with card-grid trigger picker
- Trigger types. object detected, face recognized, unknown face, motion, audio event, loitering, line cross (tripwire), any
- Inline geometry editor draws tripwires and loiter zones directly on the live camera feed with canvas overlay
- Known-face trigger uses a person picker tied to the People database
- Object-detection trigger labels come from the live detection model, not a hardcoded list
- Plain-language rule preview composited from trigger, condition, and action
- Conditions for camera scope, days, time windows, minimum confidence
- Cooldown periods to prevent alert spam
- Action types. webhook, API call, broadcast, in-app notification, email
- Execution log with action status tracking and error audit trail

### Notification Center
- Persistent in-app notifications stored in Postgres
- Bell icon with unread count badge in navbar
- Mark individual or all notifications as read
- Severity levels per notification
- Linked to rules, cameras, and observations
- Email delivery channel via SMTP with per-rule template

### Search and QA
- Three-strategy search. keyword label matching, vector similarity (pgvector cosine distance), broad regex fallback
- Synonym expansion for common terms (bike to bicycle, car to vehicle, dog to puppy, etc.)
- PostgreSQL word boundary regex to prevent substring false positives
- Person and object type filters
- Natural language QA via RAG. search results fed as context to configured VLM
- AI answer loading state with observation count feedback
- Rotating search hint placeholders (30 example queries)

### Digest Scheduling
- Background scheduler checks cameras every 60 seconds
- Configurable digest periods per camera (1h, 6h, 12h, 24h, 48h, 7d)
- Auto-generates observation summaries with person sightings and object counts
- People and unknown-cluster gallery included in 24h digest

### Storage and Retention
- Per-camera disk usage visualization
- Recording count and byte totals
- Retention policy display per camera
- System-wide storage overview
- Automatic eviction loop in the ingestion service removes expired recordings and thumbnails

### Authentication and Access Control
- JWT-based authentication with bcrypt password hashing and auto-logout on stale tokens
- Admin setup flow for first-time installation
- Invite key system for user registration with role assignment and camera access grants
- Protected API routes with role-based guards (admin vs viewer)
- Auth-aware frontend with automatic token injection
- Login/setup pages with navbar hidden on public routes

### Theme Support
- Dark and light mode toggle
- System preference detection
- Theme persistence via localStorage
- Inline init script to prevent flash of wrong theme

### VLM Provider Support
- OpenAI (GPT-4o, GPT-4o-mini, text-embedding-3-small)
- Anthropic (Claude Sonnet, Claude Haiku)
- Google Gemini (Gemini 2.0 Flash, Gemini Pro)
- Ollama with one-click local deploy
- SMTP configuration with test endpoint for email actions
- Provider health testing from settings UI
- Hot-swappable with no restart required

### One-Click Local AI (Ollama)
- Auto-detects Ollama installation across PATH and common macOS/Linux locations
- Curated vision model catalog with per-model RAM requirements
- System RAM probe with in-fit filtering and recommended-model highlight
- Deploy button orchestrates pull, start, and provider registration
- Works fully offline once the model is pulled

## Tech Stack

**Backend.** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, OpenCV, PyAV, Ultralytics YOLO, EasyOCR, NudeNet, aiosmtplib, Postgres with pgvector, Redis

**Frontend.** Next.js 16, React 19, Tailwind CSS 4, Geist typography, dark/light mode, custom styled selects, canvas-based geometry editor

**Infrastructure.** Docker Compose, MediaMTX (WebRTC/HLS relay), optional Ollama runtime

## Project Structure

```
nurby-backend/
+-- services/
|   +-- api/            # FastAPI REST + WebSocket + auth
|   |   +-- routes/     # cameras, persons, rules, events, search,
|   |                   # recordings, notifications, providers,
|   |                   # digests, invites, users, system, auth,
|   |                   # detection_models, ollama_deploy, observations
|   +-- ingestion/      # RTSP stream processing, motion detection,
|   |                   # recording, retention enforcement
|   +-- perception/     # YOLO detection, IoU tracker, face recognition,
|   |                   # VLM, plate OCR, audio events, privacy blur
|   +-- events/         # Rule evaluation, inline geometry checks,
|   |                   # action execution, email, notifications
|   +-- search/         # Vector search, embeddings, backfill,
|   |                   # digest generation
|   +-- digest/         # Background digest scheduler
|   +-- discovery/      # ONVIF camera discovery and PTZ control
|   +-- streaming/      # Live video relay config
+-- shared/
|   +-- auth.py         # JWT tokens, bcrypt hashing, route guards
|   +-- config.py       # App settings via pydantic-settings (SMTP, CORS)
|   +-- database.py     # Async SQLAlchemy engine and session
|   +-- models.py       # All Postgres models
|   +-- schemas.py      # Pydantic request/response schemas
|   +-- spatial_events.py # Point-in-polygon and segment-crossing helpers
+-- frontend/           # Next.js app
|   +-- src/app/        # Dashboard, People, Rules, Recordings, Timeline,
|   |                   # Settings, Search, Login, Setup, Camera config
|   +-- src/lib/        # Auth context, theme context
|   +-- src/components/ # Navbar, notifications, auth shell
+-- alembic/            # Database migrations
+-- scripts/            # Seed data generator
+-- config/             # MediaMTX and service configs
+-- docker-compose.yml  # Full stack orchestration
+-- pyproject.toml      # Python dependencies
```

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- Node.js 20+ (for frontend development)
- PostgreSQL 15+ with pgvector extension
- Optional. Ollama for fully local vision inference

### Run the full stack

```bash
cp .env.example .env
docker compose up --build
```

The services will be available at these addresses.

| Service   | URL                    |
|-----------|------------------------|
| Frontend  | http://localhost:3000   |
| API       | http://localhost:8000   |
| API docs  | http://localhost:8000/docs |
| WebRTC    | http://localhost:8889   |
| RTSP      | rtsp://localhost:8554   |

### Local development

**Backend**

```bash
pip install -e ".[dev]"
uvicorn services.api.main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

### First-time setup

1. Start the backend and frontend
2. Navigate to http://localhost:3000/setup
3. Create an admin account (minimum 8 character password)
4. Add your first camera from the dashboard
5. Configure a VLM provider in Settings, or click Deploy Local AI to use Ollama
6. Pick a detection model on the camera settings page so the rule builder can source its class list

### Seed demo data

```bash
python3 scripts/seed_demo_data.py          # populate with sample data
python3 scripts/seed_demo_data.py --clean   # wipe and repopulate
```

### Database migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Generate a new migration after model changes
alembic revision --autogenerate -m "description"
```

## API Overview

All endpoints except `/api/auth/*` require a credential in the
Authorization header. either a user JWT or a long-lived API key
(`nrb_...`). See [docs/api.md](docs/api.md) for the full read API and
filters, [docs/webhooks.md](docs/webhooks.md) for outbound webhooks and
HMAC signing, and [docs/devices.md](docs/devices.md) for physical alert
devices. Every route is auto-documented at `/docs` and `/openapi.json`.

| Endpoint                          | Methods         | Auth       | Description                    |
|-----------------------------------|-----------------|------------|--------------------------------|
| `/api/auth/setup`                 | POST            | Public     | Create first admin account     |
| `/api/auth/login`                 | POST            | Public     | Get JWT token                  |
| `/api/auth/register`              | POST            | Public     | Register with invite key       |
| `/api/auth/me`                    | GET             | User       | Current user profile           |
| `/api/api-keys`                   | GET, POST, DEL  | User       | Manage programmatic API keys   |
| `/api/webhook-subscriptions`      | GET, POST, PATCH, DEL | User/Admin | Standing outbound webhooks |
| `/api/devices`                    | GET             | User       | Physical alert device presets  |
| `/api/journeys`                   | GET             | User       | Cross-camera sighting sessions |
| `/api/health`                     | GET             | Public     | Service and DB health          |
| `/api/status`                     | GET             | User       | System health and camera counts|
| `/api/cameras`                    | GET, POST       | User/Admin | List and add cameras           |
| `/api/cameras/{id}`               | GET, PATCH, DEL | User/Admin | Manage individual cameras      |
| `/api/cameras/{id}/ptz/*`         | POST            | User       | PTZ move, stop, presets, goto  |
| `/api/cameras/discover`           | GET             | User       | ONVIF network scan             |
| `/api/cameras/devices`            | GET             | User       | USB device probe               |
| `/api/detection-models/classes`   | GET             | User       | Union of class names across models |
| `/api/recordings`                 | GET             | User       | Browse recorded segments       |
| `/api/observations`               | GET             | User       | Browse AI observations         |
| `/api/persons`                    | GET, POST       | User       | People management              |
| `/api/persons/activity/summary`   | GET             | User       | All persons with sighting counts |
| `/api/persons/activity/{id}`      | GET             | User       | Person observation timeline    |
| `/api/rules`                      | GET, POST       | User       | List and create rules          |
| `/api/rules/{id}`                 | GET, PATCH, DEL | User/Admin | Manage individual rules        |
| `/api/events`                     | GET             | User       | Browse fired events            |
| `/api/events/{id}/acknowledge`    | POST            | User       | Acknowledge an alert           |
| `/api/notifications`              | GET             | User       | List notifications             |
| `/api/notifications/count`        | GET             | User       | Unread notification count      |
| `/api/notifications/read-all`     | POST            | User       | Mark all as read               |
| `/api/providers`                  | GET, POST       | User/Admin | Manage VLM providers           |
| `/api/providers/{id}/test`        | POST            | User       | Test provider connectivity     |
| `/api/ollama/status`              | GET             | Admin      | Ollama install and model status |
| `/api/ollama/deploy`              | POST            | Admin      | Pull a vision model and auto-register |
| `/api/system/smtp-test`           | POST            | Admin      | Test SMTP settings             |
| `/api/system/settings`            | GET, PATCH      | User/Admin | App-wide settings (blur, etc.) |
| `/api/search`                     | GET             | User       | Search observations            |
| `/api/search/ask`                 | POST            | User       | Natural language QA via RAG    |
| `/api/search/digest`              | GET             | User       | Generate on-demand digest      |
| `/api/search/backfill`            | POST            | Admin      | Backfill vector embeddings     |
| `/api/digests`                    | GET             | User       | List digest entries            |
| `/api/invites`                    | GET, POST, DEL  | Admin      | Manage invite keys             |
| `/api/users`                      | GET, PATCH      | Admin      | User management                |
| `/api/storage`                    | GET             | User       | Storage stats per camera       |
| `WS /ws`                          |                 | User       | Real-time event stream         |

Full interactive docs available at `/docs` when the API is running.

## Agent eval suite

The agentic Q&A surface is gated by a 30-fixture eval suite under
`tests/agent_fixtures/`. The harness is fully mocked so it runs in
seconds without a database or LLM provider, and Phase 1 ships when
at least 27 of 30 fixtures pass on three consecutive nightly runs.

Run locally.

```
python -m pytest tests/test_agent_eval.py -v
```

CI runs the same command nightly via `.github/workflows/agent-eval.yml`
and posts the per-fixture report (`.eval-report.md`) as a PR comment
when the workflow is triggered on a pull request. See
`docs/agent-eval.md` for the fixture format and how to add new cases.

## License

Proprietary. All rights reserved.
