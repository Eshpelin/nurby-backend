# Nurby

AI camera monitoring platform that turns any existing IP camera into a context-aware sentry. Connect cameras, point Nurby at a vision language model (local or cloud), and define what to watch, who to recognize, what events trigger alerts, and how to search historical footage.

Privacy first. Developer friendly. Model agnostic.

## Architecture

Nurby uses a three-layer architecture with six services running in a single Docker Compose stack.

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (Next.js)                                     │
│  Cameras · Timeline · Rules · Search                    │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  API (FastAPI)         │  Streaming (MediaMTX)          │
│  REST + WebSocket      │  WebRTC · HLS · RTSP           │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  Layer 3: Events & Automation                           │
│  Rule evaluation · Alerts · Webhooks · Timeline         │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Perception & Reasoning                        │
│  YOLO detection · Face recognition · VLM invocation     │
├─────────────────────────────────────────────────────────┤
│  Layer 1: Ingestion                                     │
│  RTSP decode · Motion detection · Recording · Clips     │
└─────────────────────────────────────────────────────────┘
         │                          │
    ┌────▼────┐              ┌──────▼──────┐
    │ Postgres │              │    Redis    │
    │ pgvector │              │   Streams   │
    └─────────┘              └─────────────┘
```

## Tech Stack

**Backend.** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, OpenCV, PyAV, Postgres with pgvector, Redis

**Frontend.** Next.js 16, React 19, Tailwind CSS 4, Geist typography, dark mode

**Infrastructure.** Docker Compose, MediaMTX (WebRTC/HLS relay)

## Project Structure

```
nurby-backend/
├── services/
│   ├── api/            # FastAPI REST + WebSocket service
│   ├── ingestion/      # RTSP stream processing, motion detection, recording
│   ├── perception/     # Object detection, face recognition, VLM (future)
│   ├── events/         # Rule evaluation, alerts, webhooks (future)
│   └── streaming/      # Live video relay config (future)
├── shared/
│   ├── config.py       # App settings via pydantic-settings
│   ├── database.py     # Async SQLAlchemy engine and session
│   ├── models.py       # All Postgres models
│   └── schemas.py      # Pydantic request/response schemas
├── frontend/           # Next.js app
│   └── src/app/        # Four core screens (cameras, timeline, rules, search)
├── alembic/            # Database migrations
├── config/             # MediaMTX and service configs
├── docker-compose.yml  # Full stack orchestration
└── pyproject.toml      # Python dependencies
```

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- Node.js 20+ (for frontend development)

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

### Database migrations

```bash
# Generate a new migration after model changes
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

## API Overview

| Endpoint                          | Methods         | Description                    |
|-----------------------------------|-----------------|--------------------------------|
| `GET /api/status`                 |                 | System health and camera counts|
| `/api/cameras`                    | GET, POST       | List and add cameras           |
| `/api/cameras/{id}`               | GET, PATCH, DEL | Manage individual cameras      |
| `/api/recordings`                 | GET             | Browse recorded segments       |
| `/api/observations`               | GET             | Browse AI observations         |
| `/api/rules`                      | GET, POST       | List and create automation rules|
| `/api/rules/{id}`                 | GET, PATCH, DEL | Manage individual rules        |
| `/api/events`                     | GET             | Browse fired events            |
| `/api/events/{id}/acknowledge`    | POST            | Acknowledge an alert           |
| `/api/providers`                  | GET, POST       | Manage VLM providers           |
| `WS /ws`                         |                 | Real-time event stream         |

Full interactive docs available at `/docs` when the API is running.

## Development Phases

1. **Plumbing** (current). Single camera streaming, RTSP ingestion, recording, live view
2. **Basic Perception**. Local object detection, VLM provider abstraction, AI-generated descriptions
3. **People**. Face detection and recognition, identity management, consent flows
4. **Rules & Automation**. Trigger/condition/action rule builder, webhooks, notifications
5. **Search & Summarisation**. Semantic search, natural language queries, periodic digests

## License

Proprietary. All rights reserved.
