import asyncio
import logging
import time
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse

from shared.config import settings
from services.api.routes import admin_stats, audio, auth, cameras, conversations, detection_models, digests, events, invites, notifications, observations, ollama_deploy, persons, providers, recordings, rules, search, summaries, system, timeline, transcripts, users
from services.digest.scheduler import run_digest_loop
from services.api.ws import router as ws_router

logger = logging.getLogger(__name__)

START_TIME = time.time()


def _run_migrations() -> None:
    """Apply any pending alembic migrations on startup."""
    try:
        from alembic import command
        from alembic.config import Config

        repo_root = Path(__file__).resolve().parents[2]
        ini_path = repo_root / "alembic.ini"
        if not ini_path.exists():
            logger.warning("alembic.ini not found at %s, skipping auto-migrate", ini_path)
            return
        cfg = Config(str(ini_path))
        cfg.set_main_option("script_location", str(repo_root / "alembic"))
        logger.info("Running alembic upgrade head")
        command.upgrade(cfg, "head")
        logger.info("Migrations up to date")
    except Exception as exc:
        logger.exception("Auto-migration failed: %s", exc)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_run_migrations)
    task = asyncio.create_task(run_digest_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Nurby API",
    version="0.1.0",
    description="AI camera monitoring platform",
    lifespan=lifespan,
)

_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
]
# Allow extra origins from CORS_ORIGINS env var (comma-separated)
if settings.cors_origins:
    _CORS_ORIGINS.extend(o.strip() for o in settings.cors_origins.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_check():
    """Basic health check for monitoring."""
    from shared.database import async_session
    try:
        from sqlalchemy import text
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    status = "healthy" if db_ok else "degraded"
    code = 200 if db_ok else 503
    return JSONResponse(
        status_code=code,
        content={"status": status, "database": db_ok, "uptime_seconds": round(time.time() - START_TIME, 1)},
    )

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(invites.router, prefix="/api/invites", tags=["invites"])
app.include_router(system.router, prefix="/api", tags=["system"])
app.include_router(cameras.router, prefix="/api/cameras", tags=["cameras"])
app.include_router(detection_models.router, prefix="/api/detection-models", tags=["detection-models"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(observations.router, prefix="/api/observations", tags=["observations"])
app.include_router(persons.router, prefix="/api/persons", tags=["persons"])
app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(providers.router, prefix="/api/providers", tags=["providers"])
app.include_router(ollama_deploy.router, prefix="/api/ollama", tags=["ollama"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(digests.router, prefix="/api/digests", tags=["digests"])
app.include_router(transcripts.router, prefix="/api/transcripts", tags=["transcripts"])
app.include_router(timeline.router, prefix="/api/timeline", tags=["timeline"])
app.include_router(audio.router, prefix="/api/audio", tags=["audio"])
app.include_router(summaries.router, prefix="/api/summaries", tags=["summaries"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["conversations"])
app.include_router(admin_stats.router, prefix="/api/admin", tags=["admin"])
app.include_router(ws_router, tags=["websocket"])
