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
    get_events,
    get_household_snapshot,
    get_last_sightings,
    summarize_activity,
    get_tool,
    query_observations,
    query_relationships,
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

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        r = self._rows[0]
        return r[0] if isinstance(r, tuple) else r

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
    # Journeys key persons by their display-name signature in
    # subject_key (incident_tracker.compute_signature joins names, not
    # ids). A person_id filter resolves to the display name, then
    # matches the name-signature.
    cam_id = uuid.uuid4()
    person_id = uuid.uuid4()
    peak_obs_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    journey = SimpleNamespace(
        id=uuid.uuid4(),
        subject_kind="person",
        subject_key="Dad",
        started_at=now - timedelta(minutes=10),
        last_seen_at=now,
        ended_at=now,
        segments=[
            {
                "camera_id": str(cam_id),
                "camera_name": "Kitchen",
                "occurrence_count": 3,
                "peak_observation_id": str(peak_obs_id),
            }
        ],
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(journey,)]
        # person_id -> display_name lookup: WHERE persons.id = :pk.
        if "from persons" in s and "persons.id =" in s:
            return [("Dad",)]
        # single-name -> id resolution: WHERE persons.display_name IN (...).
        if "from persons" in s and "display_name in" in s:
            return [(person_id, "Dad")]
        # peak_observation_id -> thumbnail_path.
        if "from observations" in s:
            return [(peak_obs_id, "t.jpg")]
        if "from cameras" in s:
            return [(cam_id, "Kitchen")]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await get_journeys(ctx, person_id=str(person_id), hours=24, limit=10)
    assert len(out["journeys"]) == 1
    j = out["journeys"][0]
    assert j["person_name"] == "Dad"
    assert j["person_names"] == ["Dad"]
    assert j["person_id"] == str(person_id)
    assert j["cameras"] == [{"id": str(cam_id), "name": "Kitchen"}]
    assert j["observation_count"] == 3
    assert j["thumbnail_url"] is not None


@pytest.mark.asyncio
async def test_get_journeys_exact_token_no_cross_match(monkeypatch):
    # "Ann" must NOT match a journey whose subject_key is "Anna".
    cam_id = uuid.uuid4()
    ann_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    anna_journey = SimpleNamespace(
        id=uuid.uuid4(),
        subject_kind="person",
        subject_key="Anna",  # different person, substring of "Ann" query? no - "Ann" is substring of "Anna"
        started_at=now - timedelta(minutes=5),
        last_seen_at=now,
        ended_at=now,
        segments=[{"camera_id": str(cam_id), "camera_name": "Hall", "occurrence_count": 1}],
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(anna_journey,)]  # coarse ilike %Ann% would return Anna
        if "from persons" in s and "persons.id =" in s:
            return [("Ann",)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await get_journeys(ctx, person_id=str(ann_id), hours=24, limit=10)
    # Exact-token guard drops the "Anna" journey.
    assert out["journeys"] == []


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
            return [(p1, "Dad", None), (p2, "Daddy", None)]
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


def test_summarize_activity_in_registry():
    entry = get_tool("summarize_activity")
    assert entry is not None
    assert entry["cost_class"] == "cheap"


@pytest.mark.asyncio
async def test_summarize_activity_empty_household(monkeypatch):
    async def fake_access(user, db):
        return set()

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        return []

    db = FakeDB(responder)
    out = await summarize_activity({"user": _user("admin"), "run_id": None, "db": db})
    assert out["totals"] == {
        "observations": 0,
        "persons_seen": 0,
        "rules_fired": 0,
        "unique_labels": 0,
        "vlm_pending": 0,
        "vlm_late": 0,
    }
    assert out["persons"] == []
    assert out["rules_fired"] == []
    assert out["labels"] == []


@pytest.mark.asyncio
async def test_summarize_activity_runs_per_person_path(monkeypatch):
    # Regression. the per-Person rollup queried Journey.person_id, a
    # column that does not exist (journeys key persons by name in
    # subject_key). The empty-household test never entered this loop, so
    # the AttributeError only blew up live. This exercises it with a real
    # Person + matching Journey.
    cam_id = uuid.uuid4()
    person = SimpleNamespace(
        id=uuid.uuid4(), display_name="Dad", relationship="father"
    )
    now = datetime.now(timezone.utc)
    journey = SimpleNamespace(
        id=uuid.uuid4(),
        subject_kind="person",
        subject_key="Dad",
        started_at=now - timedelta(minutes=20),
        last_seen_at=now,
        ended_at=now,
        segments=[
            {"camera_id": str(cam_id), "camera_name": "Kitchen", "occurrence_count": 4}
        ],
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return [(person,)]
        if "from journeys" in s:
            return [(journey,)]
        return []

    db = FakeDB(responder)
    out = await summarize_activity({"user": _user("admin"), "run_id": None, "db": db})
    # The per-Person path ran without an AttributeError and rolled Dad up.
    assert any(p["display_name"] == "Dad" for p in out["persons"])
    assert out["totals"]["persons_seen"] >= 1


def test_get_events_in_registry():
    entry = get_tool("get_events")
    assert entry is not None
    assert entry["cost_class"] == "cheap"
    assert entry["side_effect"] == "read"


@pytest.mark.asyncio
async def test_get_events_no_access_returns_no_events_from_other_cams(monkeypatch):
    # With no allowed cameras, events tied to a camera_id payload are
    # filtered out. Events without a camera_id pass through.
    cam_a = uuid.uuid4()
    rule_id = uuid.uuid4()

    async def fake_access(user, db):
        return set()  # no camera access

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    ev = SimpleNamespace(
        id=uuid.uuid4(),
        rule_id=rule_id,
        observation_id=None,
        fired_at=datetime.now(timezone.utc),
        action_type="webhook",
        action_status="success",
        acked_at=None,
        payload={"camera_id": str(cam_a)},
    )
    rule = SimpleNamespace(id=rule_id, name="Cat eating")

    def responder(stmt: str):
        s = stmt.lower()
        if "from rules" in s and "lower" in s:
            return []
        if "from events" in s:
            return [(ev, rule)]
        return []

    db = FakeDB(responder)
    out = await get_events({"user": _user("admin"), "run_id": None, "db": db})
    # Camera in payload, user has no access -> event hidden.
    assert out["count"] == 0


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
        "get_events",
        "get_vehicles",
        "summarize_activity",
        "summarize_window",
        "query_relationships",
        "list_rules",
        "get_incidents",
        "get_daily_digest",
        "analyze_clip",
        "analyze_frame",
    }


# ── query_relationships ─────────────────────────────────────────────


def _segment(cam_id, cam_name, started, last_seen, incident_id=None):
    return {
        "camera_id": str(cam_id),
        "camera_name": cam_name,
        "location_label": None,
        "incident_id": str(incident_id or uuid.uuid4()),
        "started_at": started.isoformat(),
        "last_seen_at": last_seen.isoformat(),
        "occurrence_count": 1,
        "peak_observation_id": None,
    }


def _journey(subject_kind, subject_key, started, last_seen, segments, transitions=None, ended=None, person_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        subject_kind=subject_kind,
        subject_key=subject_key,
        started_at=started,
        last_seen_at=last_seen,
        ended_at=ended,
        segments=segments,
        transitions=transitions or [],
        person_id=person_id,
    )


def test_query_relationships_in_registry():
    entry = get_tool("query_relationships")
    assert entry is not None
    assert entry["cost_class"] == "cheap"
    assert entry["side_effect"] == "read"


def test_query_relationships_dialects_valid():
    # The new tool must serialize cleanly to every provider dialect.
    for prov in ("anthropic", "openai", "gemini"):
        out = all_tools_for_provider(prov)
        names = [
            (e.get("name") or e.get("function", {}).get("name")) for e in out
        ]
        assert "query_relationships" in names
    anth = next(e for e in all_tools_for_provider("anthropic") if e["name"] == "query_relationships")
    jsonschema.Draft202012Validator.check_schema(anth["input_schema"])


@pytest.mark.asyncio
async def test_query_relationships_co_present(monkeypatch):
    cam_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    dad = _journey(
        "person", "Dad", now - timedelta(minutes=20), now,
        [_segment(cam_id, "Kitchen", now - timedelta(minutes=20), now)],
    )
    aisha = _journey(
        "person", "Aisha", now - timedelta(minutes=15), now - timedelta(minutes=5),
        [_segment(cam_id, "Kitchen", now - timedelta(minutes=15), now - timedelta(minutes=5))],
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return [(uuid.uuid4(), "Dad", None)] if "lower" in s else []
        if "from journeys" in s:
            # The subject query filters on subject_kind in its WHERE; the
            # candidate-others query filters only on last_seen_at. Return
            # just the subject for the former and both for the latter so
            # the overlap test is real.
            if "subject_kind =" in s:
                return [(dad,)]
            return [(dad,), (aisha,)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_relationships(ctx, subject="Dad", relation="co_present_with")
    keys = {r["subject_key"] for r in out["results"]}
    assert "Aisha" in keys
    assert "Dad" not in keys  # subject excludes itself


@pytest.mark.asyncio
async def test_query_relationships_revisited(monkeypatch):
    cam_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    # Two journeys for the same body-cluster subject_key, >30min apart.
    first = _journey(
        "cluster", "cluster-abc", now - timedelta(hours=3), now - timedelta(hours=2, minutes=30),
        [_segment(cam_id, "Front Door", now - timedelta(hours=3), now - timedelta(hours=2, minutes=30))],
        ended=now - timedelta(hours=2, minutes=30),
    )
    second = _journey(
        "cluster", "cluster-abc", now - timedelta(minutes=20), now,
        [_segment(cam_id, "Front Door", now - timedelta(minutes=20), now)],
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return []
        if "from journeys" in s:
            return [(second,), (first,)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    # "cluster-abc" is not a known label nor a person; resolve via... it
    # is neither. Use a label-style subject path is not applicable, so we
    # drive it through a person-id-shaped resolution is impossible. The
    # tool resolves only persons/labels, so to exercise revisited we use
    # a label subject that matches subject_kind=object. Re-key as object.
    first.subject_kind = "object"
    first.subject_key = "cat"
    second.subject_kind = "object"
    second.subject_key = "cat"
    out = await query_relationships(ctx, subject="cat", relation="revisited")
    assert len(out["results"]) == 1
    assert out["results"][0]["gap_minutes"] >= 30

    # Single journey -> not flagged.
    def responder_single(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(second,)]
        return []

    db2 = FakeDB(responder_single)
    ctx2 = {"user": _user("admin"), "run_id": None, "db": db2}
    out2 = await query_relationships(ctx2, subject="cat", relation="revisited")
    assert out2["results"] == []


@pytest.mark.asyncio
async def test_query_relationships_path(monkeypatch):
    cam_a, cam_b = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    j = _journey(
        "object", "cat", now - timedelta(minutes=10), now,
        [
            _segment(cam_a, "Kitchen", now - timedelta(minutes=10), now - timedelta(minutes=6)),
            _segment(cam_b, "Hallway", now - timedelta(minutes=5), now),
        ],
        transitions=[
            {
                "from_camera_id": str(cam_a),
                "from_camera_name": "Kitchen",
                "to_camera_id": str(cam_b),
                "to_camera_name": "Hallway",
                "gap_seconds": 60,
                "ts": (now - timedelta(minutes=5)).isoformat(),
            }
        ],
    )

    async def fake_access(user, db):
        return {cam_a, cam_b}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(j,)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_relationships(ctx, subject="cat", relation="path")
    assert len(out["results"]) == 1
    hop = out["results"][0]
    assert hop["from_camera_name"] == "Kitchen"
    assert hop["to_camera_name"] == "Hallway"


@pytest.mark.asyncio
async def test_query_relationships_seen_with_label(monkeypatch):
    cam_id = uuid.uuid4()
    person_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    j = _journey(
        "person", "Dad", now - timedelta(minutes=30), now,
        [_segment(cam_id, "Kitchen", now - timedelta(minutes=30), now)],
    )
    obs = SimpleNamespace(
        id=uuid.uuid4(),
        camera_id=cam_id,
        started_at=now - timedelta(minutes=10),
        vlm_description="dad with the dog",
        thumbnail_path="t.jpg",
        object_detections={"objects": [{"label": "dog"}]},
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return [(person_id, "Dad", None)]
        if "from journeys" in s:
            return [(j,)]
        if "from observations" in s:
            return [(obs,)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_relationships(ctx, subject="Dad", relation="seen_with_label", object="dog")
    assert len(out["results"]) == 1
    assert out["results"][0]["label"] == "dog"
    assert out["results"][0]["observation_id"] == str(obs.id)


@pytest.mark.asyncio
async def test_query_relationships_disambiguation(monkeypatch):
    cam_id = uuid.uuid4()
    p1, p2 = uuid.uuid4(), uuid.uuid4()

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return [(p1, "Dad", None), (p2, "Daddy", None)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_relationships(ctx, subject="Dad", relation="co_present_with")
    assert out["results"] == []
    assert {d["display_name"] for d in out["disambiguation"]} == {"Dad", "Daddy"}


@pytest.mark.asyncio
async def test_query_relationships_access_filter(monkeypatch):
    # The subject's journey only touches a camera the user cannot see ->
    # no results.
    visible_cam = uuid.uuid4()
    hidden_cam = uuid.uuid4()
    now = datetime.now(timezone.utc)
    j = _journey(
        "object", "cat", now - timedelta(minutes=10), now,
        [_segment(hidden_cam, "Garage", now - timedelta(minutes=10), now)],
    )

    async def fake_access(user, db):
        return {visible_cam}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from journeys" in s:
            return [(j,)]
        return []

    db = FakeDB(responder)
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_relationships(ctx, subject="cat", relation="path")
    assert out["results"] == []


@pytest.mark.asyncio
async def test_query_relationships_clamps(monkeypatch):
    async def fake_access(user, db):
        return set()  # short-circuit before any journey query

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)
    db = FakeDB(lambda s: [])
    ctx = {"user": _user("admin"), "run_id": None, "db": db}
    out = await query_relationships(ctx, subject="cat", relation="path", hours=9999, limit=999)
    assert out["hours"] == 720  # clamped
    assert out["results"] == []


# ── household nickname (view-layer alias) ───────────────────────────


@pytest.mark.asyncio
async def test_get_last_sightings_shows_nickname_and_matches_it(monkeypatch):
    # A person typed as "Mommy" (their nickname) resolves, and the
    # rendered display_name is the nickname, not the canonical name.
    cam_id = uuid.uuid4()
    person = SimpleNamespace(
        id=uuid.uuid4(), display_name="Salma Bekom", nickname="Mommy"
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return [(person,)]
        return []  # no journeys

    db = FakeDB(responder)
    out = await get_last_sightings(
        {"user": _user("admin"), "run_id": None, "db": db}, person_name="Mommy"
    )
    assert out["persons"]
    assert out["persons"][0]["display_name"] == "Mommy"


@pytest.mark.asyncio
async def test_get_household_snapshot_shows_nickname(monkeypatch):
    cam = _camera("Kitchen")
    person = SimpleNamespace(
        id=uuid.uuid4(),
        display_name="Salma Bekom",
        nickname="Mommy",
        relationship="mother",
    )

    async def fake_access(user, db):
        return {cam.id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from cameras" in s:
            return [(cam,)]
        if "from persons" in s:
            # Full ORM person select carries photo_path; the alias-map
            # select is just (display_name, nickname).
            if "persons.photo_path" in s:
                return [(person,)]
            return [("Salma Bekom", "Mommy")]
        return []  # no journeys, no observations

    db = FakeDB(responder)
    out = await get_household_snapshot(
        {"user": _user("admin"), "run_id": None, "db": db}
    )
    assert out["persons"]
    assert out["persons"][0]["display_name"] == "Mommy"


@pytest.mark.asyncio
async def test_get_last_sightings_label_path_uses_started_at(monkeypatch):
    # Regression. the label branch read row.timestamp, which an
    # Observation has never had (the column is started_at). It only
    # fires when a labelled observation exists, so the empty-DB tests
    # missed it; the realistic seed surfaced it live.
    cam_id = uuid.uuid4()
    cam = _camera("Front Door")
    cam.id = cam_id
    obs = SimpleNamespace(
        id=uuid.uuid4(),
        camera_id=cam_id,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        thumbnail_path=None,
        object_detections={"objects": [{"label": "person"}]},
    )

    async def fake_access(user, db):
        return {cam_id}

    monkeypatch.setattr(tools_mod, "accessible_camera_ids", fake_access)

    def responder(stmt: str):
        s = stmt.lower()
        if "from persons" in s:
            return []
        if "from observations" in s:
            return [(obs,)]
        if "from cameras" in s:
            return [(cam,)]
        return []

    db = FakeDB(responder)
    out = await get_last_sightings({"user": _user("admin"), "run_id": None, "db": db})
    assert out["labels"]
    seen = [lb for lb in out["labels"] if lb["last_seen_at"]]
    assert seen, "expected at least one label with a last_seen_at"
    assert seen[0]["last_camera_name"] == "Front Door"
