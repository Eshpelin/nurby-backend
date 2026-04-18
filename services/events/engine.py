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

from shared.database import async_session
from shared.models import Event, Rule
from services.events.actions import execute_action
from sqlalchemy import select

logger = logging.getLogger("nurby.events.engine")


class RuleEngine:
    def __init__(self):
        self._rules: list[Rule] = []
        self._last_load = 0.0
        self._cooldowns: dict[uuid.UUID, float] = {}
        self._cache_ttl = 30  # reload rules every 30s

    async def evaluate(self, observation_data: dict):
        """Evaluate an observation against all active rules."""
        await self._maybe_reload_rules()

        for rule in self._rules:
            if not rule.enabled:
                continue

            # Check cooldown
            now = time.monotonic()
            last_fired = self._cooldowns.get(rule.id, 0)
            if now - last_fired < rule.cooldown_seconds:
                continue

            # Match trigger
            if not self._match_trigger(rule.trigger_pattern, observation_data):
                continue

            # Check conditions
            if rule.conditions and not self._check_conditions(rule.conditions, observation_data):
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

            # Execute actions
            for action in self._wrap_actions(rule.actions):
                try:
                    await execute_action(action, observation_data, rule, event_id)
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

    @staticmethod
    def _match_trigger(pattern: dict, data: dict) -> bool:
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

        elif trigger_type == "loitering":
            events = data.get("loitering_events") or []
            if not events:
                return False
            want_zone = pattern.get("zone_name")
            want_label = pattern.get("label")  # e.g. "person"
            for ev in events:
                if want_zone and ev.get("zone_name") != want_zone:
                    continue
                if want_label and ev.get("label") != want_label:
                    continue
                return True
            return False

        elif trigger_type == "line_cross":
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
    def _check_conditions(conditions: dict, data: dict) -> bool:
        """Check if additional conditions are met."""
        # Camera filter (supports single camera_id or camera_ids array)
        cam_ids = conditions.get("camera_ids")
        cam = conditions.get("camera_id")
        if cam_ids and data.get("camera_id") not in cam_ids:
            return False
        if cam and not cam_ids and data.get("camera_id") != cam:
            return False

        # Day of week filter
        allowed_days = conditions.get("days")
        if allowed_days:
            day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
            today = day_map.get(datetime.now().weekday(), "")
            if today not in allowed_days:
                return False

        # Time window
        time_after = conditions.get("time_after")
        time_before = conditions.get("time_before")
        if time_after or time_before:
            now_time = datetime.now().strftime("%H:%M")
            if time_after and time_before and time_after > time_before:
                # Overnight range (e.g. 19:00 to 07:00)
                if now_time < time_after and now_time > time_before:
                    return False
            else:
                if time_after and now_time < time_after:
                    return False
                if time_before and now_time > time_before:
                    return False

        # Confidence filter
        min_conf = conditions.get("min_confidence")
        if min_conf and (data.get("confidence") or 0) < min_conf:
            return False

        return True

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
