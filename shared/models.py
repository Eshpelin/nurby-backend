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
    vlm_max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Cascade refiner. When set, the primary VLM's output is post-
    # processed by the refiner provider whenever a trigger matches.
    vlm_refiner_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    vlm_refiner_trigger_objects: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vlm_refiner_keywords: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vlm_refiner_max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vlm_refiner_max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    webcam_device: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Audio-only mode. When true the ingestion + perception pipelines
    # skip video decode and run only the audio path (VAD, STT, audio
    # events, clap pattern, speech phrase). UI hides the video tile.
    audio_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Audio transcription config (Phase 1)
    audio_capture_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    audio_transcribe_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    audio_store_raw: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    transcript_store: Mapped[str] = mapped_column(String(16), default="full", nullable=False)  # full, redacted, summary_only
    audio_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    audio_retention_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    transcript_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    stt_provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True)
    stt_budget_minutes_per_hour: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # Summarization config (window-level VLM recap)
    summary_provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    summary_mode: Mapped[str] = mapped_column(String(16), default="off", nullable=False)  # off, periodic, event, both
    summary_period_seconds: Mapped[int] = mapped_column(Integer, default=1800, nullable=False)  # 30 min default
    summary_event_quiet_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    summary_event_trigger_objects: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # YOLO labels e.g. ["person"]
    summary_event_min_duration_seconds: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    summary_max_tokens: Mapped[int] = mapped_column(Integer, default=400, nullable=False)
    # Conversation grouping (audio rollup)
    conversation_gap_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    conversation_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    conversation_min_messages_for_summary: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # Incident tracking. Persistent server-side grouping of related
    # observations into one rolling artifact with a stable id.
    incident_tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    incident_idle_seconds: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
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
    blur_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    blur_error: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    privacy_blur: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    photo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recap_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    recap_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recap_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recap_cached_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    recap_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recap_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding = mapped_column(Vector(512), nullable=False)  # 512-dim InsightFace ArcFace embedding
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload | detection
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaceCluster(Base):
    __tablename__ = "face_clusters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    representative_embedding = mapped_column(Vector(512), nullable=False)  # average embedding of cluster (InsightFace ArcFace)
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
    embedding = mapped_column(Vector(512), nullable=False)
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
    # Cascade history. When the refiner stage replaces the primary
    # text on this observation, the original primary output is moved
    # here so the UI can show a before/after comparison.
    primary_vlm_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    refined_by_provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Incident link. Set by the perception pipeline at insert time
    # when incident tracking is enabled on the camera. Null means the
    # observation stands alone or tracking was off when it landed.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )


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
    # Token caps. NULL = no cap, defer to the provider's model default.
    # Per-camera vlm_max_tokens / summary_max_tokens further tighten
    # the output cap when set.
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AudioCapture(Base):
    __tablename__ = "audio_captures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    codec: Mapped[str] = mapped_column(String(16), default="opus", nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, default=16000, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    audio_capture_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audio_captures.id", ondelete="SET NULL"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_speech_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    words: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    filtered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    speaker_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True
    )
    speaker_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    speaker_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # video, voice, fused, ambiguous
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AudioAuditLog(Base):
    __tablename__ = "audio_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AudioDetection(Base):
    __tablename__ = "audio_detections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Normalized class name. baby_cry, scream, speech, glass_break, alarm, bark, gunshot
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    # Raw AudioSet class (useful for debugging / future remapping)
    raw_class: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Summary(Base):
    """Window-level recap generated by a VLM over many observations.

    A row is the closing artifact of a periodic timer or event window.
    Holds the narrative text, the IDs of source observations and
    transcripts, and aggregated facts (people seen, plates, object
    counts) so the UI can render the recap without joining back to
    every source row.
    """

    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # periodic | event
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_observation_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_transcript_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    people_seen: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plates_seen: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    object_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Conversation(Base):
    """A rolling group of consecutive transcripts on a camera.

    Boundary is a gap heuristic. transcripts whose start is within
    ``conversation_gap_seconds`` of the previous transcript's end on
    the same camera belong to the same conversation. The conversation
    is marked ``finalized`` and summarized after the gap window passes
    with no new transcript.
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Advances every time a transcript is appended.
    ended_at_provisional: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set when the conversation closes.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transcript_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    speakers_seen: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    clip_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    clip_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Incident(Base):
    """Server-side rolling artifact that groups related observations.

    Signature key + camera + idle window define when an incident
    accepts another observation. The pipeline opens / extends rows
    inline at observation insert time. The finalizer worker closes
    rows whose ``last_seen_at`` is past the camera's idle window and
    optionally generates a summary.
    """

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    signature_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    signature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    peak_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("observations.id", ondelete="SET NULL"), nullable=True
    )
    observation_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    thumbnails: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    journey_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journeys.id", ondelete="SET NULL"), nullable=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Journey(Base):
    """Cross-camera story for one subject.

    Groups Incident rows for the same named person or face cluster
    across multiple cameras within an idle window. Segments are
    time-ordered slices of presence on each camera; transitions
    capture camera-to-camera movement gaps.
    """

    __tablename__ = "journeys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    segments: Mapped[dict] = mapped_column(JSON, nullable=False)
    transitions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cameras_seen_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    incidents_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
