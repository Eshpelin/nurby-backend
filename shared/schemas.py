import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── Camera schemas ──

class CameraCreate(BaseModel):
    name: str
    stream_url: str
    snapshot_url: str | None = None
    location_label: str | None = None
    motion_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    recording_enabled: bool = True


class CameraUpdate(BaseModel):
    name: str | None = None
    stream_url: str | None = None
    snapshot_url: str | None = None
    location_label: str | None = None
    motion_sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    recording_enabled: bool | None = None


class CameraResponse(BaseModel):
    id: uuid.UUID
    name: str
    stream_url: str
    snapshot_url: str | None
    location_label: str | None
    motion_sensitivity: float
    recording_enabled: bool
    status: str
    width: int | None
    height: int | None
    fps: float | None
    created_at: datetime
    updated_at: datetime

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
