"""Tests for the Redis-backed VLM backlog.

A tiny in-memory fake Redis that implements just the verbs the
backlog uses keeps the test free of network + container deps.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest

from services.perception.vlm_backlog import VLMBacklog


class _FakeRedis:
    """Just enough of redis.asyncio.Redis for VLMBacklog."""

    def __init__(self):
        self._lists: dict[str, list[bytes]] = {}
        self._kv: dict[str, bytes] = {}

    # ── string ops ────────────────────────────────────────────────
    async def setex(self, key, _ttl, value):
        self._kv[key] = bytes(value) if not isinstance(value, bytes) else value

    async def get(self, key):
        return self._kv.get(key)

    async def getdel(self, key):
        return self._kv.pop(key, None)

    async def delete(self, *keys):
        for k in keys:
            self._kv.pop(k, None)
        return len(keys)

    # ── list ops ──────────────────────────────────────────────────
    async def lpush(self, key, *values):
        lst = self._lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    async def ltrim(self, key, start, stop):
        lst = self._lists.get(key, [])
        # Python slice excludes stop, Redis includes — adjust.
        self._lists[key] = lst[start : stop + 1] if stop != -1 else lst[start:]

    async def llen(self, key):
        return len(self._lists.get(key, []))

    async def brpop(self, keys, timeout=0):
        # Single-key, non-blocking flavor — enough for our tests.
        if isinstance(keys, (list, tuple)):
            key = keys[0]
        else:
            key = keys
        lst = self._lists.get(key)
        if lst:
            v = lst.pop()
            return (key, v)
        # Honor the timeout by sleeping 0 — tests should preload data.
        await asyncio.sleep(0)
        return None

    # ── pipeline ──────────────────────────────────────────────────
    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, r: _FakeRedis):
        self._r = r
        self._ops: list[tuple] = []

    def lpush(self, key, *values):
        self._ops.append(("lpush", key, values))
        return self

    def ltrim(self, key, start, stop):
        self._ops.append(("ltrim", key, start, stop))
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            verb = op[0]
            if verb == "lpush":
                results.append(await self._r.lpush(op[1], *op[2]))
            elif verb == "ltrim":
                results.append(await self._r.ltrim(op[1], op[2], op[3]))
        return results


def _frame():
    return np.zeros((10, 10, 3), dtype=np.uint8)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _ev_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


@pytest.fixture
def loop():
    return _ev_loop()


def test_enqueue_pop_roundtrip(loop):
    r = _FakeRedis()
    bl = VLMBacklog(r, capacity=50)
    cam_id = "cam-1"
    obs_id = uuid.uuid4()
    prov_id = uuid.uuid4()

    async def go():
        await bl.enqueue(
            camera_id=cam_id,
            observation_id=obs_id,
            frame=_frame(),
            detections=[{"label": "person"}],
            provider_id=prov_id,
            system_prompt="you are helpful",
            max_tokens=200,
            timestamp=datetime.now(timezone.utc),
        )
        assert await bl.size(cam_id) == 1
        env = await bl.pop(cam_id, timeout_seconds=1)
        assert env is not None and not env.stale
        assert env.observation_id == obs_id
        assert env.provider_id == prov_id
        assert env.detections == [{"label": "person"}]
        assert env.system_prompt == "you are helpful"
        assert env.max_tokens == 200
        assert env.frame is not None
        assert env.frame.shape[2] == 3
        # After pop the backlog is empty AND the frame blob is gone.
        assert await bl.size(cam_id) == 0
        assert await r.get(bl.frame_key(env.job_id)) is None

    loop.run_until_complete(go())


def test_capacity_trims_oldest(loop):
    r = _FakeRedis()
    bl = VLMBacklog(r, capacity=3)
    cam_id = "cam-cap"

    async def go():
        for i in range(5):
            await bl.enqueue(
                camera_id=cam_id,
                observation_id=uuid.uuid4(),
                frame=_frame(),
                detections=[{"i": i}],
                provider_id=uuid.uuid4(),
            )
        # capacity=3 means at most 3 entries remain.
        assert await bl.size(cam_id) == 3
        # Oldest two were dropped; the next pop returns one of the
        # last three enqueued. Specifically the OLDEST of the three.
        env = await bl.pop(cam_id, timeout_seconds=1)
        assert env is not None
        # Oldest surviving is index 2 (we pushed 0..4, trimmed to keep
        # the newest 3 = 2,3,4; oldest-first pop returns 2).
        assert env.detections == [{"i": 2}]

    loop.run_until_complete(go())


def test_stale_envelope_when_frame_blob_expired(loop):
    r = _FakeRedis()
    bl = VLMBacklog(r, capacity=10)
    cam_id = "cam-stale"

    async def go():
        await bl.enqueue(
            camera_id=cam_id,
            observation_id=uuid.uuid4(),
            frame=_frame(),
            detections=[],
            provider_id=uuid.uuid4(),
        )
        # Simulate frame TTL expiry by deleting the blob ourselves.
        list_key = bl.list_key(cam_id)
        payload = r._lists[list_key][0]
        import json
        meta = json.loads(payload)
        await r.delete(bl.frame_key(meta["job_id"]))

        env = await bl.pop(cam_id, timeout_seconds=1)
        assert env is not None
        assert env.stale is True
        assert env.frame is None

    loop.run_until_complete(go())


def test_pop_returns_none_when_empty(loop):
    r = _FakeRedis()
    bl = VLMBacklog(r, capacity=10)
    out = loop.run_until_complete(bl.pop("nobody", timeout_seconds=1))
    assert out is None
