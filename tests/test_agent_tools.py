"""Tests for the agent tool registry, access filter, and dialect adapter.

These tests run without a real Postgres. ``AsyncSession.execute`` is
stubbed via a tiny in-memory ``FakeDB`` that returns canned rows for
each select() the tools issue. The intent is to cover the contract the
Wave 2 driver depends on (access filter, disambiguation, window
clamping, dialect emission) rather than the SQL itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jsonschema
import pytest

from services.agent import access as access_mod
from services.agent import tools as tools_mod
from services.agent.tools import (
    TOOL_REGISTRY,
    all_tools_for_provider,
    analyze_clip,
    analyze_frame,
    get_camera_layout,
    get_journeys,
    get_household_snapshot,
    get_last_sightings,
    get_tool,
    query_observations,
)


# ── helpers ─────────────────────────────────────────────────────────


def _user(role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)


def _camera(name: str, location: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        location_label=location,
        scene_mode="indoor",
        status="live",
        timezone=None,
        display_order=0,
        created_at=datetime.now(timezone.utc),
    )


class FakeResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        # Each row is either (obj,) or a bare obj. scalars() should
        # return the first column.
        scalars = []
        for r in self._rows:
            if isinstance(r, tuple):
                scalars.append(r[0])
            else:
                scalars.append(r)
        return FakeScalars(scalars)


class FakeScalars:
    def __init__(self, items: list[Any]):
        self._items = items

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class FakeDB:
    """Stub AsyncSession. Override ``responder`` to return rows keyed
    by the table the select() touches. We sniff the statement string."""

    def __init__(self, responder):
        self._responder = responder
        self._gets: dict[Any, Any] = {}

    async def execute(self, stmt):
        return FakeResult(self._responder(str(stmt)))

    async def get(self, model, ident):
        return self._gets.get(ident)


def _empty_camera_responder(stmt: str) -> list[Any]:
    return []


# ── access filter ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_access_filter_admin_sees_all():
    cam_ids = [uuid.uuid4() for _ in range(3)]

    def responder(stmt: str):
        s = stmt.lower()
        if "from cameras" in s and "camera.id" in s.replace("cameras.id", "camera.id"):
            return [(cid,) for cid in cam_ids]
        if "from cameras" in s:
            return [(cid,) for cid in cam_ids]
        return []

    db = FakeDB(responder)
    user = _user("admin")
    result = await access_mod.accessible_camera_ids(user, db)
    assert result == set(cam_ids)


@pytest.mark.asyncio
async def test_access_filter_viewer_with_grants_sees_subset():
    cam_ids = [uuid.uuid4() for _ in range(3)]
    granted = {cam_ids[0], cam_ids[1]}

    def responder(stmt: str):
        s = stmt.lower()
        if "user_camera_access" in s:
            return [(cid,) for cid in granted]
        if "from cameras" in s:
            return [(cid,) for cid in cam_ids]
        return []

    db = FakeDB(responder)
    user = _user("viewer")
    result = await access_mod.accessible_camera_ids(user, db)
    assert result == granted


@pytest.mark.asyncio
async def test_access_filter_viewer_no_grants_falls_through_to_all():
    cam_ids = [uuid.uuid4() for _ in range(2)]

    def responder(stmt: str):
        s = stmt.lower()
        if "user_camera_access" in s:
            return []  # no grants
        if "from cameras" in s:
            return [(cid,) for cid in cam_ids]
        return []

    db = FakeDB(responder)
    user = _user("viewer")
    result = await access_mod.accessible_camera_ids(user, db)
    assert result == set(cam_ids)


# ── tool happy paths ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_observations_returns_observations(monkeypatch):
    cam_id = uuid.uuid4()
    obs = SimpleNamespace(
        id=uuid.uuid4(),
        camera_id=cam_id,
        started_at=datetime.now(timezone.utc),
        vlm_description="a person at the door",
        thumbnail_path="thumbs/x.jpg",
        object_detections={"objects": [{"label": "person"}]},
        person_detections={"faces": [{"person_name": "Dad"}]},
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    async def fake_embed(text):
        return None  # force keyword path

    monkeypatch.setattr(tools_mod, "_embed_query", fake_embed)

    def responder(stmt: str):
        s = stmt.lower()
        if "from observations" in s:
            return [(obs,)]
        if "from cameras" in s:
            return [(cam_id, "Front Door")]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_observations(ctx, query="person at door", hours=24, limit=10)
    assert out["count"] == 1
    assert out["observations"][0]["camera_name"] == "Front Door"
    assert out["observations"][0]["person_names"] == ["Dad"]


@pytest.mark.asyncio
async def test_get_camera_layout_infers_roles(monkeypatch):
    kitchen = _camera("Kitchen Cam", "kitchen counter")
    door = _camera("Front Door", None)
    other = _camera("Random", None)

    async def fake_access(user, db):
        return {kitchen.id, door.id, other.id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        if "from cameras" in stmt.lower():
            return [(kitchen,), (door,), (other,)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await get_camera_layout(ctx)
    roles = {c["name"]: c["role"] for c in out["cameras"]}
    assert roles["Kitchen Cam"] == "kitchen"
    assert roles["Front Door"] == "entry"
    assert roles["Random"] == "other"


@pytest.mark.asyncio
async def test_get_journeys_happy(monkeypatch):
    cam_id = uuid.uuid4()
    person_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    journey = SimpleNamespace(
        id=uuid.uuid4(),
        subject_kind="person",
        subject_key=str(person_id),
        started_at=now - timedelta(minutes=10),
        last_seen_at=now,
        ended_at=now,
        segments=[
            {"camera_id": str(cam_id), "observation_count": 3, "thumbnail_path": "t.jpg"}
        ],
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(journey,)]
        if "from persons" in s and "person.id" in s.replace("persons.id", "person.id") or "from persons" in s:
            return [(person_id, "Dad")]
        if "from cameras" in s:
            return [(cam_id, "Kitchen")]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await get_journeys(ctx, person_id=str(person_id), hours=24, limit=10)
    assert len(out["journeys"]) == 1
    j = out["journeys"][0]
    assert j["person_name"] == "Dad"
    assert j["cameras"] == [{"id": str(cam_id), "name": "Kitchen"}]
    assert j["observation_count"] == 3


@pytest.mark.asyncio
async def test_get_journeys_disambiguation(monkeypatch):
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    cam_id = uuid.uuid4()

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return [(p1, "Dad"), (p2, "Daddy")]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await get_journeys(ctx, person_name="Dad", hours=24)
    assert out["journeys"] == []
    assert "disambiguation" in out
    assert {d["display_name"] for d in out["disambiguation"]} == {"Dad", "Daddy"}


# ── window / limit bounds ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_hours_too_large_clamped(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_access(user, db):
        return set()  # short-circuit. we just want to confirm no raise

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)
    db = FakeDB(_empty_camera_responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    # 9999 hours should clamp silently to 720.
    out = await query_observations(ctx, query="x", hours=9999, limit=10)
    assert out == {"count": 0, "observations": []}


@pytest.mark.asyncio
async def test_hours_too_small_rejected(monkeypatch):
    db = FakeDB(_empty_camera_responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    with pytest.raises(ValueError):
        await query_observations(ctx, query="x", hours=0)


# ── analyzer fallback when wave 1c is absent ───────────────────────


@pytest.mark.asyncio
async def test_analyze_clip_returns_analyzer_not_ready(monkeypatch):
    cam_id = uuid.uuid4()

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    # Force the import inside analyze_clip to fail by removing any
    # installed analyzer module attribute. The except branch in
    # analyze_clip handles ImportError uniformly.
    import sys

    monkeypatch.setitem(sys.modules, "services.agent.analyzer", None)

    db = FakeDB(_empty_camera_responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    now = datetime.now(timezone.utc)
    out = await analyze_clip(
        ctx,
        camera_id=str(cam_id),
        time_from=(now - timedelta(seconds=10)).isoformat(),
        time_to=now.isoformat(),
        question="anything?",
    )
    assert out["error"] == "analyzer_not_ready"


@pytest.mark.asyncio
async def test_analyze_frame_access_denied(monkeypatch):
    cam_id = uuid.uuid4()
    obs_id = uuid.uuid4()
    obs = SimpleNamespace(id=obs_id, camera_id=cam_id, thumbnail_path="t.jpg")

    async def fake_access(user, db):
        return set()  # no access

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)
    db = FakeDB(_empty_camera_responder)
    db._gets[obs_id] = obs
    ctx = {"user": _user("viewer"), "run_id": None, "db": db}
    out = await analyze_frame(ctx, observation_id=str(obs_id), question="?")
    assert out["error"] == "camera_access_denied"


# ── provider dialect adapter ───────────────────────────────────────


def test_anthropic_dialect_schema():
    out = all_tools_for_provider("anthropic")
    assert len(out) == len(TOOL_REGISTRY)
    for entry in out:
        assert set(entry.keys()) == {"name", "description", "input_schema"}
        jsonschema.Draft202012Validator.check_schema(entry["input_schema"])


def test_openai_dialect_schema():
    out = all_tools_for_provider("openai")
    for entry in out:
        assert entry["type"] == "function"
        fn = entry["function"]
        assert set(fn.keys()) == {"name", "description", "parameters"}
        jsonschema.Draft202012Validator.check_schema(fn["parameters"])


def test_gemini_dialect_schema():
    out = all_tools_for_provider("gemini")
    for entry in out:
        assert set(entry.keys()) == {"name", "description", "parameters"}
        jsonschema.Draft202012Validator.check_schema(entry["parameters"])


def test_unknown_dialect_raises():
    with pytest.raises(ValueError):
        all_tools_for_provider("totally-unknown")


@pytest.mark.asyncio
async def test_get_last_sightings_no_access_returns_empty(monkeypatch):
    async def fake_access(user, db):
        return set()

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)
    out = await get_last_sightings({"user": _user("admin"), "run_id": None, "db": FakeDB(lambda s: [])}, since_days=30)
    assert out == {"persons": [], "labels": [], "since_days": 30}


@pytest.mark.asyncio
async def test_get_last_sightings_clamps_since_days(monkeypatch):
    async def fake_access(user, db):
        return set()

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)
    out = await get_last_sightings({"user": _user("admin"), "run_id": None, "db": FakeDB(lambda s: [])}, since_days=9999)
    assert out["since_days"] == 365
    out = await get_last_sightings({"user": _user("admin"), "run_id": None, "db": FakeDB(lambda s: [])}, since_days=0)
    assert out["since_days"] == 1


@pytest.mark.asyncio
async def test_get_household_snapshot_no_access_returns_empty(monkeypatch):
    async def fake_access(user, db):
        return set()

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)
    out = await get_household_snapshot({"user": _user("admin"), "run_id": None, "db": FakeDB(lambda s: [])})
    assert out["cameras"] == []
    assert out["persons"] == []
    assert out["active_journeys"] == []
    assert "now_iso" in out


def test_get_household_snapshot_in_registry():
    entry = get_tool("get_household_snapshot")
    assert entry is not None
    assert entry["cost_class"] == "cheap"


def test_get_last_sightings_in_registry():
    entry = get_tool("get_last_sightings")
    assert entry is not None
    assert entry["side_effect"] == "read"
    assert entry["cost_class"] == "cheap"


def test_registry_lookup():
    assert get_tool("query_observations") is not None
    assert get_tool("doesnotexist") is None
    names = {t["name"] for t in TOOL_REGISTRY}
    assert names == {
        "query_observations",
        "get_journeys",
        "get_camera_layout",
        "get_household_snapshot",
        "get_last_sightings",
        "analyze_clip",
        "analyze_frame",
    }
