import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user, require_admin
from shared.config import settings
from shared.database import get_db
from shared.models import Observation, Rule, User
from shared.schemas import (
    RuleCreate,
    RuleReplayResponse,
    RuleReplaySample,
    RuleResponse,
    RuleTestActionPreview,
    RuleTestRequest,
    RuleTestResponse,
    RuleUpdate,
)

router = APIRouter()
logger = logging.getLogger("nurby.api.rules")


async def _publish_invalidation(rule_id: uuid.UUID | str) -> None:
    """Best-effort. perception listens on ``nurby:rules:invalidate`` and
    re-loads the rule set on the next evaluate() tick. Failures here
    only mean the perception engine waits up to its 30s passive TTL
    instead of refreshing within ~1s.
    """
    try:
        import redis.asyncio as aioredis

        from services.events.engine import RULES_INVALIDATE_CHANNEL

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.publish(RULES_INVALIDATE_CHANNEL, str(rule_id))
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
    except Exception:
        logger.debug("rule invalidation publish failed", exc_info=True)


@router.get("", response_model=list[RuleResponse])
async def list_rules(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rule).order_by(Rule.created_at))
    return result.scalars().all()


@router.post("", response_model=RuleResponse, status_code=201)
async def create_rule(body: RuleCreate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = Rule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await _publish_invalidation(rule.id)
    return rule


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(rule_id: uuid.UUID, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: uuid.UUID, body: RuleUpdate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    await _publish_invalidation(rule.id)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    await _publish_invalidation(rule_id)


# ── Dry-run helpers (used by /test and /replay) ───────────────────
#
# The synthesis function builds an observation_data dict shaped to
# match the engine's _match_trigger expectations for each trigger
# type. The shapes follow the perception worker's runtime conventions,
# not the Observation SQL columns, so e.g. ``audio_event`` is a single
# dict not a list (the engine reads ``data["audio_event"]`` directly).
# If the perception worker ever changes its observation_data shape,
# update both _synthesize_observation_for_trigger and the engine in
# lockstep.

def _synthesize_observation_for_trigger(
    trigger_pattern: dict,
    camera_id: uuid.UUID | None,
) -> dict:
    """Build a permissive observation dict that should match ``trigger_pattern``.

    Permissive means we err on the side of "yes, the trigger matches"
    so the user gets to verify their conditions / actions even when
    they have not described a fully-fleshed scenario.
    """
    t = trigger_pattern.get("type")
    cam = str(camera_id) if camera_id else "test-camera"
    obs: dict = {
        "observation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "camera_id": cam,
    }

    if t == "object_detected":
        label = trigger_pattern.get("label") or "person"
        obs["object_detections"] = {
            "objects": [{"label": label, "confidence": 0.9}],
        }

    elif t == "face_recognized":
        pid = trigger_pattern.get("person_id") or str(uuid.uuid4())
        obs["person_detections"] = {
            "count": 1,
            "faces": [{"person_id": str(pid), "confidence": 0.9}],
        }

    elif t == "face_unknown":
        obs["person_detections"] = {
            "count": 1,
            "faces": [{"cluster_id": str(uuid.uuid4()), "person_id": None}],
        }

    elif t == "face_detected":
        obs["person_detections"] = {
            "count": 1,
            "faces": [{"confidence": 0.9}],
        }

    elif t == "motion":
        score = float(trigger_pattern.get("min_score", 0.01))
        obs["motion_score"] = score + 0.05

    elif t == "audio_event":
        label = trigger_pattern.get("label") or "baby_cry"
        score = float(trigger_pattern.get("min_score", 0.3)) + 0.1
        # Engine reads data["audio_event"] (singular dict).
        obs["audio_event"] = {"label": label, "score": score}

    elif t == "clap_pattern":
        count = int(trigger_pattern.get("count", 2))
        obs["clap_pattern"] = {"count": count}
        pcam = trigger_pattern.get("camera_id")
        if pcam:
            obs["camera_id"] = pcam

    elif t == "speech_phrase":
        phrases = trigger_pattern.get("phrases") or ["hello"]
        first = next((str(p) for p in phrases if str(p).strip()), "hello")
        obs["transcript"] = {"text": first}
        pcam = trigger_pattern.get("camera_id")
        if pcam:
            obs["camera_id"] = pcam

    elif t == "loitering":
        # Inline geometry mode. Place a track inside the polygon and
        # backdate the entry timestamp so the engine fires immediately.
        # The engine reads from self._loiter_entry. tests using this
        # endpoint just need first-call match semantics, which the
        # legacy "loitering_events" pre-computed list provides without
        # needing two evaluate() calls.
        label = trigger_pattern.get("label") or "person"
        threshold = float(trigger_pattern.get("threshold_seconds", 30))
        pcam = trigger_pattern.get("camera_id") or cam
        obs["camera_id"] = pcam
        obs["loitering_events"] = [{
            "camera_id": str(pcam),
            "label": label,
            "duration_seconds": threshold + 1,
            "rule_id": None,
            "zone_name": trigger_pattern.get("zone_name") or "test-zone",
        }]

    elif t == "line_cross":
        label = trigger_pattern.get("label") or "person"
        direction = trigger_pattern.get("direction") or "in"
        if direction == "any":
            direction = "in"
        pcam = trigger_pattern.get("camera_id") or cam
        obs["camera_id"] = pcam
        obs["line_cross_events"] = [{
            "camera_id": str(pcam),
            "label": label,
            "direction": direction,
            "zone_name": trigger_pattern.get("zone_name") or "test-line",
        }]

    elif t == "any":
        pass

    return obs


def _observation_to_engine_payload(observation: Observation) -> dict:
    """Reconstruct an observation_data dict from a stored Observation row.

    The perception worker emits observation_data as a single dict but
    the Observation table only stores a subset of the runtime fields
    (object_detections, person_detections, vlm_description, etc.).
    Trigger types that depend on transient fields like ``tracks`` or
    ``audio_event`` will not match against replayed rows. We document
    that limitation in the response (samples come back empty) instead
    of silently lying.
    """
    return {
        "observation_id": str(observation.id),
        "camera_id": str(observation.camera_id) if observation.camera_id else None,
        "timestamp": observation.started_at.isoformat() if observation.started_at else None,
        "object_detections": observation.object_detections or {},
        "person_detections": observation.person_detections or {},
        "vlm_description": observation.vlm_description or "",
        "confidence": observation.confidence,
        "thumbnail_path": observation.thumbnail_path,
    }


def _render_actions_preview(actions: list[dict], observation: dict, rule) -> list[RuleTestActionPreview]:
    """Render each action's templated fields against the observation.

    No action is executed. For vlm_call we leave the prompt template
    rendered so the user can preview what would be sent to the model.
    For webhook we render the payload_template into a dict so the user
    sees the final body. For telegram we render the text template.
    """
    from services.events.actions import _build_template_context
    from services.events.templates import render

    # Use a deterministic placeholder event_id so the preview matches
    # what the action chain would see.
    preview_event_id = uuid.uuid4()
    ctx = _build_template_context(observation, rule, preview_event_id)

    out: list[RuleTestActionPreview] = []
    for idx, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        rendered = render(action, ctx, strict=False)
        out.append(RuleTestActionPreview(
            index=idx,
            action_type=str(action.get("type") or "unknown"),
            rendered_action=rendered if isinstance(rendered, dict) else {"value": rendered},
        ))
    return out


def _explain_outcome(
    matched_trigger: bool,
    matched_conditions: bool,
    trigger_pattern: dict,
    conditions: dict | None,
    observation: dict,
    tz,
) -> str:
    """Build a human-friendly explanation for the test result."""
    if not matched_trigger:
        t = trigger_pattern.get("type")
        if t == "object_detected":
            want = trigger_pattern.get("label")
            seen = [d.get("label") for d in (observation.get("object_detections") or {}).get("objects", [])]
            if want:
                return f"Trigger did not match. expected label '{want}', observation had {seen or 'no objects'}."
            return "Trigger did not match. no object detections in observation."
        if t == "motion":
            want = trigger_pattern.get("min_score", 0.01)
            return f"Trigger did not match. motion_score {observation.get('motion_score', 0)} below min_score {want}."
        if t == "face_recognized":
            return "Trigger did not match. no recognized face in observation."
        return f"Trigger did not match. trigger type '{t}' found no matching content."

    if not matched_conditions and conditions:
        # Identify which condition blocked us so the UI can highlight it.
        from datetime import datetime as _dt
        cam_ids = conditions.get("camera_ids")
        cam = conditions.get("camera_id")
        if cam_ids and observation.get("camera_id") not in cam_ids:
            return f"Camera filter blocked. observation camera {observation.get('camera_id')} not in {cam_ids}."
        if cam and not cam_ids and observation.get("camera_id") != cam:
            return f"Camera filter blocked. observation camera {observation.get('camera_id')} != {cam}."
        days = conditions.get("days")
        if days:
            day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
            today = day_map.get(_dt.now(tz).weekday(), "")
            if today not in days:
                return f"Schedule blocked. today is {today}, rule only runs on {days}."
        time_after = conditions.get("time_after")
        time_before = conditions.get("time_before")
        if time_after or time_before:
            now_t = _dt.now(tz).strftime("%H:%M")
            window = f"{time_after or '00:00'}-{time_before or '23:59'}"
            return f"Schedule blocked. current time {now_t} outside window {window}."
        min_conf = conditions.get("min_confidence")
        if min_conf is not None:
            got = observation.get("confidence") or 0
            return f"Confidence too low. min_confidence {min_conf} required, observation has {got}."
        return "Conditions blocked the trigger."

    return "Trigger matched and all conditions passed."


@router.post("/test", response_model=RuleTestResponse)
async def test_rule(
    body: RuleTestRequest,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run a (possibly unsaved) rule against a synthesized observation.

    No event is written. No action is executed. The response includes
    the observation that was evaluated so the UI can show "this is
    what we tested against".
    """
    from services.events.engine import RuleEngine

    # Build the observation: explicit > recent-by-camera > synthesized.
    observation: dict | None = None
    if body.dry_run_observation is not None:
        observation = dict(body.dry_run_observation)
        observation.setdefault("observation_id", str(uuid.uuid4()))
        observation.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        if body.camera_id:
            observation.setdefault("camera_id", str(body.camera_id))
    elif body.camera_id is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        q = (
            select(Observation)
            .where(Observation.camera_id == body.camera_id)
            .where(Observation.started_at >= cutoff)
            .order_by(Observation.started_at.desc())
            .limit(1)
        )
        result = await db.execute(q)
        row = result.scalars().first()
        if row is not None:
            observation = _observation_to_engine_payload(row)

    if observation is None:
        observation = _synthesize_observation_for_trigger(body.trigger_pattern, body.camera_id)

    # Build a fake rule and run the engine's pure matcher methods.
    fake_rule = SimpleNamespace(
        id=uuid.uuid4(),
        name="__test__",
        enabled=True,
        trigger_pattern=body.trigger_pattern,
        conditions=body.conditions,
        actions=body.actions,
        cooldown_seconds=body.cooldown_seconds,
    )
    engine = RuleEngine()
    tz = await engine._resolve_timezone()

    matched_trigger = engine._match_trigger(body.trigger_pattern, observation, fake_rule.id)
    matched_conditions = engine._check_conditions(body.conditions or {}, observation, tz)
    schedule_blocked = matched_trigger and not matched_conditions
    matched = matched_trigger and matched_conditions

    reason = _explain_outcome(
        matched_trigger, matched_conditions, body.trigger_pattern, body.conditions, observation, tz,
    )

    would_fire = _render_actions_preview(body.actions or [], observation, fake_rule)

    return RuleTestResponse(
        matched=matched,
        reason=reason,
        matched_trigger=matched_trigger,
        matched_conditions=matched_conditions,
        schedule_blocked=schedule_blocked,
        cooldown_active=False,
        synthesized_observation=observation,
        would_fire=would_fire,
    )


@router.post("/{rule_id}/replay", response_model=RuleReplayResponse)
async def replay_rule(
    rule_id: uuid.UUID,
    hours: int = Query(default=24, ge=1, le=168),
    limit_samples: int = Query(default=5, ge=1, le=25),
    max_scanned: int = Query(default=10_000, ge=1, le=10_000),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replay a saved rule over the last ``hours`` of real observations.

    Pure read-only. We never write an Event row or execute an action.
    ``samples`` is capped by ``limit_samples``. ``scanned`` is capped
    by ``max_scanned``. ``hours`` is capped at 168 (7 days).

    Note. trigger types that depend on transient runtime fields like
    ``tracks`` (inline-geometry loitering/line_cross), ``audio_event``,
    ``transcript``, or ``clap_pattern`` will not match historical rows
    because those fields are not persisted on the Observation table.
    Those rules return scanned > 0 but matched = 0; the UI should
    show a tooltip explaining that replay is detection-only.
    """
    from services.events.engine import RuleEngine

    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Clamp inputs defensively. Query() bounds already enforce these
    # but a future caller that bypasses the validator (e.g. internal
    # use) still gets the same caps.
    hours = min(max(hours, 1), 168)
    limit_samples = min(max(limit_samples, 1), 25)
    max_scanned = min(max(max_scanned, 1), 10_000)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = (
        select(Observation)
        .where(Observation.started_at >= cutoff)
        .order_by(Observation.started_at.desc())
        .limit(max_scanned)
    )

    conds = rule.conditions or {}
    cam_ids = conds.get("camera_ids")
    cam_id = conds.get("camera_id")
    if cam_ids:
        try:
            uuid_list = [uuid.UUID(str(c)) for c in cam_ids]
            q = q.where(Observation.camera_id.in_(uuid_list))
        except (ValueError, TypeError):
            pass
    elif cam_id:
        try:
            q = q.where(Observation.camera_id == uuid.UUID(str(cam_id)))
        except (ValueError, TypeError):
            pass

    result = await db.execute(q)
    rows = list(result.scalars().all())

    engine = RuleEngine()
    tz = await engine._resolve_timezone()

    scanned = 0
    matched_count = 0
    samples: list[RuleReplaySample] = []
    first_at: datetime | None = None
    last_at: datetime | None = None

    for row in rows:
        scanned += 1
        payload = _observation_to_engine_payload(row)
        if not engine._match_trigger(rule.trigger_pattern, payload, rule.id):
            continue
        if rule.conditions and not engine._check_conditions(rule.conditions, payload, tz):
            continue
        matched_count += 1
        ts = row.started_at
        if first_at is None or (ts and ts < first_at):
            first_at = ts
        if last_at is None or (ts and ts > last_at):
            last_at = ts
        if len(samples) < limit_samples:
            snippet = None
            if row.vlm_description:
                snippet = row.vlm_description[:140]
            else:
                # Fallback. compact detection summary so the UI has
                # something to show when the VLM did not run.
                objs = (row.object_detections or {}).get("objects") if isinstance(row.object_detections, dict) else None
                if objs:
                    labels = sorted({str(d.get("label")) for d in objs if d.get("label")})
                    if labels:
                        snippet = ", ".join(labels)[:140]
            samples.append(RuleReplaySample(
                observation_id=row.id,
                timestamp=row.started_at,
                camera_id=row.camera_id,
                thumbnail_path=row.thumbnail_path,
                snippet=snippet,
            ))

    return RuleReplayResponse(
        rule_id=rule_id,
        hours=hours,
        scanned=scanned,
        matched=matched_count,
        first_matched_at=first_at,
        last_matched_at=last_at,
        samples=samples,
    )
