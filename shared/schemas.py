import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── Camera schemas ──

class CameraCreate(BaseModel):
    name: str
    stream_url: str
    stream_type: str = "rtsp"  # rtsp, http_mjpeg, http_snapshot, hls, usb, file
    snapshot_url: str | None = None
    location_label: str | None = None
    username: str | None = None
    password: str | None = None
    auth_token: str | None = None
    snapshot_interval: float = Field(default=2.0, ge=0.5, le=60.0)
    motion_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    recording_enabled: bool = True
    vlm_provider_id: uuid.UUID | None = None
    vlm_prompt: str | None = None
    vlm_interval: int = Field(default=0, ge=0, le=3600)
    vlm_max_tokens: int = Field(default=200, ge=50, le=2000)
    detect_objects: bool = True
    detect_faces: bool = True
    object_confidence: float = Field(default=0.35, ge=0.05, le=1.0)
    vlm_trigger: str = "always"  # always, on_object
    vlm_trigger_objects: list[str] | None = None
    detection_models: list[dict] | None = None
    detection_merge: str = "any"
    detection_consensus_min: int = Field(default=2, ge=1, le=10)
    digest_enabled: bool = True
    digest_period: str = "24h"
    digest_provider_id: uuid.UUID | None = None
    digest_prompt: str | None = None
    retention_mode: str = "none"  # none, time, size
    retention_days: int = Field(default=30, ge=1, le=3650)
    retention_gb: float = Field(default=50.0, ge=1.0, le=10000.0)


class CameraUpdate(BaseModel):
    name: str | None = None
    stream_url: str | None = None
    stream_type: str | None = None
    snapshot_url: str | None = None
    location_label: str | None = None
    username: str | None = None
    password: str | None = None
    auth_token: str | None = None
    snapshot_interval: float | None = Field(default=None, ge=0.5, le=60.0)
    motion_sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    recording_enabled: bool | None = None
    vlm_provider_id: uuid.UUID | None = None
    vlm_prompt: str | None = None
    vlm_interval: int | None = Field(default=None, ge=0, le=3600)
    vlm_max_tokens: int | None = Field(default=None, ge=50, le=2000)
    detect_objects: bool | None = None
    detect_faces: bool | None = None
    object_confidence: float | None = Field(default=None, ge=0.05, le=1.0)
    vlm_trigger: str | None = None
    vlm_trigger_objects: list[str] | None = None
    detection_models: list[dict] | None = None
    detection_merge: str | None = None
    detection_consensus_min: int | None = Field(default=None, ge=1, le=10)
    digest_enabled: bool | None = None
    digest_period: str | None = None
    digest_provider_id: uuid.UUID | None = None
    digest_prompt: str | None = None
    retention_mode: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    retention_gb: float | None = Field(default=None, ge=1.0, le=10000.0)


class CameraResponse(BaseModel):
    id: uuid.UUID
    name: str
    stream_url: str
    stream_type: str
    snapshot_url: str | None
    location_label: str | None
    username: str | None
    auth_token: str | None
    snapshot_interval: float
    motion_sensitivity: float
    recording_enabled: bool
    vlm_provider_id: uuid.UUID | None
    vlm_prompt: str | None
    vlm_interval: int
    vlm_max_tokens: int
    detect_objects: bool
    detect_faces: bool
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
    status: str
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

    model_config = {"from_attributes": True}


# ── Person schemas ──

class PersonCreate(BaseModel):
    display_name: str
    relationship: str | None = None
    consent_given: bool = False


class PersonUpdate(BaseModel):
    display_name: str | None = None
    relationship: str | None = None
    consent_given: bool | None = None


class PersonResponse(BaseModel):
    id: uuid.UUID
    display_name: str
    relationship: str | None
    consent_given: bool
    photo_path: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


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


# ── Rule schemas ──

class RuleCreate(BaseModel):
    name: str
    enabled: bool = True
    trigger_pattern: dict
    conditions: dict | None = None
    actions: dict
    cooldown_seconds: int = 300


class RuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    trigger_pattern: dict
    conditions: dict | None
    actions: dict
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

    model_config = {"from_attributes": True}


# ── Provider schemas ──

class ProviderCreate(BaseModel):
    name: str
    kind: str
    base_url: str
    api_key: str | None = None
    default_model: str | None = None
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


# ── System schemas ──

class SystemStatus(BaseModel):
    version: str
    cameras_total: int
    cameras_online: int
    cameras_recording: int
    uptime_seconds: float
