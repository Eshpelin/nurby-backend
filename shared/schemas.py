import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
    detect_objects: bool = True
    detect_faces: bool = True
    scene_mode: str = Field(default="indoor", max_length=16)  # indoor, outdoor
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
    detect_objects: bool | None = None
    detect_faces: bool | None = None
    scene_mode: str | None = Field(default=None, max_length=16)
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
    detect_objects: bool
    detect_faces: bool
    scene_mode: str
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
    relationship: str | None = Field(default=None, max_length=64)
    consent_given: bool = False
    privacy_blur: bool = False
    is_starred: bool = False
    recap_prompt: str | None = Field(default=None, max_length=2000)
    recap_provider: str | None = Field(default=None, max_length=32)
    recap_model: str | None = Field(default=None, max_length=255)


class PersonUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
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
    "webhook", "api_call", "broadcast", "notify", "email", "vlm_call",
}


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


# ── Event schemas ──

class EventResponse(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID | None
    observation_id: uuid.UUID | None
    fired_at: datetime
    payload: dict | None
    acknowledged_at: datetime | None
    action_status: str
    action_error: str | None
    action_type: str | None

    model_config = {"from_attributes": True}


# ── Provider schemas ──

class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=32)
    base_url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, max_length=255)
    active: bool = True


class ProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str
    default_model: str | None
    active: bool
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
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


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
