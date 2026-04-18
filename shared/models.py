import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stream_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    stream_type: Mapped[str] = mapped_column(String(32), default="rtsp")  # rtsp, http_mjpeg, http_snapshot, hls, usb, file
    snapshot_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    location_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    snapshot_interval: Mapped[float] = mapped_column(Float, default=2.0)  # seconds between snapshot pulls
    motion_sensitivity: Mapped[float] = mapped_column(Float, default=0.5)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # deprecated, use recording_mode
    recording_mode: Mapped[str] = mapped_column(String(16), default="always")  # off, always, on_motion, on_object, clip
    recording_trigger_objects: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # labels for on_object mode
    recording_clip_pre: Mapped[int] = mapped_column(Integer, default=5)  # pre-buffer seconds for clip mode
    recording_clip_post: Mapped[int] = mapped_column(Integer, default=10)  # post-buffer seconds for clip mode
    # Per-camera perception config
    vlm_provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True)
    vlm_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)  # custom system prompt override
    vlm_interval: Mapped[int] = mapped_column(Integer, default=0)  # seconds between VLM calls, 0 = every keyframe
    vlm_max_tokens: Mapped[int] = mapped_column(Integer, default=200)
    detect_objects: Mapped[bool] = mapped_column(Boolean, default=True)
    detect_faces: Mapped[bool] = mapped_column(Boolean, default=True)
    scene_mode: Mapped[str] = mapped_column(String(16), default="indoor")  # indoor, outdoor
    object_confidence: Mapped[float] = mapped_column(Float, default=0.35)  # YOLO confidence threshold
    # VLM trigger config
    vlm_trigger: Mapped[str] = mapped_column(String(16), default="always")  # always, on_object
    vlm_trigger_objects: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # e.g. ["person", "cat"]
    # Multi-model detection config
    detection_models: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of {"model", "confidence", "enabled", "label_filter"}
    detection_merge: Mapped[str] = mapped_column(String(16), default="any")  # any, consensus, best
    detection_consensus_min: Mapped[int] = mapped_column(Integer, default=2)  # min models that must agree for consensus mode
    # Per-camera digest config
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_period: Mapped[str] = mapped_column(String(16), default="24h")  # 1h, 6h, 12h, 24h, 48h, 7d
    digest_provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True)
    digest_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Retention policy
    retention_mode: Mapped[str] = mapped_column(String(16), default="none")  # none, time, size
    retention_days: Mapped[int] = mapped_column(Integer, default=30)  # days to keep recordings
    retention_gb: Mapped[float] = mapped_column(Float, default=50.0)  # max GB per camera
    # Motion zones: [{"name": "Zone 1", "points": [[x,y], ...], "type": "include"|"exclude"}]
    motion_zones: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="offline")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CameraStatusLog(Base):
    __tablename__ = "camera_status_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # offline, live, recording, error
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "stream disconnected", "reconnected"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    photo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding = mapped_column(Vector(128), nullable=False)  # 128-dim face embedding
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload | detection
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaceCluster(Base):
    __tablename__ = "face_clusters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    representative_embedding = mapped_column(Vector(128), nullable=False)  # average embedding of cluster
    sample_thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # best face crop
    sighting_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True)  # linked once named
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, named, ignored
    auto_label_number: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)  # "Unknown 645"
    appearance_description: Mapped[str | None] = mapped_column(Text, nullable=True)  # VLM short demographics/clothing
    appearance_description_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, done, failed


class FaceClusterSample(Base):
    __tablename__ = "face_cluster_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("face_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    embedding = mapped_column(Vector(128), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_detections: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    person_detections: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vlm_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vlm_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    clip_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description_embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)


class DigestEntry(Base):
    __tablename__ = "digest_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    highlights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_observations: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_pattern: Mapped[dict] = mapped_column(JSON, nullable=False)
    conditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actions: Mapped[dict] = mapped_column(JSON, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_status: Mapped[str] = mapped_column(String(16), default="pending")
    action_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="viewer")  # admin, viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InviteKey(Base):
    __tablename__ = "invite_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(50), default="viewer")  # role assigned to users who redeem this key
    camera_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of camera UUIDs to grant on redeem
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserCameraAccess(Base):
    __tablename__ = "user_camera_access"
    __table_args__ = (UniqueConstraint("user_id", "camera_id", name="uq_user_camera"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True
    )
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
