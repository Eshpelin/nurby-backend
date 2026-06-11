"""
Rule evaluation engine.

Evaluates observations against configured rules. Each rule has a
trigger pattern, optional conditions, and actions to execute.

Trigger patterns match on observation content.
    {"type": "object_detected", "label": "person"}
    {"type": "face_detected"}
    {"type": "face_recognized", "person_id": "uuid"}
    {"type": "motion", "min_score": 0.05}

Conditions add constraints.
    {"camera_id": "uuid"}
    {"time_after": "08:00", "time_before": "18:00"}
    {"min_confidence": 0.5}

Actions define what happens.
    {"type": "webhook", "url": "https://..."}
    {"type": "notify", "message": "..."}
    {"type": "broadcast"}
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None  # type: ignore

from sqlalchemy import select

from services.events.actions import execute_action
from services.perception.spatial_events import (
    _cross_direction,
    _point_in_polygon,
    _segments_cross,
)
from shared.database import async_session
from shared.models import Event, Recording, Rule

# Redis pubsub channel that backend routes publish to whenever a rule
# is created, updated, or deleted. The perception process listens and
# zeros out _last_load so the next evaluate() refreshes immediately.
RULES_INVALIDATE_CHANNEL = "nurby:rules:invalidate"

def _centroid(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

logger = logging.getLogger("nurby.events.engine")


class RuleEngine:
    # Redis key scheme for cross-process cooldown state.
    COOLDOWN_KEY_PREFIX = "nurby:rule_cooldown:"
    # TTL buffer added on top of cooldown_seconds when writing the key.
    COOLDOWN_TTL_BUFFER = 60
    # In-process microcache window. Within this many seconds of a Redis
    # read, the engine trusts its local copy and skips a roundtrip. Keeps
    # the cooldown semantics tight for back-to-back evaluations from the
    # same worker without spamming Redis.
    COOLDOWN_MICROCACHE_SECONDS = 1.0

    def __init__(self):
        self._rules: list[Rule] = []
        self._last_load = 0.0
        # Microcache only. Authoritative cooldown state lives in Redis so
        # it survives perception restarts and is shared across workers.
        # Maps rule_id -> (cached_at_epoch, last_fired_epoch). Entries are
        # only honored for COOLDOWN_MICROCACHE_SECONDS, after which the
        # engine re-reads Redis. Note. epoch (time.time) not monotonic;
        # monotonic is process-local and cannot be persisted.
        self._cooldowns: dict[uuid.UUID, tuple[float, float]] = {}
        self._cache_ttl = 30  # reload rules every 30s
        # Per-(rule_id, track_id) entry timestamp for inline-geometry loiter rules.
        self._loiter_entry: dict[tuple[uuid.UUID, int], float] = {}
        # Multi-frame persistence state for min_frames triggers. Maps
        # (rule_id, track_key) -> (first_seen_epoch, hit_count). A rule with
        # min_frames=3 only fires once the same tracked object has matched
        # on 3 keyframes inside its window, so a single hot frame (leaf
        # gusting past, headlight flare) cannot fire an alert.
        self._persistence: dict[tuple, tuple[float, int]] = {}
        # Pubsub invalidator. set when start_invalidation_listener is called.
        self._invalidate_stop: asyncio.Event | None = None
        self._invalidate_task: asyncio.Task | None = None
        # Redis client for cooldown state. Lazy-initialized on first use.
        self._cooldown_redis = None
        self._cooldown_redis_failed = False
        # Cached value of the rules_cooldown_backend app setting. Refreshed
        # on the same cadence as the rules list.
        self._cooldown_backend = "redis"
        self._cooldown_backend_loaded_at = 0.0

    async def evaluate(self, observation_data: dict):
        """Evaluate an observation against all active rules."""
        await self._maybe_reload_rules()
        await self._maybe_reload_cooldown_backend()

        # Resolve the household timezone once per tick. Day-of-week
        # and time-window checks pin against this zone so a 22:00 to
        # 06:00 schedule means LA-local, not perception-host-local.
        tz = await self._resolve_timezone()

        for rule in self._rules:
            if not rule.enabled:
                continue

            # Check cooldown. cooldown_seconds=0 means no cooldown and
            # skips all Redis traffic so chatty triggers (motion, etc.)
            # do not generate per-keyframe roundtrips.
            now = time.time()
            if rule.cooldown_seconds and rule.cooldown_seconds > 0:
                last_fired = await self._read_cooldown(rule.id)
                if last_fired and (now - last_fired) < rule.cooldown_seconds:
                    continue

            # Match trigger
            if not self._match_trigger(rule.trigger_pattern, observation_data, rule.id):
                continue

            # Check conditions
            if rule.conditions and not self._check_conditions(rule.conditions, observation_data, tz):
                continue

            # Rule matched. Fire actions.
            logger.info("Rule '%s' triggered by observation", rule.name)
            if rule.cooldown_seconds and rule.cooldown_seconds > 0:
                await self._write_cooldown(rule.id, now, rule.cooldown_seconds)

            # Resolve the footage clip covering this observation so the
            # event and any webhook payload carry a direct link to it.
            await self._attach_footage(observation_data)

            # Store event
            event_id = await self._store_event(
                rule_id=rule.id,
                observation_id=observation_data.get("observation_id"),
                payload=observation_data,
            )

            # Execute actions. Thread a shared `vars` dict so later actions
            # can reference outputs written by earlier ones.
            observation_data.setdefault("vars", {})
            for action in self._wrap_actions(rule.actions):
                try:
                    await execute_action(action, observation_data, rule, event_id)
                except RuntimeError as exc:
                    # Chain-abort signal. A vlm_call with on_error=stop or a
                    # verify action that failed confirmation raises here to
                    # suppress every remaining action (notify, telegram, ...).
                    logger.info("Rule '%s' chain stopped. %s", rule.name, exc)
                    break
                except Exception:
                    logger.exception("Action failed for rule '%s'", rule.name)

            # Fan the fired event out to standing webhook subscriptions.
            try:
                from services.events.actions import dispatch_subscriptions

                await dispatch_subscriptions(observation_data, rule, event_id)
            except Exception:
                logger.exception("subscription dispatch failed for rule '%s'", rule.name)

    async def _maybe_reload_rules(self):
        now = time.monotonic()
        if now - self._last_load < self._cache_ttl:
            return
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(Rule).where(Rule.enabled == True)
                )
                self._rules = list(result.scalars().all())
                # Detach from session
                for r in self._rules:
                    db.expunge(r)
            self._last_load = now
            logger.debug("Loaded %d active rules", len(self._rules))
        except Exception:
            logger.exception("Failed to load rules")

    async def _maybe_reload_cooldown_backend(self) -> None:
        """Refresh the rules_cooldown_backend app setting on the same
        30s cadence as the rules list. Lets operators flip to in-memory
        cooldown without a redeploy.
        """
        now = time.monotonic()
        if now - self._cooldown_backend_loaded_at < self._cache_ttl:
            return
        try:
            from shared.app_settings import get_setting
            val = await get_setting("rules_cooldown_backend", "redis")
            if val not in ("redis", "memory"):
                val = "redis"
            self._cooldown_backend = val
        except Exception:
            self._cooldown_backend = "redis"
        self._cooldown_backend_loaded_at = now

    async def _get_cooldown_redis(self):
        """Lazy-init a Redis client for cooldown state. Returns None if
        Redis is unreachable or the operator has set rules_cooldown_backend
        to "memory".
        """
        if self._cooldown_backend == "memory":
            return None
        if self._cooldown_redis_failed:
            return None
        if self._cooldown_redis is not None:
            return self._cooldown_redis
        try:
            import redis.asyncio as aioredis

            from shared.config import settings
            self._cooldown_redis = aioredis.from_url(
                settings.redis_url, decode_responses=True
            )
        except Exception:
            logger.exception("cooldown redis init failed; using in-process microcache only")
            self._cooldown_redis_failed = True
            self._cooldown_redis = None
        return self._cooldown_redis

    async def _read_cooldown(self, rule_id: uuid.UUID) -> float | None:
        """Return the last-fired epoch for rule_id, or None if no
        cooldown is recorded. Hits the in-process microcache first, then
        Redis. Redis errors degrade silently to microcache-only.
        """
        now = time.time()
        cached = self._cooldowns.get(rule_id)
        if cached is not None:
            cached_at, last_fired = cached
            if now - cached_at < self.COOLDOWN_MICROCACHE_SECONDS:
                return last_fired
        client = await self._get_cooldown_redis()
        if client is None:
            # Degraded mode. fall back to whatever the microcache has.
            return cached[1] if cached else None
        try:
            raw = await client.get(self.COOLDOWN_KEY_PREFIX + str(rule_id))
        except Exception:
            logger.warning("cooldown redis GET failed for rule %s; using microcache", rule_id)
            return cached[1] if cached else None
        if raw is None:
            # Key expired in Redis. clear stale microcache entry so the
            # rule can fire again.
            self._cooldowns.pop(rule_id, None)
            return None
        try:
            last_fired = float(raw)
        except (TypeError, ValueError):
            return None
        self._cooldowns[rule_id] = (now, last_fired)
        return last_fired

    async def _write_cooldown(
        self, rule_id: uuid.UUID, fired_at: float, cooldown_seconds: int
    ) -> None:
        """Persist last-fired epoch to Redis with TTL =
        cooldown_seconds + buffer. Always updates the microcache, even
        when Redis is unreachable, so the same process at least sees its
        own recent fires.
        """
        self._cooldowns[rule_id] = (fired_at, fired_at)
        client = await self._get_cooldown_redis()
        if client is None:
            return
        try:
            await client.set(
                self.COOLDOWN_KEY_PREFIX + str(rule_id),
                str(fired_at),
                ex=int(cooldown_seconds) + self.COOLDOWN_TTL_BUFFER,
            )
        except Exception:
            logger.warning("cooldown redis SET failed for rule %s; degraded mode", rule_id)

    def _match_trigger(self, pattern: dict, data: dict, rule_id: uuid.UUID | None = None) -> bool:
        """Check if observation data matches trigger pattern."""
        trigger_type = pattern.get("type")

        # Camera availability events are synthetic, observation-less
        # payloads published by ingestion when a camera goes dark or
        # recovers. They only ever match their own trigger types, and
        # those types never match real observations, so the catch-all
        # "any" stays scoped to actual footage.
        if data.get("event_kind") == "camera_status":
            if trigger_type not in ("camera_offline", "camera_online"):
                return False
            want = "offline" if trigger_type == "camera_offline" else "online"
            if data.get("camera_status") != want:
                return False
            cam_filter = pattern.get("camera_id")
            if cam_filter and str(cam_filter) != str(data.get("camera_id")):
                return False
            return True
        if trigger_type in ("camera_offline", "camera_online"):
            return False

        if trigger_type == "object_detected":
            label = pattern.get("label")
            detections = data.get("object_detections", {}).get("objects", [])
            if not detections:
                return False
            candidates = [d for d in detections if not label or d.get("label") == label]
            candidates = self._filter_detection_geometry(candidates, pattern, data)
            if not candidates:
                return False
            # Surface the strongest matched detection's confidence so the
            # min_confidence condition can gate on it (the observation-level
            # VLM confidence is always None at live eval time).
            best = max(candidates, key=lambda d: d.get("confidence") or 0)
            data["_matched_confidence"] = best.get("confidence")
            min_frames = int(pattern.get("min_frames") or 1)
            if min_frames <= 1:
                return True
            return self._check_persistence(
                rule_id, pattern, candidates, min_frames,
                within_seconds=float(pattern.get("within_seconds") or 30),
            )

        elif trigger_type == "vehicle_detected":
            # Vehicle identity trigger. fires on a specific plate, any
            # plate-identified vehicle, or any vehicle at all. Plate match is
            # case-insensitive substring so "ABC" matches "ABC123".
            vd = data.get("vehicle_detections") or {}
            vehicles = vd.get("vehicles", []) if isinstance(vd, dict) else []
            if not vehicles:
                return False
            want_plate = (pattern.get("plate") or "").strip().upper()
            if want_plate:
                return any(want_plate in (v.get("plate_text") or "").upper() for v in vehicles)
            if pattern.get("identified_only"):
                return any(v.get("vehicle_id") for v in vehicles)
            return True

        elif trigger_type == "face_detected":
            faces = data.get("person_detections", {})
            return faces is not None and faces.get("count", 0) > 0

        elif trigger_type == "face_recognized":
            target_person = pattern.get("person_id")
            faces = data.get("person_detections", {})
            if not faces or faces.get("count", 0) == 0:
                return False
            for face in faces.get("faces", []):
                if face.get("person_id"):
                    if target_person is None or face["person_id"] == target_person:
                        return True
            return False

        elif trigger_type == "face_unknown":
            faces = data.get("person_detections", {})
            if not faces or faces.get("count", 0) == 0:
                return False
            return any(f.get("person_id") is None for f in faces.get("faces", []))

        elif trigger_type == "motion":
            min_score = pattern.get("min_score", 0.01)
            return data.get("motion_score", 0) >= min_score

        elif trigger_type == "audio_event":
            ev = data.get("audio_event") or {}
            if not ev:
                return False
            want_label = pattern.get("label")  # baby_cry, scream, speech, etc
            min_score = pattern.get("min_score", 0.3)
            if want_label and ev.get("label") != want_label:
                return False
            return float(ev.get("score", 0)) >= float(min_score)

        elif trigger_type == "clap_pattern":
            # Fires when the rolling clap counter on this camera hits
            # the configured count within the configured window. The
            # audio worker tags rule_data with clap_pattern when a
            # clap event closes a window. count=2 = double clap,
            # count=3 = triple, etc. Optional camera filter.
            cp = data.get("clap_pattern") or {}
            if not cp:
                return False
            want_count = int(pattern.get("count", 2))
            if int(cp.get("count", 0)) != want_count:
                return False
            pcam = pattern.get("camera_id")
            if pcam and pcam != data.get("camera_id"):
                return False
            return True

        elif trigger_type == "speech_phrase":
            # Matches transcript text against a phrase list. Fires when
            # any phrase appears (case-insensitive substring). Set
            # match="all" to require every phrase. Optional camera
            # filter so a 'kitchen lights on' phrase only fires from
            # the kitchen camera.
            tx = data.get("transcript") or {}
            if not tx:
                return False
            text = (tx.get("text") or "").strip().lower()
            if not text:
                return False
            phrases = pattern.get("phrases") or []
            if not phrases:
                return False
            pcam = pattern.get("camera_id")
            if pcam and pcam != data.get("camera_id"):
                return False
            match_mode = (pattern.get("match") or "any").lower()
            phrases_lc = [str(p).strip().lower() for p in phrases if p]
            if not phrases_lc:
                return False
            if match_mode == "all":
                return all(p in text for p in phrases_lc)
            return any(p in text for p in phrases_lc)

        elif trigger_type == "loitering":
            # Inline geometry mode. trigger carries its own polygon.
            pts = pattern.get("points")
            if pts and len(pts) >= 3:
                pcam = pattern.get("camera_id")
                if pcam and pcam != data.get("camera_id"):
                    return False
                threshold = float(pattern.get("threshold_seconds", 30))
                want_label = pattern.get("label")
                tracks = data.get("tracks") or []
                now = time.monotonic()
                fired = False
                for tr in tracks:
                    if want_label and tr.get("label") != want_label:
                        continue
                    tid = tr.get("track_id")
                    if tid is None:
                        continue
                    inside = _point_in_polygon(_centroid(tr["bbox"]), pts)
                    key = (rule_id, tid)
                    entry = self._loiter_entry.get(key)
                    if inside:
                        if entry is None:
                            self._loiter_entry[key] = now
                        elif now - entry >= threshold:
                            fired = True
                            self._loiter_entry[key] = now  # re-arm
                    else:
                        self._loiter_entry.pop(key, None)
                return fired

            # Legacy zone_name mode. relies on pipeline-precomputed events.
            events = data.get("loitering_events") or []
            if not events:
                return False
            want_zone = pattern.get("zone_name")
            want_label = pattern.get("label")
            for ev in events:
                if want_zone and ev.get("zone_name") != want_zone:
                    continue
                if want_label and ev.get("label") != want_label:
                    continue
                return True
            return False

        elif trigger_type == "line_cross":
            # Inline geometry mode. trigger carries the line segment.
            pts = pattern.get("points")
            if pts and len(pts) == 2:
                pcam = pattern.get("camera_id")
                if pcam and pcam != data.get("camera_id"):
                    return False
                want_dir = pattern.get("direction", "any")
                want_label = pattern.get("label")
                a, b = pts[0], pts[1]
                tracks = data.get("tracks") or []
                for tr in tracks:
                    if want_label and tr.get("label") != want_label:
                        continue
                    prev = tr.get("prev_bbox")
                    if not prev:
                        continue
                    prev_c = _centroid(prev)
                    cur_c = _centroid(tr["bbox"])
                    if not _segments_cross(prev_c, cur_c, a, b):
                        continue
                    direction = _cross_direction(prev_c, cur_c, a, b)
                    if want_dir != "any" and direction != want_dir:
                        continue
                    return True
                return False

            # Legacy zone_name mode.
            events = data.get("line_cross_events") or []
            if not events:
                return False
            want_zone = pattern.get("zone_name")
            want_dir = pattern.get("direction", "any")
            want_label = pattern.get("label")
            for ev in events:
                if want_zone and ev.get("zone_name") != want_zone:
                    continue
                if want_label and ev.get("label") != want_label:
                    continue
                if want_dir != "any" and ev.get("direction") != want_dir:
                    continue
                return True
            return False

        elif trigger_type == "any":
            return True

        return False

    @staticmethod
    def _filter_detection_geometry(detections: list, pattern: dict, data: dict) -> list:
        """Drop detections outside the trigger's size/ratio bounds.

        ``min_area_pct``/``max_area_pct`` are fractions of the frame (0..1);
        ``min_ratio``/``max_ratio`` bound width:height. Kills the classic
        false-positive classes: a "person" the size of the whole frame
        (headlight flare) or a 40:1 "dog" (fence shadow). Detections with
        no usable bbox pass through; the filter never blocks on missing
        geometry, only on bad geometry.
        """
        min_a = pattern.get("min_area_pct")
        max_a = pattern.get("max_area_pct")
        min_r = pattern.get("min_ratio")
        max_r = pattern.get("max_ratio")
        if min_a is None and max_a is None and min_r is None and max_r is None:
            return detections
        fw = data.get("frame_width")
        fh = data.get("frame_height")
        out = []
        for d in detections:
            bbox = d.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                out.append(d)
                continue
            try:
                x1, y1, x2, y2 = (float(v) for v in bbox)
            except (TypeError, ValueError):
                out.append(d)
                continue
            w, h = max(0.0, x2 - x1), max(0.0, y2 - y1)
            if h > 0:
                ratio = w / h
                if min_r is not None and ratio < float(min_r):
                    continue
                if max_r is not None and ratio > float(max_r):
                    continue
            if fw and fh and (min_a is not None or max_a is not None):
                area_pct = (w * h) / (float(fw) * float(fh))
                if min_a is not None and area_pct < float(min_a):
                    continue
                if max_a is not None and area_pct > float(max_a):
                    continue
            out.append(d)
        return out

    def _check_persistence(
        self,
        rule_id,
        pattern: dict,
        candidates: list,
        min_frames: int,
        *,
        within_seconds: float,
    ) -> bool:
        """True once any matched object has persisted for ``min_frames``
        keyframes inside ``within_seconds``. Keyed on tracker_id when the
        tracker stamped one, else on the label as a coarse fallback."""
        now = time.time()
        # Opportunistic GC so a busy scene cannot grow the dict unbounded.
        if len(self._persistence) > 4096:
            cutoff = now - max(within_seconds * 2, 120)
            self._persistence = {
                k: v for k, v in self._persistence.items() if v[0] >= cutoff
            }
        fired = False
        for d in candidates:
            track_key = d.get("tracker_id")
            if track_key is None:
                track_key = f"label:{d.get('label')}"
            key = (rule_id, track_key)
            first, count = self._persistence.get(key, (now, 0))
            if now - first > within_seconds:
                # Window expired. start a fresh streak from this frame.
                first, count = now, 0
            count += 1
            self._persistence[key] = (first, count)
            if count >= min_frames:
                fired = True
        return fired

    @staticmethod
    async def _resolve_timezone():
        """Read system_timezone from app settings. Falls back to UTC."""
        try:
            from shared.app_settings import get_setting
            name = await get_setting("system_timezone")
        except Exception:
            name = None
        if name and ZoneInfo is not None:
            try:
                return ZoneInfo(name)
            except Exception:
                logger.warning("Invalid system_timezone %r. falling back to UTC", name)
        return timezone.utc

    @staticmethod
    def _check_conditions(conditions: dict, data: dict, tz=None) -> bool:
        """Check if additional conditions are met.

        ``tz`` is the household timezone resolved once per evaluate()
        tick. Pass None for legacy callers; UTC is used in that case.

        Time window semantics. ``time_after <= now <= time_before``.
        Boundaries are inclusive. Overnight ranges (``time_after >
        time_before``) wrap midnight; the check becomes
        ``now >= time_after OR now <= time_before``.
        """
        if tz is None:
            tz = timezone.utc

        # Camera filter (supports single camera_id or camera_ids array)
        cam_ids = conditions.get("camera_ids")
        cam = conditions.get("camera_id")
        if cam_ids and data.get("camera_id") not in cam_ids:
            return False
        if cam and not cam_ids and data.get("camera_id") != cam:
            return False

        # Day of week filter (timezone-aware).
        allowed_days = conditions.get("days")
        if allowed_days:
            day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
            today = day_map.get(datetime.now(tz).weekday(), "")
            if today not in allowed_days:
                return False

        # Time window (timezone-aware).
        time_after = conditions.get("time_after")
        time_before = conditions.get("time_before")
        if time_after or time_before:
            now_time = datetime.now(tz).strftime("%H:%M")
            if time_after and time_before and time_after > time_before:
                # Overnight range (e.g. 22:00 to 06:00). Pass if we
                # are after the start OR before the end.
                if now_time < time_after and now_time > time_before:
                    return False
            else:
                if time_after and now_time < time_after:
                    return False
                if time_before and now_time > time_before:
                    return False

        # Confidence filter. ``is not None`` so a configured 0.0
        # threshold (no minimum) is treated the same as falsy 0 today
        # while a None means "no filter configured".
        min_conf = conditions.get("min_confidence")
        if min_conf is not None:
            # Live evaluation has no observation-level VLM confidence yet
            # (the pipeline sends confidence=None), so fall back to the
            # confidence of the detection the trigger actually matched,
            # then to the strongest object in frame. Before this fallback
            # every rule with min_confidence silently never fired live.
            conf = data.get("confidence")
            if conf is None:
                conf = data.get("_matched_confidence")
            if conf is None:
                objs = (data.get("object_detections") or {}).get("objects") or []
                confs = [o.get("confidence") for o in objs if o.get("confidence") is not None]
                conf = max(confs) if confs else None
            # No confidence signal at all (audio, status events): the
            # condition cannot be evaluated, so it does not block.
            if conf is not None and conf < min_conf:
                return False

        return True

    # ── Pubsub invalidation ────────────────────────────────────────

    async def start_invalidation_listener(self) -> None:
        """Subscribe to ``nurby:rules:invalidate`` on Redis and zero
        ``_last_load`` whenever a message arrives. Idempotent. callers
        may invoke this once during perception startup. The task self-
        cancels via ``stop_invalidation_listener``.
        """
        if self._invalidate_task is not None and not self._invalidate_task.done():
            return
        self._invalidate_stop = asyncio.Event()
        self._invalidate_task = asyncio.create_task(self._invalidation_loop())

    async def stop_invalidation_listener(self) -> None:
        if self._invalidate_stop is not None:
            self._invalidate_stop.set()
        task = self._invalidate_task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._invalidate_task = None
        self._invalidate_stop = None

    async def _invalidation_loop(self) -> None:
        try:
            import redis.asyncio as aioredis

            from shared.config import settings
        except Exception:
            logger.warning("redis unavailable; rule reload falls back to %ss timer", self._cache_ttl)
            return

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(RULES_INVALIDATE_CHANNEL)
            logger.info("Listening for rule invalidations on %s", RULES_INVALIDATE_CHANNEL)
            while self._invalidate_stop is not None and not self._invalidate_stop.is_set():
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=1.5,
                    )
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    logger.exception("invalidation pubsub error")
                    await asyncio.sleep(1.0)
                    continue
                if not msg:
                    continue
                logger.info("rules invalidated by %s", msg.get("data"))
                self._last_load = 0.0
        finally:
            try:
                await pubsub.unsubscribe(RULES_INVALIDATE_CHANNEL)
                await pubsub.close()
                await client.aclose()
            except Exception:
                pass

    @staticmethod
    def _wrap_actions(actions) -> list[dict]:
        """Ensure actions is always a list."""
        if isinstance(actions, list):
            return actions
        if isinstance(actions, dict):
            return [actions]
        return []

    @staticmethod
    async def _attach_footage(data: dict) -> None:
        """Enrich observation_data with recording_id + recording_url for
        the clip that covers this observation on the same camera.

        Resolved once per observation. Matches the most recent Recording
        whose window contains the observation timestamp. Best-effort. a
        missing clip just leaves the fields empty.
        """
        if "recording_id" in data:
            return
        data["recording_id"] = None
        data["recording_url"] = ""
        cam_raw = data.get("camera_id")
        ts_raw = data.get("timestamp")
        if not cam_raw or not ts_raw:
            return
        try:
            cam_id = uuid.UUID(str(cam_raw))
        except (ValueError, TypeError):
            return
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                return
        try:
            async with async_session() as db:
                rec = (
                    await db.execute(
                        select(Recording)
                        .where(Recording.camera_id == cam_id)
                        .where(Recording.started_at <= ts)
                        .where(
                            (Recording.ended_at.is_(None))
                            | (Recording.ended_at >= ts)
                        )
                        .order_by(Recording.started_at.desc())
                        .limit(1)
                    )
                ).scalars().first()
        except Exception:
            logger.exception("footage lookup failed")
            return
        if rec is None:
            return
        from shared.config import settings as _settings

        base = (_settings.public_base_url or "").rstrip("/")
        data["recording_id"] = str(rec.id)
        data["recording_url"] = f"{base}/api/recordings/{rec.id}/stream"

    @staticmethod
    async def _store_event(
        rule_id: uuid.UUID,
        observation_id: str | None,
        payload: dict,
    ) -> uuid.UUID:
        """Store fired event in DB."""
        try:
            obs_uuid = uuid.UUID(observation_id) if observation_id else None
        except (ValueError, TypeError):
            obs_uuid = None

        rec_raw = payload.get("recording_id")
        try:
            rec_uuid = uuid.UUID(str(rec_raw)) if rec_raw else None
        except (ValueError, TypeError):
            rec_uuid = None

        event = Event(
            rule_id=rule_id,
            observation_id=obs_uuid,
            recording_id=rec_uuid,
            payload=payload,
        )
        try:
            async with async_session() as db:
                db.add(event)
                await db.commit()
                await db.refresh(event)
                logger.info("Stored event %s for rule %s", event.id, rule_id)
                return event.id
        except Exception:
            logger.exception("Failed to store event")
            return uuid.uuid4()
