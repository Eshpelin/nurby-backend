"""pHash dedupe tests."""

import asyncio

import numpy as np

from services.perception.vlm_dedupe import (
    DEFAULT_HASH_THRESHOLD,
    hamming_distance,
    phash,
    should_enqueue,
)


def _grad_frame(seed=0):
    """Reproducible non-trivial frame; pure random noise so DCT
    energy spreads across the low-freq block enough for the hash to
    differentiate."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)


def test_phash_identical_distance_zero():
    f = _grad_frame(seed=1)
    a = phash(f)
    b = phash(f.copy())
    assert hamming_distance(a, b) == 0


def test_phash_different_scenes_far_apart():
    a = phash(_grad_frame(seed=1))
    b = phash(_grad_frame(seed=99))
    assert hamming_distance(a, b) > DEFAULT_HASH_THRESHOLD


class _FakeRedis:
    def __init__(self):
        self._kv = {}

    async def get(self, key):
        return self._kv.get(key)

    async def setex(self, key, _ttl, value):
        self._kv[key] = value


def _run(c):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(c)


def test_should_enqueue_first_frame_allows_and_stores():
    r = _FakeRedis()
    frame = _grad_frame(seed=2)
    allow, h, prior = _run(should_enqueue(r, "cam-1", frame))
    assert allow is True
    assert prior is None
    # Stored under expected key.
    stored = _run(r.get("nurby:vlm_last_phash:cam-1"))
    assert stored is not None
    assert int(stored) == h


def test_should_enqueue_skips_duplicate():
    r = _FakeRedis()
    frame = _grad_frame(seed=3)
    _run(should_enqueue(r, "cam-2", frame))
    allow, _h, prior = _run(should_enqueue(r, "cam-2", frame.copy()))
    assert allow is False
    assert prior is not None


def test_should_enqueue_allows_when_scene_changes():
    r = _FakeRedis()
    _run(should_enqueue(r, "cam-3", _grad_frame(seed=4)))
    allow, _h, _p = _run(should_enqueue(r, "cam-3", _grad_frame(seed=77)))
    assert allow is True


def test_should_enqueue_allows_on_redis_failure():
    """Redis outage must never starve the VLM pipeline."""

    class _BrokenRedis:
        async def get(self, _k):
            raise RuntimeError("redis down")

        async def setex(self, _k, _t, _v):
            raise RuntimeError("redis down")

    allow, _h, prior = _run(should_enqueue(_BrokenRedis(), "cam-4", _grad_frame(seed=5)))
    assert allow is True
    assert prior is None
