"""Cross-service identity map: bind ingestion track_ids to person_id via Redis.

This closes the careful gap from the build trace. Ingestion's HARRunner produces track_ids on
the dense stream; perception resolves person_id from faces on keyframes. They are reconciled
here:

- Perception, on each keyframe, receives ingestion's current track boxes (carried on the
  versioned keyframe payload), binds recognised faces to those boxes with the tested
  ``identity_binding.bind_faces_to_tracks`` logic, and writes ``(camera, ingestion_track_id)
  -> {person_id, person_name}`` into Redis with a TTL. It also refreshes the TTL of any present
  track that already has a binding, so identity holds through face occlusion.
- Ingestion (the HAR hook) reads this map to attribute its action segments to the right
  person, and attaches no identity when there is no binding. Never a guessed person.

The Redis TTL is the hold mechanism; the binding *decision* is the unit-tested pure function
shared with perception's keyframe binder. The store/lookup here is tested against a fake Redis.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("nurby.perception.har_idmap")

DEFAULT_TTL_SECONDS = 90


def _key(camera_id, track_id) -> str:
    return f"har:idmap:{camera_id}:{int(track_id)}"


async def bind_and_store(redis, camera_id, ingestion_tracks, faces, ttl: int = DEFAULT_TTL_SECONDS) -> dict[int, dict]:
    """Bind recognised faces to ingestion track boxes and persist to Redis. Refresh the TTL of
    already-bound present tracks so identity survives a frame with no visible face. Returns the
    fresh face->track bindings made this call (for tests/telemetry)."""
    from services.perception.identity_binding import bind_faces_to_tracks

    cam = str(camera_id)
    fresh = bind_faces_to_tracks(ingestion_tracks, faces)
    try:
        for tid, info in fresh.items():
            payload = json.dumps({
                "person_id": info.get("person_id"),
                "person_name": info.get("person_name"),
            })
            await redis.set(_key(cam, tid), payload, ex=ttl)
        # Hold through occlusion: refresh TTL for present tracks that already have a binding
        # but did not get a fresh face this keyframe.
        for t in ingestion_tracks or []:
            tid = t.get("tracker_id")
            if tid is None or int(tid) in fresh:
                continue
            k = _key(cam, tid)
            if await redis.exists(k):
                await redis.expire(k, ttl)
    except Exception:
        logger.debug("har idmap store failed", exc_info=True)
    return fresh


async def lookup(redis, camera_id, track_id) -> dict | None:
    try:
        raw = await redis.get(_key(camera_id, track_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, AttributeError):
        return None


async def lookup_many(redis, camera_id, track_ids) -> dict[int, dict]:
    """Batch lookup for the runner's current tracks. Returns {track_id: {person_id,
    person_name}} only for tracks that have a binding."""
    ids = [int(t) for t in (track_ids or [])]
    if not ids:
        return {}
    out: dict[int, dict] = {}
    try:
        keys = [_key(camera_id, t) for t in ids]
        vals = await redis.mget(keys)
    except Exception:
        return {}
    for tid, v in zip(ids, vals or []):
        if not v:
            continue
        try:
            out[tid] = json.loads(v.decode() if isinstance(v, (bytes, bytearray)) else v)
        except (ValueError, AttributeError):
            continue
    return out
