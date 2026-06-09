"""Tests for the cross-service identity map (ingestion track_id -> person_id via Redis)."""

import uuid

import pytest

from services.perception import har_idmap

PID = str(uuid.uuid4())
CAM = "cam-1"


class FakeRedis:
    """Minimal async Redis: set(ex), get, exists, expire, mget. Tracks expire calls."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def set(self, k, v, ex=None):
        self.store[k] = v
        if ex is not None:
            self.ttls[k] = ex

    async def get(self, k):
        return self.store.get(k)

    async def exists(self, k):
        return 1 if k in self.store else 0

    async def expire(self, k, ttl):
        self.expire_calls.append((k, ttl))
        if k in self.store:
            self.ttls[k] = ttl
            return 1
        return 0

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]


def _track(tid, bbox):
    return {"tracker_id": tid, "bbox": bbox}


def _face(pid, bbox, name="Mum", dist=0.4):
    return {"person_id": pid, "person_name": name, "bbox": bbox, "match_distance": dist}


@pytest.mark.asyncio
async def test_bind_and_lookup():
    r = FakeRedis()
    tracks = [_track(7, [100, 100, 200, 400])]
    faces = [_face(PID, [130, 120, 170, 180])]
    fresh = await har_idmap.bind_and_store(r, CAM, tracks, faces, ttl=90)
    assert 7 in fresh
    got = await har_idmap.lookup(r, CAM, 7)
    assert got["person_id"] == PID and got["person_name"] == "Mum"


@pytest.mark.asyncio
async def test_unknown_face_not_stored():
    r = FakeRedis()
    await har_idmap.bind_and_store(r, CAM, [_track(7, [100, 100, 200, 400])],
                                   [_face(None, [130, 120, 170, 180])], ttl=90)
    assert await har_idmap.lookup(r, CAM, 7) is None


@pytest.mark.asyncio
async def test_ttl_refreshed_for_present_track_without_face():
    r = FakeRedis()
    # bind once
    await har_idmap.bind_and_store(r, CAM, [_track(7, [100, 100, 200, 400])],
                                   [_face(PID, [130, 120, 170, 180])], ttl=90)
    # next keyframe: track present, NO face -> binding held, TTL refreshed
    await har_idmap.bind_and_store(r, CAM, [_track(7, [105, 100, 205, 400])], [], ttl=90)
    assert (har_idmap._key(CAM, 7), 90) in r.expire_calls
    assert (await har_idmap.lookup(r, CAM, 7))["person_id"] == PID


@pytest.mark.asyncio
async def test_lookup_many_batches_present_bindings():
    r = FakeRedis()
    await har_idmap.bind_and_store(
        r, CAM,
        [_track(1, [100, 100, 200, 400]), _track(2, [400, 100, 500, 400])],
        [_face(PID, [130, 120, 170, 180])],   # only track 1 has a face
        ttl=90,
    )
    out = await har_idmap.lookup_many(r, CAM, [1, 2, 3])
    assert 1 in out and out[1]["person_id"] == PID
    assert 2 not in out and 3 not in out  # unbound tracks absent
