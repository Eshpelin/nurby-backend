import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.api.routes import auth, cameras, events, invites, observations, persons, providers, recordings, rules, search, system, users
from services.api.ws import router as ws_router

START_TIME = time.time()

app = FastAPI(
    title="Nurby API",
    version="0.1.0",
    description="AI camera monitoring platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(invites.router, prefix="/api/invites", tags=["invites"])
app.include_router(system.router, prefix="/api", tags=["system"])
app.include_router(cameras.router, prefix="/api/cameras", tags=["cameras"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(observations.router, prefix="/api/observations", tags=["observations"])
app.include_router(persons.router, prefix="/api/persons", tags=["persons"])
app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(providers.router, prefix="/api/providers", tags=["providers"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(ws_router, tags=["websocket"])
