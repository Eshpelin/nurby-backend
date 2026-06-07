import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Camera schemas ──

class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    stream_url: str = Field(min_length=1, max_length=1024)
    stream_type: str = Field(default="rtsp", max_length=32)  # rtsp, http_mjpeg, http_snapshot, hls, usb, file
    snapshot_url: str | None = Field(default=None, max_length=1024)
    location_label: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    auth_token: str | None = Field(default=None, max_length=512)
    snapshot_interval: float = Field(default=2.0, ge=0.5, le=60.0)
    motion_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    recording_enabled: bool = True
    recording_mode: str = "always"
    recording_trigger_objects: list[str] | None = None
    recording_clip_pre: int = Field(default=5, ge=1, le=30)
    recording_clip_post: int = Field(default=10, ge=1, le=60)
    vlm_provider_id: uuid.UUID | None = None
    vlm_prompt: str | None = Field(default=None, max_length=4096)
    vlm_interval: int = Field(default=0, ge=0, le=3600)
    vlm_max_tokens: int = Field(default=200, ge=50, le=2000)
    vlm_max_input_tokens: int | None = Field(default=None, ge=64, le=2_000_000)
    vlm_refiner_provider_id: uuid.UUID | None = None
    vlm_refiner_trigger_objects: list[str] | None = None
    vlm_refiner_keywords: list[str] | None = None
    vlm_refiner_max_tokens: int | None = Field(default=None, ge=50, le=2000)
    vlm_refiner_max_input_tokens: int | None = Field(default=None, ge=64, le=2_000_000)
    detect_objects: bool = True
    detect_faces: bool = True
    scene_mode: str = Field(default="indoor", max_length=16)  # indoor, outdoor
    plateless_reid_enabled: bool | None = None  # None = auto (off outdoors)
    object_confidence: float = Field(default=0.35, ge=0.05, le=1.0)
    vlm_trigger: str = Field(default="always", max_length=16)  # always, on_object
    vlm_trigger_objects: list[str] | None = None
    detection_models: list[dict] | None = None
    detection_merge: str = Field(default="any", max_length=16)
    detection_consensus_min: int = Field(default=2, ge=1, le=10)
    digest_enabled: bool = True
    digest_period: str = "24h"
    digest_provider_id: uuid.UUID | None = None
    digest_prompt: str | None = Field(default=None, max_length=4096)
    retention_mode: str = Field(default="none", max_length=16)  # none, time, size
    retention_days: int = Field(default=30, ge=1, le=3650)
    retention_gb: float = Field(default=50.0, ge=1.0, le=10000.0)
    motion_zones: list[dict] | None = None
    webcam_device: str | None = Field(default=None, max_length=255)
    audio_only: bool = False
    privacy_zone_targets: list[str] | None = None
    privacy_zone_blur_strength: int = Field(default=55, ge=5, le=151)
    yolo_world_prompts: list[str] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    # Summary config
    summary_provider_id: uuid.UUID | None = None
    summary_mode: str = Field(default="off", max_length=16)
    summary_period_seconds: int = Field(default=1800, ge=60, le=86400)
    summary_event_quiet_seconds: int = Field(default=60, ge=5, le=3600)
    summary_event_trigger_objects: list[str] | None = None
    summary_event_min_duration_seconds: int = Field(default=5, ge=1, le=3600)
    summary_max_tokens: int = Field(default=400, ge=50, le=2000)
    # Conversation grouping
    conversation_gap_seconds: int = Field(default=30, ge=5, le=600)
    conversation_summary_enabled: bool = True
    conversation_min_messages_for_summary: int = Field(default=2, ge=1, le=20)
    incident_tracking_enabled: bool = True
    incident_idle_seconds: int = Field(default=600, ge=30, le=86400)


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    stream_url: str | None = Field(default=None, min_length=1, max_length=1024)
    stream_type: str | None = Field(default=None, max_length=32)
    snapshot_url: str | None = Field(default=None, max_length=1024)
    location_label: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    auth_token: str | None = Field(default=None, max_length=512)
    snapshot_interval: float | None = Field(default=None, ge=0.5, le=60.0)
    motion_sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    recording_enabled: bool | None = None
    recording_mode: str | None = None
    recording_trigger_objects: list[str] | None = None
    recording_clip_pre: int | None = Field(default=None, ge=1, le=30)
    recording_clip_post: int | None = Field(default=None, ge=1, le=60)
    vlm_provider_id: uuid.UUID | None = None
    vlm_prompt: str | None = Field(default=None, max_length=4096)
    vlm_interval: int | None = Field(default=None, ge=0, le=3600)
    vlm_max_tokens: int | None = Field(default=None, ge=50, le=2000)
    vlm_max_input_tokens: int | None = Field(default=None, ge=64, le=2_000_000)
    vlm_refiner_provider_id: uuid.UUID | None = None
    vlm_refiner_trigger_objects: list[str] | None = None
    vlm_refiner_keywords: list[str] | None = None
    vlm_refiner_max_tokens: int | None = Field(default=None, ge=50, le=2000)
    vlm_refiner_max_input_tokens: int | None = Field(default=None, ge=64, le=2_000_000)
    detect_objects: bool | None = None
    detect_faces: bool | None = None
    scene_mode: str | None = Field(default=None, max_length=16)
    plateless_reid_enabled: bool | None = None  # null = auto
    object_confidence: float | None = Field(default=None, ge=0.05, le=1.0)
    vlm_trigger: str | None = Field(default=None, max_length=16)
    vlm_trigger_objects: list[str] | None = None
    detection_models: list[dict] | None = None
    detection_merge: str | None = Field(default=None, max_length=16)
    detection_consensus_min: int | None = Field(default=None, ge=1, le=10)
    digest_enabled: bool | None = None
    digest_period: str | None = None
    digest_provider_id: uuid.UUID | None = None
    digest_prompt: str | None = Field(default=None, max_length=4096)
    retention_mode: str | None = Field(default=None, max_length=16)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    retention_gb: float | None = Field(default=None, ge=1.0, le=10000.0)
    motion_zones: list[dict] | None = None
    webcam_device: str | None = Field(default=None, max_length=255)
    audio_only: bool | None = None
    privacy_zone_targets: list[str] | None = None
    privacy_zone_blur_strength: int | None = Field(default=None, ge=5, le=151)
    yolo_world_prompts: list[str] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    display_order: int | None = None
    # Summary config
    summary_provider_id: uuid.UUID | None = None
    summary_mode: str | None = Field(default=None, max_length=16)
    summary_period_seconds: int | None = Field(default=None, ge=60, le=86400)
    summary_event_quiet_seconds: int | None = Field(default=None, ge=5, le=3600)
    summary_event_trigger_objects: list[str] | None = None
    summary_event_min_duration_seconds: int | None = Field(default=None, ge=1, le=3600)
    summary_max_tokens: int | None = Field(default=None, ge=50, le=2000)
    # Conversation grouping
    conversation_gap_seconds: int | None = Field(default=None, ge=5, le=600)
    conversation_summary_enabled: bool | None = None
    conversation_min_messages_for_summary: int | None = Field(default=None, ge=1, le=20)
    incident_tracking_enabled: bool | None = None
    incident_idle_seconds: int | None = Field(default=None, ge=30, le=86400)
    # Smart Track
    ptz_smart_track_enabled: bool | None = None
    ptz_smart_track_targets: list[str] | None = None
    ptz_smart_track_ignore: list[str] | None = None
    ptz_smart_track_priority: list[str] | None = None
    ptz_smart_track_lost_seconds: int | None = Field(default=None, ge=1, le=300)
    ptz_smart_track_home_preset: str | None = Field(default=None, max_length=64)
    ptz_smart_track_zoom: bool | None = None
    ptz_smart_track_deadzone: float | None = Field(default=None, ge=0.0, le=0.5)
    ptz_smart_track_max_speed: float | None = Field(default=None, ge=0.05, le=1.0)
    ptz_smart_track_gain: float | None = Field(default=None, ge=0.1, le=5.0)
    ptz_smart_track_no_go: list[dict] | None = None
    ptz_smart_track_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    ptz_smart_track_require_face: list[uuid.UUID] | None = None
    ptz_smart_track_move_budget_per_minute: int | None = Field(default=None, ge=1, le=600)
    ptz_profile_token: str | None = Field(default=None, max_length=64)


class CameraReorderItem(BaseModel):
    id: uuid.UUID
    display_order: int


class CameraResponse(BaseModel):
    id: uuid.UUID
    name: str
    stream_url: str
    stream_type: str
    snapshot_url: str | None
    location_label: str | None
    has_credentials: bool = False
    snapshot_interval: float
    motion_sensitivity: float
    recording_enabled: bool
    recording_mode: str
    recording_trigger_objects: list[str] | None
    recording_clip_pre: int
    recording_clip_post: int
    vlm_provider_id: uuid.UUID | None
    vlm_prompt: str | None
    vlm_interval: int
    vlm_max_tokens: int
    vlm_max_input_tokens: int | None = None
    vlm_refiner_provider_id: uuid.UUID | None = None
    vlm_refiner_trigger_objects: list[str] | None = None
    vlm_refiner_keywords: list[str] | None = None
    vlm_refiner_max_tokens: int | None = None
    vlm_refiner_max_input_tokens: int | None = None
    detect_objects: bool
    detect_faces: bool
    scene_mode: str
    plateless_reid_enabled: bool | None = None
    object_confidence: float
    vlm_trigger: str
    vlm_trigger_objects: list[str] | None
    detection_models: list[dict] | None
    detection_merge: str
    detection_consensus_min: int
    digest_enabled: bool
    digest_period: str
    digest_provider_id: uuid.UUID | None
    digest_prompt: str | None
    retention_mode: str
    retention_days: int
    retention_gb: float
    motion_zones: list[dict] | None
    status: str
    display_order: int = 0
    webcam_device: str | None = None
    audio_only: bool = False
    privacy_zone_targets: list[str] | None = None
    privacy_zone_blur_strength: int = 55
    yolo_world_prompts: list[str] | None = None
    timezone: str | None = None
    summary_provider_id: uuid.UUID | None = None
    summary_mode: str = "off"
    summary_period_seconds: int = 1800
    summary_event_quiet_seconds: int = 60
    summary_event_trigger_objects: list[str] | None = None
    summary_event_min_duration_seconds: int = 5
    summary_max_tokens: int = 400
    conversation_gap_seconds: int = 30
    conversation_summary_enabled: bool = True
    conversation_min_messages_for_summary: int = 2
    incident_tracking_enabled: bool = True
    incident_idle_seconds: int = 600
    ptz_smart_track_enabled: bool = False
    ptz_smart_track_targets: list[str] | None = None
    ptz_smart_track_ignore: list[str] | None = None
    ptz_smart_track_priority: list[str] | None = None
    ptz_smart_track_lost_seconds: int = 3
    ptz_smart_track_home_preset: str | None = None
    ptz_smart_track_zoom: bool = False
    ptz_smart_track_deadzone: float = 0.15
    ptz_smart_track_max_speed: float = 0.5
    ptz_smart_track_gain: float = 1.5
    ptz_smart_track_no_go: list[dict] | None = None
    ptz_smart_track_min_confidence: float = 0.45
    ptz_smart_track_require_face: list[uuid.UUID] | None = None
    ptz_smart_track_move_budget_per_minute: int = 30
    ptz_profile_token: str = "Profile_1"
    width: int | None
    height: int | None
    fps: float | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Camera status log schemas ──

class CameraStatusLogResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    status: str
    previous_status: str | None
    reason: str | None
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── Recording schemas ──

class RecordingResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    file_path: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    file_size_bytes: int | None
    thumbnail_path: str | None
    blur_status: str = "pending"
    blur_error: str | None = None

    model_config = {"from_attributes": True}


# ── Person schemas ──

class PersonCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    nickname: str | None = Field(default=None, max_length=255)
    relationship: str | None = Field(default=None, max_length=64)
    consent_given: bool = False
    privacy_blur: bool = False
    is_starred: bool = False
    recap_prompt: str | None = Field(default=None, max_length=2000)
    recap_provider: str | None = Field(default=None, max_length=32)
    recap_model: str | None = Field(default=None, max_length=255)


class PersonUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    nickname: str | None = Field(default=None, max_length=255)
    relationship: str | None = Field(default=None, max_length=64)
    consent_given: bool | None = None
    privacy_blur: bool | None = None
    is_starred: bool | None = None
    recap_prompt: str | None = Field(default=None, max_length=2000)
    recap_provider: str | None = Field(default=None, max_length=32)
    recap_model: str | None = Field(default=None, max_length=255)


class PersonResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    nickname: str | None
    relationship: str | None
    consent_given: bool
    privacy_blur: bool
    photo_path: str | None
    is_starred: bool
    recap_prompt: str | None
    recap_provider: str | None
    recap_model: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PersonRecapResponse(BaseModel):
    person_id: uuid.UUID
    display_name: str
    photo_path: str | None
    status: str
    last_seen_at: datetime | None
    last_camera_id: uuid.UUID | None
    last_camera_name: str | None
    last_thumbnail_path: str | None
    last_observation_id: uuid.UUID | None = None
    sightings_24h: int
    generated_at: datetime
    cached: bool
    stale: bool


# ── Observation schemas ──

class ObservationResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    object_detections: dict | None
    person_detections: dict | None
    vlm_description: str | None
    vlm_provider: str | None
    confidence: float | None
    thumbnail_path: str | None
    clip_path: str | None
    primary_vlm_description: str | None = None
    refined_by_provider_name: str | None = None
    refined_at: datetime | None = None
    incident_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


# ── Notification schemas ──

class NotificationResponse(BaseModel):
    id: uuid.UUID
    message: str
    severity: str
    rule_id: uuid.UUID | None
    camera_id: uuid.UUID | None
    observation_id: uuid.UUID | None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Rule schemas ──

_VALID_ACTION_TYPES = {
    "webhook", "api_call", "broadcast", "notify", "email", "vlm_call", "telegram",
    "verify",
}

# Phase 2 inline-button actions. Kept in lockstep with
# ``services.events.actions.TELEGRAM_BUTTON_ACTIONS`` and the
# ``_CALLBACK_ACTIONS`` tuple in the Telegram poller. Phase 4 will add
# variants like ``name_cluster``; extending this set is the documented
# extension point.
_VALID_TELEGRAM_BUTTON_ACTIONS = {
    "ack",
    "mute_event",
    "snooze_rule",
    "open",
    # Phase 4. Deep-link to a cluster on the web (uses ``url=``) and
    # kick off an in-chat naming dialog. The cluster-naming actions are
    # only emitted by the system-initiated cluster prompts, not the
    # rule builder UI. Their schema validation still lives here so any
    # internal caller routes through the same allowlist.
    "open_cluster",
    "name_cluster_telegram",
    # Phase 4 stretch. yes/no follow-up answer captured onto event_notes.
    "yn_yes",
    "yn_no",
}
_MAX_TELEGRAM_BUTTONS = 4


def _validate_telegram_buttons(buttons, idx: int) -> None:
    if buttons is None:
        return
    if not isinstance(buttons, list):
        raise ValueError(f"action[{idx}] buttons must be a list")
    if len(buttons) > _MAX_TELEGRAM_BUTTONS:
        raise ValueError(
            f"action[{idx}] has {len(buttons)} buttons; max {_MAX_TELEGRAM_BUTTONS}"
        )
    for b_idx, btn in enumerate(buttons):
        if not isinstance(btn, dict):
            raise ValueError(f"action[{idx}].buttons[{b_idx}] must be an object")
        label = btn.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"action[{idx}].buttons[{b_idx}].label is required")
        b_action = btn.get("action")
        if b_action not in _VALID_TELEGRAM_BUTTON_ACTIONS:
            raise ValueError(
                f"action[{idx}].buttons[{b_idx}].action must be one of "
                f"{sorted(_VALID_TELEGRAM_BUTTON_ACTIONS)}"
            )
        if b_action == "open":
            url = btn.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError(
                    f"action[{idx}].buttons[{b_idx}].url is required for action='open'"
                )
        if b_action in ("mute_event", "snooze_rule"):
            dur = btn.get("duration_seconds")
            if dur is not None:
                if not isinstance(dur, int) or isinstance(dur, bool) or dur <= 0:
                    raise ValueError(
                        f"action[{idx}].buttons[{b_idx}].duration_seconds must be a positive int"
                    )


def _validate_verify_action(action, idx: int) -> None:
    """Validate a ``verify`` action. question is a required non-empty
    string; min_confidence (if present) is a float in [0,1]; on_fail (if
    present) is one of {stop, continue}."""
    question = action.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"action[{idx}] verify requires a non-empty 'question'")

    mc = action.get("min_confidence")
    if mc is not None:
        if isinstance(mc, bool) or not isinstance(mc, (int, float)):
            raise ValueError(f"action[{idx}] verify min_confidence must be a number")
        if not (0.0 <= float(mc) <= 1.0):
            raise ValueError(f"action[{idx}] verify min_confidence must be in [0, 1]")

    on_fail = action.get("on_fail")
    if on_fail is not None and on_fail not in ("stop", "continue"):
        raise ValueError(f"action[{idx}] verify on_fail must be 'stop' or 'continue'")


def _validate_action_chain(actions):
    """Static checks. each action is a dict, has a known type, and
    any `{{vars.X.*}}` references point to an `output` declared by a
    previous vlm_call action in the chain.
    """
    import re
    from services.events.templates import collect_refs

    items = actions if isinstance(actions, list) else [actions] if isinstance(actions, dict) else []
    known_outputs: set[str] = set()
    known_schemas: dict[str, dict] = {}

    for idx, action in enumerate(items):
        if not isinstance(action, dict):
            raise ValueError(f"action[{idx}] must be an object")
        a_type = action.get("type")
        if a_type not in _VALID_ACTION_TYPES:
            raise ValueError(f"action[{idx}] has unknown type '{a_type}'")

        if a_type == "telegram":
            _validate_telegram_buttons(action.get("buttons"), idx)

        if a_type == "verify":
            _validate_verify_action(action, idx)

        refs = collect_refs(action)
        for ref in refs:
            if not ref.startswith("vars."):
                continue
            tail = ref[len("vars."):]
            name = tail.split(".", 1)[0]
            if name not in known_outputs:
                raise ValueError(
                    f"action[{idx}] references vars.{name} but no prior action declares output '{name}'"
                )
            # Best-effort top-level schema key check.
            schema = known_schemas.get(name)
            if schema and "." in tail:
                nested = tail.split(".")[1]
                props = schema.get("properties") or {}
                if props and nested not in props:
                    raise ValueError(
                        f"action[{idx}] references vars.{name}.{nested} not in response_schema"
                    )

        if a_type == "vlm_call":
            output = action.get("output")
            if output:
                if not re.fullmatch(r"[a-zA-Z_][\w]*", output):
                    raise ValueError(f"action[{idx}] output '{output}' is not a valid identifier")
                known_outputs.add(output)
                if isinstance(action.get("response_schema"), dict):
                    known_schemas[output] = action["response_schema"]


def _validate_trigger_pattern(trigger_pattern: dict) -> None:
    """Reject geometry-bound trigger shapes that cannot evaluate.

    Loitering and line_cross rules need both a polygon/segment AND a
    camera_id. speech_phrase rules need at least one phrase. The
    perception engine silently no-ops on bad shapes today, so we
    catch them at the API boundary and surface an inline-friendly
    error the frontend can render next to the field.
    """
    if not isinstance(trigger_pattern, dict):
        raise ValueError("trigger_pattern must be an object")

    t = trigger_pattern.get("type")

    if t == "loitering":
        # Legacy zone_name mode is still supported (pipeline pre-
        # computes events). Only the inline-geometry mode needs
        # validation here.
        if "zone_name" in trigger_pattern and trigger_pattern.get("zone_name"):
            return
        pts = trigger_pattern.get("points")
        if not isinstance(pts, list) or len(pts) < 3:
            raise ValueError(
                "Loitering rules need at least 3 zone points and a camera. "
                "Open the geometry editor."
            )
        if not trigger_pattern.get("camera_id"):
            raise ValueError(
                "Loitering rules need a camera_id so the zone is anchored to a feed."
            )

    elif t == "line_cross":
        if "zone_name" in trigger_pattern and trigger_pattern.get("zone_name"):
            return
        pts = trigger_pattern.get("points")
        if not isinstance(pts, list) or len(pts) != 2:
            raise ValueError(
                "Line-cross rules need exactly 2 points and a camera. "
                "Open the geometry editor."
            )
        if not trigger_pattern.get("camera_id"):
            raise ValueError(
                "Line-cross rules need a camera_id so the line is anchored to a feed."
            )

    elif t == "speech_phrase":
        phrases = trigger_pattern.get("phrases")
        if not isinstance(phrases, list) or not [p for p in phrases if str(p).strip()]:
            raise ValueError(
                "Speech-phrase rules need at least one non-empty phrase."
            )


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    trigger_pattern: dict
    conditions: dict | None = None
    actions: dict | list
    cooldown_seconds: int = 300

    @field_validator("actions")
    @classmethod
    def _check_actions(cls, v):
        _validate_action_chain(v)
        return v

    @model_validator(mode="after")
    def _check_trigger(self):
        _validate_trigger_pattern(self.trigger_pattern)
        return self


class RuleUpdate(BaseModel):
    """Partial-update payload for PATCH /rules/{id}.

    Mirrors RuleCreate but every field is optional so the frontend can
    flip ``enabled`` or rename a rule without re-sending the trigger.
    Geometry validation only fires when ``trigger_pattern`` is set.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    trigger_pattern: dict | None = None
    conditions: dict | None = None
    actions: dict | list | None = None
    cooldown_seconds: int | None = None

    @field_validator("actions")
    @classmethod
    def _check_actions(cls, v):
        if v is None:
            return v
        _validate_action_chain(v)
        return v

    @model_validator(mode="after")
    def _check_trigger(self):
        if self.trigger_pattern is not None:
            _validate_trigger_pattern(self.trigger_pattern)
        return self


class RuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    trigger_pattern: dict
    conditions: dict | None
    actions: dict | list
    cooldown_seconds: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Rule test + replay schemas ──
#
# Both endpoints are pure dry-run. ``/rules/test`` never persists an
# event and never executes an action. ``/rules/{id}/replay`` only
# reads Observation rows and returns a tally plus sample matches.

class RuleTestRequest(BaseModel):
    """Payload for POST /api/rules/test.

    The trigger / conditions / actions fields mirror a normal rule body
    so the frontend can send the in-progress draft straight from the
    rule builder. ``camera_id`` and ``dry_run_observation`` are two
    escape hatches. when ``dry_run_observation`` is set, the synth
    step is skipped and the observation is used verbatim. when only
    ``camera_id`` is set, the most recent Observation row for that
    camera (within the last 1h) is used. Otherwise the engine
    synthesizes a permissive observation tailored to the trigger.
    """

    trigger_pattern: dict
    conditions: dict | None = None
    cooldown_seconds: int = 0
    actions: list[dict] = Field(default_factory=list)
    camera_id: uuid.UUID | None = None
    dry_run_observation: dict | None = None

    @field_validator("actions")
    @classmethod
    def _check_actions(cls, v):
        # Run the same chain validator that RuleCreate uses so the
        # frontend sees the same 422 errors before the rule is saved.
        if v:
            _validate_action_chain(v)
        return v

    @model_validator(mode="after")
    def _check_trigger(self):
        _validate_trigger_pattern(self.trigger_pattern)
        return self


class RuleTestActionPreview(BaseModel):
    """One entry in ``would_fire``. ``rendered_action`` is the action
    dict with every ``{{...}}`` token resolved against the synthesized
    observation, but the action itself is never executed."""

    index: int
    action_type: str
    rendered_action: dict


class RuleTestResponse(BaseModel):
    """Response for POST /api/rules/test.

    ``cooldown_active`` is always false for /test (there is no fired
    history to consult). It is kept in the response shape so the UI
    can render the same outcome panel for /test and the future
    "explain last fire" endpoint.
    """

    matched: bool
    reason: str
    matched_trigger: bool
    matched_conditions: bool
    schedule_blocked: bool
    cooldown_active: bool = False
    synthesized_observation: dict
    would_fire: list[RuleTestActionPreview] = Field(default_factory=list)


class RuleReplaySample(BaseModel):
    observation_id: uuid.UUID
    timestamp: datetime
    camera_id: uuid.UUID | None
    thumbnail_path: str | None
    snippet: str | None


class RuleReplayResponse(BaseModel):
    rule_id: uuid.UUID
    hours: int
    scanned: int
    matched: int
    first_matched_at: datetime | None
    last_matched_at: datetime | None
    samples: list[RuleReplaySample] = Field(default_factory=list)


# ── Event schemas ──

class EventResponse(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID | None
    observation_id: uuid.UUID | None
    recording_id: uuid.UUID | None = None
    fired_at: datetime
    payload: dict | None
    acknowledged_at: datetime | None
    action_status: str
    action_error: str | None
    action_type: str | None
    # Phase 2 ack fields. ``acked_via`` is one of ``telegram``,
    # ``web``, ``api`` (or null if not yet acknowledged).
    acked_at: datetime | None = None
    acked_by_user_id: uuid.UUID | None = None
    acked_via: str | None = None
    muted_until: datetime | None = None

    model_config = {"from_attributes": True}


# ── Provider schemas ──

class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=32)
    base_url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, max_length=255)
    active: bool = True
    # NULL = no cap, defer to the provider's model default.
    max_input_tokens: int | None = Field(default=None, ge=64, le=2_000_000)
    max_output_tokens: int | None = Field(default=None, ge=16, le=200_000)


class ProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str
    default_model: str | None
    active: bool
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Face cluster schemas ──

class FaceClusterResponse(BaseModel):
    id: uuid.UUID
    sample_thumbnail_path: str | None
    sighting_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    first_camera_id: uuid.UUID | None
    person_id: uuid.UUID | None
    status: str

    model_config = {"from_attributes": True}


class FaceClusterSampleResponse(BaseModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    camera_id: uuid.UUID
    thumbnail_path: str | None
    captured_at: datetime

    model_config = {"from_attributes": True}


class NameClusterRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    relationship: str | None = Field(default=None, max_length=64)


# ── System schemas ──

class SystemStatus(BaseModel):
    version: str
    cameras_total: int
    cameras_online: int
    cameras_recording: int
    uptime_seconds: float


class CameraStorageStats(BaseModel):
    camera_id: uuid.UUID
    camera_name: str
    recording_count: int
    recording_bytes: int
    observation_count: int
    retention_mode: str
    retention_days: int
    retention_gb: float


class StorageResponse(BaseModel):
    cameras: list[CameraStorageStats]
    total_recording_bytes: int
    total_observations: int


class SystemSettingsResponse(BaseModel):
    """Safe-to-expose subset of runtime flags. Mirrors the whitelist
    in ``services/api/routes/system.py``."""

    system_timezone: str | None = None
    journey_idle_seconds: int = 300
    daily_digest_enabled: bool = True
    daily_digest_hour: int = 7
    nudity_blur: bool = True
    audio_events: bool = True
    body_reid_tentative_decay_days: int = 14
    cluster_naming_min_sightings: int = 3
    public_base_url: str | None = None
    rules_cooldown_backend: str = "redis"
    onboarding_dismissed: bool = False
    vlm_enrichment_enabled: bool = True
    vlm_enrichment_budget_minutes_per_hour: int = 20
    vehicle_appearance_match_min_similarity: float = 0.90
    guardian_enabled: bool = True
    guardian_free_delay_seconds: int = 1800
    guardian_free_image_interval_seconds: int = 3600
    guardian_reveal_min_confidence: float = 0.90
    guardian_max_cameras_per_person: int = 12
    guardian_pickup_detection_enabled: bool = True
    guardian_pickup_window_seconds: int = 120
    guardian_image_blur_radius: int = 12
    guardian_unblurred_clips_enabled: bool = False


class SystemSettingsUpdate(BaseModel):
    """Partial-update body for PATCH /api/system/settings. Pydantic's
    ``extra=forbid`` makes the route reject typos and stray keys with
    a 422 before our whitelist check even runs."""

    model_config = ConfigDict(extra="forbid")

    system_timezone: str | None = None
    journey_idle_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)
    daily_digest_enabled: bool | None = None
    daily_digest_hour: int | None = Field(default=None, ge=0, le=23)
    nudity_blur: bool | None = None
    audio_events: bool | None = None
    body_reid_tentative_decay_days: int | None = Field(default=None, ge=0, le=3650)
    cluster_naming_min_sightings: int | None = Field(default=None, ge=0, le=1000)
    public_base_url: str | None = None
    rules_cooldown_backend: str | None = Field(default=None, pattern="^(redis|memory)$")
    onboarding_dismissed: bool | None = None
    vlm_enrichment_enabled: bool | None = None
    vlm_enrichment_budget_minutes_per_hour: int | None = Field(default=None, ge=0, le=600)
    vehicle_appearance_match_min_similarity: float | None = Field(default=None, ge=0.5, le=1.0)
    guardian_enabled: bool | None = None
    guardian_free_delay_seconds: int | None = Field(default=None, ge=0, le=24 * 3600)
    guardian_free_image_interval_seconds: int | None = Field(default=None, ge=0, le=24 * 3600)
    guardian_reveal_min_confidence: float | None = Field(default=None, ge=0.5, le=1.0)
    guardian_max_cameras_per_person: int | None = Field(default=None, ge=1, le=1000)
    guardian_pickup_detection_enabled: bool | None = None
    guardian_pickup_window_seconds: int | None = Field(default=None, ge=10, le=1800)
    guardian_image_blur_radius: int | None = Field(default=None, ge=1, le=100)
    guardian_unblurred_clips_enabled: bool | None = None


# -- User schemas --

class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=72)
    invite_key: str = Field(min_length=1, max_length=64)


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=72)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    is_active: bool
    # True for the auto-created first-run owner that has not yet set a
    # real email + password. Drives the "Secure your account" prompt.
    is_provisional: bool = False
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


# Pragmatic email shape check. Not full RFC 5322, just enough to stop a
# malformed value from being stored as the login email, which would lock
# the owner out (they could never type a matching string at /login).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountClaim(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class AdminSetup(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v


# -- Invite key schemas --

class InviteKeyCreate(BaseModel):
    role: str = "viewer"
    camera_ids: list[uuid.UUID] | None = None
    max_uses: int = 1
    expires_at: datetime | None = None


class InviteKeyResponse(BaseModel):
    id: uuid.UUID
    key: str
    role: str
    camera_ids: list[uuid.UUID] | None
    max_uses: int
    use_count: int
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# -- Camera access schemas --

class UserCameraAccessResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    camera_id: uuid.UUID
    granted_at: datetime

    model_config = {"from_attributes": True}


class CameraShareRequest(BaseModel):
    user_ids: list[uuid.UUID]


class SetCameraAccessRequest(BaseModel):
    camera_ids: list[uuid.UUID]


# -- Telegram channel schemas --

class TelegramChannelCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    bot_token: str = Field(min_length=20, max_length=512)


class TelegramChannelUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    default_silent: bool | None = None
    enabled: bool | None = None
    # Phase 3 settings. delivery_mode is NOT settable here. use the
    # dedicated /delivery endpoint so we can call setWebhook /
    # deleteWebhook atomically.
    media_quality: str | None = Field(default=None, pattern=r"^(off|low|high)$")
    rate_limit_per_chat_qps: float | None = Field(default=None, ge=0.05, le=10.0)
    rate_limit_per_chat_burst: int | None = Field(default=None, ge=1, le=20)
    dedupe_window_seconds: int | None = Field(default=None, ge=0, le=600)
    # Phase 4. Household sharing. Owner-only; the route rejects PATCH
    # by non-owners. ``share_permissions`` is 'use' or 'use_and_test'.
    shared_with_household: bool | None = None
    share_permissions: str | None = Field(default=None, pattern=r"^(use|use_and_test)$")


class TelegramChannelResponse(BaseModel):
    id: uuid.UUID
    label: str
    bot_username: str | None
    chat_id: str | None
    chat_title: str | None
    chat_type: str | None
    default_silent: bool
    enabled: bool
    paired_at: datetime | None
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_error: str | None
    pairing_status: str  # pending | paired | blocked | disabled | error
    # Phase 3 fields. webhook_secret is intentionally never exposed.
    delivery_mode: str  # long_poll | webhook
    webhook_url: str | None
    media_quality: str  # off | low | high
    rate_limit_per_chat_qps: float
    rate_limit_per_chat_burst: int
    dedupe_window_seconds: int
    # Phase 4. Household sharing. ``owned_by_me`` is computed by the
    # route per-request so a non-owner sees the channel as
    # "shared by <Other>". ``owner_display_name`` is best-effort.
    shared_with_household: bool = False
    share_permissions: str = "use"
    owned_by_me: bool = True
    owner_display_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EventNoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class EventNoteResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    author_user_id: uuid.UUID | None = None
    author_display_name: str | None = None
    source: str  # telegram | web | api
    text: str
    telegram_message_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TelegramDeliveryUpdate(BaseModel):
    """Request body for POST /channels/{id}/delivery."""

    mode: str = Field(pattern=r"^(long_poll|webhook)$")
    # When flipping webhook -> long_poll. ask Telegram to discard
    # pending updates instead of replaying them on the next poll.
    drop_pending_updates: bool = False


class TelegramWebhookInfoResponse(BaseModel):
    """Passthrough of Telegram getWebhookInfo for the settings UI."""

    url: str | None = None
    has_custom_certificate: bool = False
    pending_update_count: int = 0
    last_error_date: int | None = None
    last_error_message: str | None = None
    ip_address: str | None = None
    max_connections: int | None = None
    # Backend reachability check. None means we did not attempt the
    # probe. true/false reflects an HTTP GET from the backend to
    # ``public_base_url + /api/health``.
    backend_reachable: bool | None = None
    backend_probe_error: str | None = None


class TelegramPairInitResponse(BaseModel):
    nonce: str
    deep_link: str
    qr_payload: str
    expires_in_seconds: int


class TelegramTestResponse(BaseModel):
    ok: bool
    message_id: int | None = None
    error: str | None = None


# -- Digest entry schemas --

class DigestEntryResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID | None
    period: str
    summary: str
    highlights: list[str] | None
    stats: dict | None
    total_observations: int
    generated_at: datetime

    model_config = {"from_attributes": True}


# ── Agent v1 (Wave 1A) ──────────────────────────────────────────────
#
# Response models for the agentic Q&A surface. The driver returns
# AgentRunResponse for list endpoints (cheap) and AgentRunDetailResponse
# for the audit page (nested tool calls + vlm calls). AgentAskRequest
# is the user-facing entry point; AgentAskResponse hands back a run_id
# and websocket URL the frontend subscribes to for streaming tokens.


class AgentAskRequest(BaseModel):
    """POST /api/agent/ask body. ``parent_run_id`` carries multi-turn
    follow-ups so the audit page can group them. ``dry_run`` short-
    circuits before any tool execution and returns the planned tool
    sequence only (used by tests + the future budget preview UI)."""

    question: str = Field(min_length=1, max_length=4000)
    provider_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=128)
    parent_run_id: uuid.UUID | None = None
    dry_run: bool = False


class AgentAskResponse(BaseModel):
    """Immediate response from POST /api/agent/ask. The frontend
    subscribes to ``ws_url`` for streamed tokens + tool events."""

    run_id: uuid.UUID
    ws_url: str


class AgentRunResponse(BaseModel):
    """Cheap list-friendly run summary (no nested children)."""

    id: uuid.UUID
    user_id: uuid.UUID
    parent_run_id: uuid.UUID | None
    question: str
    status: str
    final_answer: str | None
    provider_id: uuid.UUID | None
    model: str | None
    turns_used: int
    tokens_in: int
    tokens_out: int
    cost_cents: int
    latency_ms: int | None
    error_message: str | None
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class AgentToolCallResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    turn_index: int
    tool_name: str
    arguments: dict
    result: dict | None
    error_message: str | None
    latency_ms: int | None
    tokens_in: int
    tokens_out: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentVlmCallResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    tool_call_id: uuid.UUID | None
    provider_id: uuid.UUID | None
    model: str | None
    target_kind: str
    observation_id: uuid.UUID | None
    recording_id: uuid.UUID | None
    time_from: datetime | None
    time_to: datetime | None
    frame_count: int
    question: str
    response: dict | None
    confidence: float | None
    tokens_in: int
    tokens_out: int
    cost_cents: int
    cached: bool
    thumbnails_path: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentRunDetailResponse(AgentRunResponse):
    """Full audit-page payload. Nested tool calls + vlm calls."""

    plan: str | None = None
    tool_calls: list[AgentToolCallResponse] = Field(default_factory=list)
    vlm_calls: list[AgentVlmCallResponse] = Field(default_factory=list)


class AgentDailyUsageResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    usage_date: datetime
    tokens_in: int
    tokens_out: int
    cost_cents: int
    run_count: int
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Guardian by Nurby schemas ──

class FacilityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    timezone: str | None = Field(default=None, max_length=64)
    reveal_min_confidence: float | None = Field(default=None, ge=0.5, le=1.0)
    max_cameras_per_person: int | None = Field(default=None, ge=1, le=1000)


class FacilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    reveal_min_confidence: float | None = Field(default=None, ge=0.5, le=1.0)
    max_cameras_per_person: int | None = Field(default=None, ge=1, le=1000)


class FacilityResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str | None
    is_default: bool
    reveal_min_confidence: float | None
    max_cameras_per_person: int | None
    created_at: datetime
    model_config = {"from_attributes": True}


class GuardianLinkCreate(BaseModel):
    # Bind an existing guardian user (by id or email) to an existing person.
    person_id: uuid.UUID
    guardian_user_id: uuid.UUID | None = None
    guardian_email: str | None = Field(default=None, max_length=255)
    facility_id: uuid.UUID | None = None  # defaults to the default facility
    relationship_label: str | None = Field(default=None, max_length=64)
    tier: str = Field(default="full", pattern=r"^(full|summary|alerts_only)$")
    alert_prefs: dict | None = None
    premium: bool = False
    live_presence: bool = False
    live_video: bool = False
    audio: bool = False
    is_primary_parent: bool = False
    reveal_min_confidence: float | None = Field(default=None, ge=0.5, le=1.0)
    expires_at: datetime | None = None


class GuardianLinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relationship_label: str | None = Field(default=None, max_length=64)
    tier: str | None = Field(default=None, pattern=r"^(full|summary|alerts_only)$")
    alert_prefs: dict | None = None
    premium: bool | None = None
    live_presence: bool | None = None
    live_video: bool | None = None
    audio: bool | None = None
    is_primary_parent: bool | None = None
    reveal_min_confidence: float | None = Field(default=None, ge=0.5, le=1.0)
    expires_at: datetime | None = None


class GuardianAlertPrefsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_prefs: dict


class GuardianChannelsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notify_channels: dict


class GuardianLinkResponse(BaseModel):
    id: uuid.UUID
    facility_id: uuid.UUID
    person_id: uuid.UUID
    guardian_user_id: uuid.UUID
    relationship_label: str | None
    tier: str
    alert_prefs: dict | None
    notify_channels: dict | None
    premium: bool
    live_presence: bool
    live_video: bool
    audio: bool
    is_primary_parent: bool
    reveal_min_confidence: float | None
    granted_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    model_config = {"from_attributes": True}


class ApprovedPickupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(default="person", pattern=r"^(person|vehicle)$")
    linked_person_id: uuid.UUID | None = None
    vehicle_plate: str | None = Field(default=None, max_length=32)


class ApprovedPickupResponse(BaseModel):
    id: uuid.UUID
    person_id: uuid.UUID
    name: str
    kind: str
    linked_person_id: uuid.UUID | None
    vehicle_plate: str | None
    photo_path: str | None
    active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class GuardianAccessLogResponse(BaseModel):
    id: uuid.UUID
    guardian_link_id: uuid.UUID
    guardian_user_id: uuid.UUID
    person_id: uuid.UUID
    action: str
    at: datetime
    ip: str | None
    detail: dict | None
    model_config = {"from_attributes": True}
