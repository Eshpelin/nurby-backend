"""Unit tests for the agent VLM analyzer (Wave 1C).

The analyzer is exercised without Postgres, ffmpeg, or a real VLM
provider. All side-effectful boundaries are monkeypatched. The
behavioural contract these tests pin down.

* ``normalize_question`` is deterministic and idempotent.
* ``select_frames`` returns the bucket size required by the design doc
  decision tree for every window-duration regime.
* A cache miss writes the row and a follow-up cache hit returns the
  same answer WITHOUT calling the VLM again.
* The redaction layer fires BEFORE the VLM ever sees a frame; a
  monkeypatched VLM stub asserts the bytes match the redacted output.
* The ``cannot_tell`` verdict + low confidence is preserved verbatim
  (the analyzer must never silently upgrade it).
* A window larger than ``agent_max_clip_minutes`` returns the
  structured ``clip_too_long`` error without calling ffmpeg or the VLM.
* When the underlying recording is gone, ``media_evicted`` is returned.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from services.agent import analyzer as A

# ────────────────────────────────────────────────────────────────────
# normalize_question
# ────────────────────────────────────────────────────────────────────


def test_normalize_question_is_deterministic():
    a = A.normalize_question("  Did Daddy EAT today?? ")
    b = A.normalize_question("did daddy eat today")
    assert a == b == "did daddy eat today"


def test_normalize_question_collapses_internal_whitespace_and_punct():
    s = A.normalize_question("Hello,\tworld!!\n\nIs    this OK?")
    assert s == "hello world is this ok"


def test_question_hash_changes_with_meaning_not_with_formatting():
    h1 = A.question_hash("Did dad eat?")
    h2 = A.question_hash("  did   dad eat ")
    h3 = A.question_hash("did mom eat")
    assert h1 == h2
    assert h1 != h3


# ────────────────────────────────────────────────────────────────────
# select_frames
# ────────────────────────────────────────────────────────────────────


def test_select_frames_short_clip_under_30s_is_capped_at_8():
    frames = A.select_frames(20)
    assert 1 <= len(frames) <= 8
    assert all(0 <= f <= 20 for f in frames)


def test_select_frames_under_30s_emits_at_least_one_frame():
    frames = A.select_frames(3)
    assert len(frames) >= 1
    assert 0 <= frames[0] <= 3


def test_select_frames_medium_clip_uses_six_uniform():
    frames = A.select_frames(120)
    assert len(frames) == 6
    # Even spacing means strictly monotonic and inside the window.
    assert frames == sorted(frames)
    assert all(0 < f < 120 for f in frames)


def test_select_frames_long_clip_uses_eight_uniform():
    frames = A.select_frames(1200)  # 20 min
    assert len(frames) == 8


def test_select_frames_extra_long_clip_uses_head_plus_tail():
    frames = A.select_frames(3600)  # 60 min
    assert len(frames) == 12
    # First eight in the first 10 min.
    assert all(f < 600 for f in frames[:8])
    assert all(f >= 600 for f in frames[8:])


# ────────────────────────────────────────────────────────────────────
# Cache miss → hit round-trip
# ────────────────────────────────────────────────────────────────────


class _StubVLM:
    """Deterministic VLM stub. Records every call so a follow-up cache
    hit can be detected by checking call_count == 1.
    """

    def __init__(self, response: dict):
        self.response = response
        self.call_count = 0
        self.last_frames: list[np.ndarray] | None = None

    async def __call__(self, provider, frames, question):
        self.call_count += 1
        self.last_frames = frames
        # Inject the usage block the real callers strip out.
        return {**self.response, "_usage": {"input_tokens": 100, "output_tokens": 20}}


class _CacheStore:
    """In-memory stand-in for vlm_frame_analysis."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    async def lookup(self, db, *, observation_id, recording_id, qhash, provider_id, model):
        key = (str(observation_id) if observation_id else None,
               str(recording_id) if recording_id else None,
               qhash, str(provider_id) if provider_id else None, model)
        return self.rows.get(key)

    async def write(self, db, *, observation_id, recording_id, qhash, question_text,
                    provider_id, model, response_json, confidence, cost_tokens_in,
                    cost_tokens_out, cost_cents, thumbnail_path):
        key = (str(observation_id) if observation_id else None,
               str(recording_id) if recording_id else None,
               qhash, str(provider_id) if provider_id else None, model)
        self.rows[key] = {
            "id": uuid.uuid4(),
            "response_json": response_json,
            "confidence": confidence,
            "cost_tokens_in": cost_tokens_in,
            "cost_tokens_out": cost_tokens_out,
            "cost_cents": cost_cents,
            "thumbnail_path": thumbnail_path,
        }


def _fake_frame(value: int = 0) -> np.ndarray:
    arr = np.full((4, 4, 3), value, dtype=np.uint8)
    return arr


def _patch_pipeline(monkeypatch, vlm_stub: _StubVLM, *, frames: list[np.ndarray] | None = None,
                    cache_store: _CacheStore | None = None, observation=None,
                    recording=None, camera=None):
    """Replace every IO boundary with deterministic stubs."""
    cache_store = cache_store or _CacheStore()

    async def _fake_extract(rec, sample_secs):
        return list(frames) if frames is not None else []

    async def _fake_extract_one(path, ts):
        return (frames[0].copy() if frames else None)

    async def _fake_redact(frames, camera_id, db):
        # Return a recognizable transformation so a downstream
        # assertion can confirm the VLM saw the redacted bytes.
        out = []
        for f in frames:
            r = f.copy()
            r[:] = 99
            out.append(r)
        return out, [{"privacy_zones": 1, "blurred_person_ids": [], "nudenet_regions": 0}]

    stable_provider = SimpleNamespace(
        id=uuid.uuid4(), name="stub", kind="openai", base_url="http://x",
        api_key="k", default_model="m", active=True,
        max_input_tokens=None, max_output_tokens=None,
    )

    async def _fake_resolve_provider(db, *, explicit_id, camera):
        return stable_provider

    async def _fake_get(model, pk):
        # Generic .get for the db stub.
        if model.__name__ == "Observation":
            return observation
        if model.__name__ == "Camera":
            return camera
        if model.__name__ == "Recording":
            return recording
        return None

    db = SimpleNamespace(get=_fake_get, execute=None, commit=None, rollback=None)
    # _find_recording_for_observation issues db.execute; provide an
    # awaitable that returns a tiny shim.
    class _Scalar:
        def __init__(self, val): self._val = val
        def scalar_one_or_none(self): return self._val

    async def _fake_execute(stmt, *args, **kwargs):
        return _Scalar(recording)

    db.execute = _fake_execute

    monkeypatch.setattr(A, "extract_frames_from_recording", _fake_extract)
    monkeypatch.setattr(A, "_ffmpeg_extract_one", _fake_extract_one)
    monkeypatch.setattr(A, "_redact_frames", _fake_redact)
    monkeypatch.setattr(A, "_resolve_provider", _fake_resolve_provider)
    monkeypatch.setattr(A, "call_vlm_structured", vlm_stub)
    monkeypatch.setattr(A, "cache_lookup", cache_store.lookup)
    monkeypatch.setattr(A, "cache_write", cache_store.write)
    monkeypatch.setattr(A, "_save_thumbnails", lambda *a, **kw: None)

    async def _fake_record(*a, **kw):
        return None
    monkeypatch.setattr(A, "_record_vlm_call", _fake_record)

    # Bypass the analyzer's "open my own db" path.
    captured_db = db
    class _CMSession:
        async def __aenter__(self_): return captured_db
        async def __aexit__(self_, *exc): return False
    monkeypatch.setattr(A, "async_session", lambda: _CMSession())

    return cache_store, db


@pytest.mark.asyncio
async def test_cache_miss_then_hit_does_not_recall_vlm(monkeypatch):
    obs_id = uuid.uuid4()
    cam_id = uuid.uuid4()
    observation = SimpleNamespace(
        id=obs_id, camera_id=cam_id,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=15),
        ended_at=datetime.now(timezone.utc),
        clip_path=None,
    )
    recording = SimpleNamespace(
        id=uuid.uuid4(), camera_id=cam_id,
        started_at=observation.started_at - timedelta(seconds=1),
        ended_at=observation.ended_at + timedelta(seconds=1),
        duration_seconds=20.0, file_path="x.mp4",
    )
    camera = SimpleNamespace(id=cam_id, vlm_provider_id=None)
    stub = _StubVLM({
        "verdict": "yes", "summary": "Person eating.",
        "confidence": 0.88, "evidence": [{"frame_index": 0, "description": "spoon"}],
    })
    cache, db = _patch_pipeline(
        monkeypatch, stub, frames=[_fake_frame(0)], observation=observation,
        recording=recording, camera=camera,
    )
    ctx = SimpleNamespace(run_id=uuid.uuid4(), db=db, user=None, tool_call_id=None)

    first = await A.analyze_frame_target(ctx, obs_id, "Did dad eat?")
    assert first.cache_hit is False
    assert first.answer["verdict"] == "yes"
    assert stub.call_count == 1

    second = await A.analyze_frame_target(ctx, obs_id, "Did dad eat?")
    assert second.cache_hit is True
    assert second.answer["verdict"] == "yes"
    # No second VLM call should have happened.
    assert stub.call_count == 1


@pytest.mark.asyncio
async def test_redaction_runs_before_vlm_sees_frames(monkeypatch):
    obs_id = uuid.uuid4()
    cam_id = uuid.uuid4()
    observation = SimpleNamespace(
        id=obs_id, camera_id=cam_id,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        ended_at=datetime.now(timezone.utc),
        clip_path=None,
    )
    recording = SimpleNamespace(
        id=uuid.uuid4(), camera_id=cam_id,
        started_at=observation.started_at, ended_at=observation.ended_at,
        duration_seconds=10.0, file_path="x.mp4",
    )
    camera = SimpleNamespace(id=cam_id, vlm_provider_id=None)
    raw_frame = _fake_frame(0)
    stub = _StubVLM({
        "verdict": "no", "summary": "Nothing.", "confidence": 0.7, "evidence": [],
    })
    _patch_pipeline(
        monkeypatch, stub, frames=[raw_frame], observation=observation,
        recording=recording, camera=camera,
    )
    ctx = SimpleNamespace(run_id=uuid.uuid4(), db=None, user=None, tool_call_id=None)

    await A.analyze_frame_target(ctx, obs_id, "anything")
    assert stub.last_frames is not None
    # The redaction stub replaces every pixel with 99. Any unmodified
    # raw byte (0) reaching the VLM would mean the redaction layer was
    # skipped.
    assert (stub.last_frames[0] == 99).all()


@pytest.mark.asyncio
async def test_cannot_tell_low_confidence_is_preserved(monkeypatch):
    obs_id = uuid.uuid4()
    cam_id = uuid.uuid4()
    observation = SimpleNamespace(
        id=obs_id, camera_id=cam_id,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        ended_at=datetime.now(timezone.utc),
        clip_path=None,
    )
    recording = SimpleNamespace(
        id=uuid.uuid4(), camera_id=cam_id,
        started_at=observation.started_at, ended_at=observation.ended_at,
        duration_seconds=5.0, file_path="x.mp4",
    )
    camera = SimpleNamespace(id=cam_id, vlm_provider_id=None)
    stub = _StubVLM({
        "verdict": "cannot_tell",
        "summary": "Frame too dark.",
        "confidence": 0.12,
        "evidence": [],
    })
    _patch_pipeline(
        monkeypatch, stub, frames=[_fake_frame()], observation=observation,
        recording=recording, camera=camera,
    )
    ctx = SimpleNamespace(run_id=uuid.uuid4(), db=None, user=None, tool_call_id=None)
    res = await A.analyze_frame_target(ctx, obs_id, "did the cat go outside")
    assert res.answer["verdict"] == "cannot_tell"
    assert res.answer["confidence"] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_long_clip_is_rejected_with_structured_error(monkeypatch):
    async def _fake_get_setting(key, default=None):
        if key == "agent_max_clip_minutes":
            return 10
        return default
    monkeypatch.setattr(A, "get_setting", _fake_get_setting)

    cam_id = uuid.uuid4()
    ctx = SimpleNamespace(run_id=uuid.uuid4(), db=None, user=None, tool_call_id=None)
    now = datetime.now(timezone.utc)
    res = await A.analyze_clip_target(
        ctx, cam_id, now - timedelta(minutes=30), now, "What happened?"
    )
    assert res.error == "clip_too_long"
    assert res.answer.get("error") == "clip_too_long"
    assert res.tokens_in == 0
    assert res.tokens_out == 0


@pytest.mark.asyncio
async def test_missing_recording_returns_media_evicted(monkeypatch):
    obs_id = uuid.uuid4()
    cam_id = uuid.uuid4()
    observation = SimpleNamespace(
        id=obs_id, camera_id=cam_id,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        ended_at=datetime.now(timezone.utc),
        clip_path=None,
    )
    camera = SimpleNamespace(id=cam_id, vlm_provider_id=None)
    stub = _StubVLM({})
    # frames=[] simulates a successful extraction call that returned
    # zero frames (file vanished).
    _patch_pipeline(
        monkeypatch, stub, frames=[], observation=observation,
        recording=None, camera=camera,
    )
    ctx = SimpleNamespace(run_id=uuid.uuid4(), db=None, user=None, tool_call_id=None)
    res = await A.analyze_frame_target(ctx, obs_id, "anything")
    assert res.error == "media_evicted"
    assert stub.call_count == 0
