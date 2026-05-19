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

from shared.database import async_session
from shared.models import Event, Rule
from services.events.actions import execute_action
from services.perception.spatial_events import (
    _point_in_polygon,
    _segments_cross,
    _cross_direction,
)
from sqlalchemy import select

# Redis pubsub channel that backend routes publish to whenever a rule
# is created, updated, or deleted. The perception process listens and
# zeros out _last_load so the next evaluate() refreshes immediately.
RULES_INVALIDATE_CHANNEL = "nurby:rules:invalidate"

def _centroid(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

logger = logging.getLogger("nurby.events.engine")


class RuleEngine:
    def __init__(self):
        self._rules: list[Rule] = []
        self._last_load = 0.0
        self._cooldowns: dict[uuid.UUID, float] = {}
        self._cache_ttl = 30  # reload rules every 30s
        # Per-(rule_id, track_id) entry timestamp for inline-geometry loiter rules.
        self._loiter_entry: dict[tuple[uuid.UUID, int], float] = {}
        # Pubsub invalidator. set when start_invalidation_listener is called.
        self._invalidate_stop: asyncio.Event | None = None
        self._invalidate_task: asyncio.Task | None = None

    async def evaluate(self, observation_data: dict):
        """Evaluate an observation against all active rules."""
        await self._maybe_reload_rules()

        # Resolve the household timezone once per tick. Day-of-week
        # and time-window checks pin against this zone so a 22:00 to
        # 06:00 schedule means LA-local, not perception-host-local.
        tz = await self._resolve_timezone()

        for rule in self._rules:
            if not rule.enabled:
                continue

            # Check cooldown
            now = time.monotonic()
            last_fired = self._cooldowns.get(rule.id, 0)
            if now - last_fired < rule.cooldown_seconds:
                continue

            # Match trigger
            if not self._match_trigger(rule.trigger_pattern, observation_data, rule.id):
                continue

            # Check conditions
            if rule.conditions and not self._check_conditions(rule.conditions, observation_data, tz):
                continue

            # Rule matched. Fire actions.
            logger.info("Rule '%s' triggered by observation", rule.name)
            self._cooldowns[rule.id] = now

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
                    # on_error=stop from a vlm_call aborts the chain.
                    logger.info("Rule '%s' chain stopped. %s", rule.name, exc)
                    break
                except Exception:
                    logger.exception("Action failed for rule '%s'", rule.name)

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

    def _match_trigger(self, pattern: dict, data: dict, rule_id: uuid.UUID | None = None) -> bool:
        """Check if observation data matches trigger pattern."""
        trigger_type = pattern.get("type")

        if trigger_type == "object_detected":
            label = pattern.get("label")
            detections = data.get("object_detections", {}).get("objects", [])
            if not detections:
                return False
            if label:
                return any(d["label"] == label for d in detections)
            return len(detections) > 0

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
        if min_conf is not None and (data.get("confidence") or 0) < min_conf:
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

        event = Event(
            rule_id=rule_id,
            observation_id=obs_uuid,
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
