import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
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
    # Which facility exposes this camera. Null = unscoped (visible to all
    # facilities), preserving single-household behaviour.
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    # Per-camera plateless vehicle grouping (CLIP appearance re-id). Tri-state.
    # None = auto (on unless the camera is outdoor, where a busy street would
    # spawn too many transient identities). True/False force it. plated
    # vehicles are unaffected.
    plateless_reid_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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
    # Smart privacy zones. AI auto-detects regions on every keyframe
    # matching one of these target labels and blurs them before the
    # frame is encoded for VLM, thumbnail, or recording.
    privacy_zone_targets: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    privacy_zone_blur_strength: Mapped[int] = mapped_column(Integer, default=55, nullable=False)
    # YOLO-World v2 prompt list. Plain-English class names this
    # camera should detect. Only consulted when a yolov8*-worldv2
    # model is in the detection_models list.
    yolo_world_prompts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # IANA timezone string for this camera. Null = use the system
    # timezone setting. Drives timestamp rendering + daily digest
    # anchor selection.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Audio transcription config (Phase 1)
    audio_capture_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    audio_transcribe_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    audio_store_raw: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    transcript_store: Mapped[str] = mapped_column(String(16), default="full", nullable=False)  # full, redacted, summary_only
    audio_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    audio_retention_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    transcript_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    stt_provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True)
    stt_budget_minutes_per_hour: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # STT accuracy/speed knobs. Defaults match the original behavior, so a
    # camera left alone transcribes exactly as before. Raise beam_size for
    # better accuracy on noisy audio at a CPU cost. condition_on_previous
    # carries context across segments (more coherent long speech, but can
    # propagate a transcription error). no_speech_threshold gates silence.
    audio_stt_beam_size: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    audio_stt_condition_on_previous_text: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    audio_stt_no_speech_threshold: Mapped[float] = mapped_column(Float, default=0.6, nullable=False)
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
    # Smart Track. Auto-follow detections via ONVIF PTZ. Reads
    # detections from the perception pipeline, sends ContinuousMove
    # commands to keep the target near frame center, returns to home
    # preset after `lost_seconds` of no target.
    ptz_smart_track_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ptz_smart_track_targets: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # labels to follow
    ptz_smart_track_ignore: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # labels to never follow
    ptz_smart_track_priority: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # tie-break order
    ptz_smart_track_lost_seconds: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    ptz_smart_track_home_preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ptz_smart_track_zoom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ptz_smart_track_deadzone: Mapped[float] = mapped_column(Float, default=0.15, nullable=False)
    ptz_smart_track_max_speed: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    ptz_smart_track_gain: Mapped[float] = mapped_column(Float, default=1.5, nullable=False)
    # Optional ONVIF angle no-go boxes. [{"pan_min":..,"pan_max":..,"tilt_min":..,"tilt_max":..}]
    ptz_smart_track_no_go: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ptz_smart_track_min_confidence: Mapped[float] = mapped_column(Float, default=0.45, nullable=False)
    # Optional. Only follow these Person UUIDs (via face match).
    ptz_smart_track_require_face: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Mechanical wear cap. Max ContinuousMove commands per minute.
    ptz_smart_track_move_budget_per_minute: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # ONVIF media profile token. Most cameras use "Profile_1".
    ptz_profile_token: Mapped[str] = mapped_column(String(64), default="Profile_1", nullable=False)
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
    # Household-wide colloquial name shown in place of display_name across
    # all view surfaces (notifications, digest, timeline, agent answers).
    # Purely presentational. Identity matching, journey signatures, and
    # cluster naming always use the canonical display_name.
    nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    relationship: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which facility this person belongs to. Null = unscoped (every camera).
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    privacy_blur: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    photo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recap_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    recap_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recap_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recap_cached_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    recap_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recap_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Agent v1 (resolution 5, docs/agent-design.md section 17). Marks
    # persons whose audio transcripts should be redacted before being
    # exposed to the agent. Lands now so v2 can flip without another
    # migration; tool layer reads it as a no-op until v2 wires it up.
    audio_redact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    # Phase 4. Stamped once we DM a household admin asking them to name
    # this cluster. Stays null until then so the cluster-naming
    # initiator can lock-step against it without re-prompting.
    naming_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FaceClusterSample(Base):
    __tablename__ = "face_cluster_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("face_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    embedding = mapped_column(Vector(512), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BodyCluster(Base):
    """Cross-camera body re-identification cluster.

    Sibling of FaceCluster. Built from OSNet appearance embeddings on
    full-person bounding boxes. Lets the system recognize the same
    individual across cameras even when the face is not visible, by
    matching clothing, body shape, and color.

    A BodyCluster carries `status` and `confidence`. A "tentative"
    cluster has not yet been face-confirmed. Once a co-occurring face
    cluster gets linked to the same `person_id`, the body cluster is
    promoted to "confirmed".
    """
    __tablename__ = "body_clusters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    representative_embedding = mapped_column(Vector(512), nullable=False)
    representative_color: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sample_thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sighting_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    linked_face_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), default="tentative", nullable=False)
    auto_label_number: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    appearance_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 4. Mirrors FaceCluster.naming_prompted_at.
    naming_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BodyClusterSample(Base):
    __tablename__ = "body_cluster_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("body_clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    embedding = mapped_column(Vector(512), nullable=False)
    color_histogram: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vehicle(Base):
    """A tracked vehicle identity, the vehicle analogue of Person.

    Identity is keyed by ``identity_key``. the license plate when one was
    read (exact), otherwise a normalized appearance description such as
    "red forklift" (approximate, for plateless vehicles). The perception
    pipeline upserts a Vehicle per detected vehicle and links each sighting
    through ``Observation.vehicle_detections`` (mirrors person_detections),
    so sightings are queried from observations exactly like persons.
    """

    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stable dedupe key. plate text (uppercased, spaces stripped) or
    # "type:description" for plateless vehicles. Unique so the pipeline can
    # upsert without races.
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_plate: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # car, truck, bus, motorcycle, van, forklift
    make: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # VLM one-liner. "Red Nissan sedan with tinted windows". Generated once
    # per vehicle so it does not re-run every frame.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, done, failed
    photo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # True until a human confirms/renames it. auto-created plate/appearance
    # vehicles start provisional so the UI can offer them as suggestions.
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sighting_count: Mapped[int] = mapped_column(Integer, default=1)
    # Plateless identity. a vehicle with no readable plate (forklift, car at
    # a bad angle) is re-identified by its CLIP appearance embedding instead.
    # plateless=True marks these, and appearance_embedding holds the running
    # representative so a recurring vehicle collapses to one row without a
    # plate. Plated vehicles leave both unset.
    plateless: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    appearance_embedding: Mapped[list[float] | None] = mapped_column(Vector(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_detections: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    person_detections: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Vehicle sightings in this frame. mirrors person_detections. shape.
    # {"vehicles": [{"bbox": [...], "label": "car", "plate_text": str|None,
    #   "vehicle_id": uuid|None, "identity_key": str, "confidence": float}],
    #  "count": int}. Drives the Vehicles tab the same way person_detections
    # drives People.
    vehicle_detections: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    # VLM backlog drift. When the VLM worker patches in the description
    # more than 60 seconds after the keyframe landed, vlm_late is set
    # and vlm_enqueued_at carries the original enqueue timestamp. The
    # UI shows a small clock icon on late captions; the agent's
    # summarize_activity rolls a 'pending' bucket from these.
    vlm_late: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vlm_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Idle enrichment bookkeeping. How many VLM passes (including the
    # original live pass) exist for this observation, and when the last
    # enrichment ran. Denormalized so candidate selection does not have
    # to aggregate observation_vlm_passes on every scan.
    enrich_pass_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ObservationAction(Base):
    """Structured per-person action for one observation frame.

    A parallel, queryable signal alongside ``Observation.vlm_description``. The
    perception pipeline classifies each recognised dependant's body crop into a
    closed action vocabulary (see ``services.perception.actions.ACTIONS``) and
    appends one row per person here. Unlike the prose caption, this is indexable,
    so "every meal Mum attended this week" or "did Dad fall" become real queries.

    Rows are written only for recognised dependants in frame (the action pass is
    gated on dependant presence to bound VLM cost), so ``person_id`` /
    ``person_name`` are normally set. ``action`` is always one of the closed
    vocabulary; ``posture`` is advisory; ``confidence`` is the model's own,
    nullable when the provider did not return one.
    """

    __tablename__ = "observation_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("observations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    person_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    posture: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Open-world description of what this person was doing, beyond the closed
    # action enum (objects held, clothing, finer activity). Free text from the
    # VLM, nullable. The closed action stays the queryable anchor; this holds the
    # nuance the enum cannot.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PersonActionSegment(Base):
    """A contiguous run of one action by one tracked person (HAR timeline source).

    Written by the HAR state machine on a transition (or on track loss): merged, debounced
    action runs with a start and an end. Unlike ``observation_actions`` (keyframe-anchored,
    one row per observation), this is **track-anchored and observation-independent** so it can
    capture continuous activity between keyframes. It is the cheap range-query source for the
    per-camera activity timeline and the wellbeing rollups.

    Identity is the held binding from ``identity_binding``: ``person_id`` is set only for a
    recognised, consented person; segments for unknown/body-only tracks either carry no
    ``person_id`` or are dropped before write, and are never shown on a guardian surface.
    Has its own age-based retention (``har_segment_retention_days``) because, unlike
    observations, continuous HAR would otherwise grow this table without bound.
    """

    __tablename__ = "person_action_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    person_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # skeleton | skeleton+vlm | geometric — provenance for trust + the training set.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ObservationVlmPass(Base):
    """A single versioned VLM pass over one observation's frame.

    Append-only. The original live caption is stored as ``pass_no=1,
    lens='live'``; idle enrichment appends later passes with different
    lenses. ``Observation.vlm_description`` stays authoritative and points
    at whichever pass the reduce step blesses (tracked by ``authoritative``
    here), but no pass is ever destroyed, so enrichment is fully
    reversible and auditable.
    """

    __tablename__ = "observation_vlm_passes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("observations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    pass_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # live | attributes | temporal | anomaly | reduce
    lens: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), default="v1", nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured extraction. objects, colors, text/plates read, counts,
    # time-of-day cues. Drives search and rules in later phases.
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True for the pass currently surfaced as the observation's
    # authoritative caption. At most one per observation.
    authoritative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Set true once a later reduce pass reconciles and replaces this one.
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("observation_id", "pass_no", name="ux_obs_pass_no"),
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
    # Per-guardian inbox. Null = household/operator-wide notification (the
    # original behaviour); set = a private copy for one guardian user.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
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
    # Rule-level snooze. Set by the "Snooze rule 1h" Telegram button.
    # While now() < snoozed_until, the telegram action skips all sends
    # for this rule regardless of event/camera. Cleared by ops or by
    # the user from the rule builder. Mute and snooze are deliberately
    # separate. snooze is rule-wide, mute is per-event. Snooze wins.
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # Footage covering the observation, resolved at fire time by camera +
    # timestamp. Lets an alert consumer jump straight to the clip.
    recording_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_status: Mapped[str] = mapped_column(String(16), default="pending")
    action_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Phase 2 ack triad. Set in lockstep by either the Telegram callback
    # handler or POST /api/events/{id}/ack. ``acked_via`` is one of
    # ``telegram``, ``web``, or ``api``. Pre-existing ``acknowledged_at``
    # is left in place for back-compat with old admin tooling but new
    # code should read ``acked_at``.
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acked_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Per-event mute. Set by the "Mute 10 min" Telegram button. While
    # now() < muted_until, downstream Telegram re-sends for the
    # rule+camera combo of this event are skipped. Snooze wins over
    # mute because snooze is rule-wide.
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    role: Mapped[str] = mapped_column(String(50), default="viewer")  # admin, viewer, guardian
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Auto-created first-run owner that has not set real credentials yet.
    # The app drops a new user straight in via /auth/bootstrap, then nags
    # them to claim the account (set email + password) which clears this.
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    # Native-audio analysis (docs/native-audio-conversation-design.md).
    # Populated only when a supports_audio provider analyzes the conversation
    # clip, capturing what the audio reveals beyond the transcript text.
    # Distinct from summary_text so provenance stays clear.
    audio_speaker_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_tone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    audio_non_verbal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    audio_gist: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_analyzed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


class DailyDigest(Base):
    """Household-wide morning summary. One row per generation run.

    Aggregates the last 24h across observations, incidents,
    journeys, audio detections, and conversations. Free-form
    summary_text plus a structured ``facts`` dict so the UI can
    render bullet lists without re-parsing the LLM output.
    """

    __tablename__ = "daily_digests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)


class PrivacyZone(Base):
    """Per-camera private region. Polygon stored in normalized 0-1
    coordinates so the same zone applies across resolution changes.

    ``source`` distinguishes ``auto`` (AI-proposed) from ``manual``
    (user-drawn). ``locked`` makes the zone immune to the auto
    refresh path so the user can pin a tight bathroom door bbox
    without the detector overwriting it on the next frame.
    """

    __tablename__ = "privacy_zones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    polygon: Mapped[dict] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    auto_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # PTZ pose at detection time. {pan, tilt, zoom} or null. Cameras
    # without PTZ leave this null and rely on freshness alone.
    ptz_pose: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Freshness gate. Auto zones not re-detected within this window
    # stop applying so a camera that panned away does not keep blurring
    # the wrong region. Manual and locked zones ignore it.
    stale_after_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)


class TelegramChannel(Base):
    """User-owned Telegram bot channel for rule notifications.

    The bot token is stored Fernet-encrypted in ``bot_token_enc``. The
    target chat is established via the pairing flow. ``chat_id`` stays
    null until the user invokes ``/start <nonce>`` (DM) or
    ``/pair <nonce>`` (group) and the long-poller binds the chat. Once
    paired, the channel can be used as the target of a ``telegram``
    rule action.
    """

    __tablename__ = "telegram_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    bot_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    bot_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    default_silent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Phase 3. delivery + rate limit + dedupe knobs.
    # delivery_mode is "long_poll" or "webhook". When "webhook" the
    # poller manager skips this channel and updates arrive via
    # POST /api/telegram/webhook/{channel_id}.
    delivery_mode: Mapped[str] = mapped_column(String(16), default="long_poll", nullable=False)
    # webhook_secret is a hex random shared secret. Telegram echoes it
    # in the X-Telegram-Bot-Api-Secret-Token header on every delivery
    # so we can reject forged updates. Functions like a per-channel
    # API key. never returned in API responses after creation.
    webhook_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Cached for display + setWebhook idempotency.
    webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # off | low (re-encode to 720p JPEG q70) | high (original bytes).
    media_quality: Mapped[str] = mapped_column(String(16), default="high", nullable=False)
    rate_limit_per_chat_qps: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    rate_limit_per_chat_burst: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    dedupe_window_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # Phase 4. Household sharing. When true, every user can pick this
    # channel in their rule builder + receive alerts on it. Ownership
    # (token, delete, edit token) stays with user_id. share_permissions
    # is 'use' (others can pick it) or 'use_and_test' (others can also
    # fire the test endpoint).
    shared_with_household: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    share_permissions: Mapped[str] = mapped_column(String(16), default="use", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TelegramOutboxDedupe(Base):
    """Short-lived ledger of outbound message hashes.

    Used by :class:`services.notify.telegram.DedupeStore` to suppress
    sending the same content to the same chat repeatedly within a
    user-configurable window. Rows older than an hour are pruned
    opportunistically on insert. an explicit retention worker is not
    needed because traffic naturally garbage-collects.
    """

    __tablename__ = "telegram_outbox_dedupe"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EventNote(Base):
    """Free-text annotation on an Event.

    Phase 4. Replying to a Telegram alert lands here with
    ``source='telegram'``. The web UI also exposes POST + DELETE on
    these rows under /api/events/{id}/notes. Deleting is a hard delete;
    notes are cheap and the source row's history (Event) is preserved.
    """

    __tablename__ = "event_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # telegram | web | api
    text: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramDialog(Base):
    """Multi-step in-chat state machine state.

    Phase 4. Drives face/body cluster naming over Telegram and the
    optional ask-yes-no prompt. One open dialog per (channel_id,
    chat_id, awaiting) is the contract enforced by the lookup index.
    ``context`` carries the kind-specific payload (cluster_id,
    event_id, etc.). ``expires_at`` is bumped on each user reply and
    a stale dialog is treated as terminal.
    """

    __tablename__ = "telegram_dialogs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("telegram_channels.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    awaiting: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ── Agent v1 (Wave 1A) ──────────────────────────────────────────────
#
# Lifecycle + audit + cache tables for the agentic Q&A layer
# (docs/agent-design.md sections 5.4, 7, 11.1). Wave 1B (tool registry)
# writes AgentToolCall rows. Wave 1C (analyzer) writes AgentVlmCall +
# VlmFrameAnalysis. The driver in services/agent/runs.py owns row
# creation; see services/agent/budget.py for the daily-usage rollup.


class AgentRun(Base):
    """One agentic Q&A invocation.

    Started when a user submits a question. The driver appends tool
    calls + vlm calls during the run loop, updates the rollup
    counters as turns complete, and stamps a terminal ``status`` plus
    ``ended_at`` when the loop exits. ``parent_run_id`` is set on
    multi-turn follow-ups so the audit page can group a conversation.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    # status in {running, completed, failed, cancelled, budget_exhausted}
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turns_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentToolCall(Base):
    """One tool invocation inside an AgentRun.

    ``turn_index`` is monotonically increasing per run; multiple tool
    calls inside the same agent turn share the same index. Written by
    Wave 1B's tool registry via ``services.agent.runs.append_tool_call``.
    """

    __tablename__ = "agent_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentVlmCall(Base):
    """One VLM analyzer invocation inside an AgentRun.

    Cheap row even on a cache hit (``cached=true``, ``tokens_*=0``,
    ``cost_cents=0``) so the audit trail always shows what the agent
    looked at. Wave 1C's analyzer writes this via
    ``services.agent.runs.record_vlm_call``.
    """

    __tablename__ = "agent_vlm_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tool_calls.id", ondelete="CASCADE"), nullable=True
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # target_kind in {frame, clip}
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("observations.id", ondelete="SET NULL"), nullable=True
    )
    recording_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recordings.id", ondelete="SET NULL"), nullable=True
    )
    time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    thumbnails_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VlmFrameAnalysis(Base):
    """Eternal cache row keyed by (frame target, question, model).

    Section 5.4. Lifetime tied to the underlying media via FK CASCADE.
    Exactly one of (observation_id, recording_id) must be non-null
    (CHECK constraint). Partial unique indexes enforce one row per
    target+question+provider+model. Written by Wave 1C's analyzer.
    """

    __tablename__ = "vlm_frame_analysis"
    __table_args__ = (
        CheckConstraint(
            "(observation_id IS NOT NULL) OR (recording_id IS NOT NULL)",
            name="ck_vlm_frame_analysis_target_present",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("observations.id", ondelete="CASCADE"), nullable=True
    )
    recording_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recordings.id", ondelete="CASCADE"), nullable=True
    )
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentDailyUsage(Base):
    """Per-user per-day rollup used by ``services.agent.budget``.

    UPSERT-ed on every recorded usage event. Cheap-to-query so the
    pre-run budget check is fast. Older rows are kept for the audit
    page; no auto-prune in v1.
    """

    __tablename__ = "agent_daily_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_agent_daily_usage_user_day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usage_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ApiKey(Base):
    """Long-lived machine credential for programmatic API access.

    The plaintext key (``nrb_<random>``) is shown once at creation and
    never stored. We persist a sha256 hash for constant-time lookup and
    a short prefix for display. Keys are scoped (read / write) and can
    carry an optional expiry; revocation stamps ``revoked_at``.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # sha256 hex of the full plaintext key. Indexed for O(1) auth lookup.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # First chars of the key (e.g. "nrb_ab12cd") for UI display only.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # Advisory scope. "read" or "write". write implies read.
    scope: Mapped[str] = mapped_column(String(16), default="read", nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookSubscription(Base):
    """Standing outbound webhook. Every fired Event is fanned out to all
    active subscriptions, in addition to per-rule webhook actions.

    Optional ``rule_ids`` / ``camera_ids`` JSON lists scope which events
    a subscription receives. ``secret`` enables HMAC body signing.
    """

    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # JSON filter lists. NULL/empty means "all".
    rule_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    camera_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────────────────
# Guardian by Nurby (docs/guardian-portal-product-brief.md section 24).
# A thin permission-and-view layer over the existing engine. These rows bind
# an existing User (role "guardian") to an existing Person, attach
# entitlements + alert prefs, register approved pickups, and log every view.
# No detection/identity/AI logic is forked here; presence, alerts, recaps,
# and search all delegate to existing Nurby subsystems.
# ─────────────────────────────────────────────────────────────────────────


class Facility(Base):
    """The operator that owns cameras and grants guardian access.

    For a single-household self-host deploy, one default Facility is
    auto-created and every Person/Camera implicitly belongs to it. The model
    exists so the daycare/multi-tenant story is a config change, not a
    schema migration.
    """

    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)  # IANA, null = system default
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Facility-level overrides of the system guardian settings. Null = inherit.
    reveal_min_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_cameras_per_person: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardianLink(Base):
    """Binding of a guardian (User) to a dependant (Person). The spine.

    The privacy guarantee rests on this row. The facility grants and revokes;
    the guardian never self-grants. Each link is independently tiered,
    entitled, alert-configured, expirable, and revocable.
    """

    __tablename__ = "guardian_links"
    __table_args__ = (
        UniqueConstraint("guardian_user_id", "person_id", name="uq_guardian_person"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guardian_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_label: Mapped[str | None] = mapped_column(String(64), nullable=True)  # mother, father, grandparent, carer
    # full | summary | alerts_only  (see brief section 11)
    tier: Mapped[str] = mapped_column(String(16), default="full", nullable=False)
    # Per-link alert opt-ins within the facility-allowed set. booleans keyed by
    # alert kind: arrived, departed, picked_up, entered_zone, left_zone, not_seen.
    alert_prefs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Channels this guardian receives alerts on. booleans keyed by
    # channel: telegram, email, in_app. Null = all available channels.
    notify_channels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Entitlement flags (brief section 24.11). No billing yet; an admin toggles
    # these and they gate exactly as paid features will.
    premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # recap + smart search
    live_presence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # removes 30-min delay
    live_video: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # blurred live clips, lifts image cap
    audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # audio-derived signals
    # At least one primary parent paid unlocks free extra guardians on this person.
    is_primary_parent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Stricter-only per-link reveal override. May raise the floor, never lower it.
    reveal_min_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last time an image was served to this guardian, for the 1/hour free throttle.
    last_image_served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovedPickup(Base):
    """A person or vehicle approved to pick up a dependant.

    Verified pickup checks a departure event against this registry. A match
    yields "picked up by X"; a non-match yields a yellow "unrecognized pickup".
    """

    __tablename__ = "approved_pickups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="person", nullable=False)  # person | vehicle
    # When the approved pickup is a known Person in the system, link it so the
    # face engine confirms identity. Null for free-text/vehicle entries.
    linked_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    vehicle_plate: Mapped[str | None] = mapped_column(String(32), nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardianEvent(Base):
    """A guardian-facing event about a dependant (arrived, departed, picked_up,
    entered/left zone). Persisted when the fan-out fires so the panel can show a
    real day-timeline and a pickup-moment card, not just raw sightings.

    Keyed by person (the event is about the dependant); each of that person's
    guardians sees it filtered by their own delay/prefs at read time.
    """

    __tablename__ = "guardian_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # arrived | departed | picked_up | entered_zone | left_zone | not_seen
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    zone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # For pickups: was the escort on the approved list, and who.
    pickup_matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pickup_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class GuardianAccessLog(Base):
    """Append-only audit of every guardian view. Visible to the facility.

    Transparency is a feature (brief section 12). Every status check, image
    fetch, timeline view, live session, recap, and search is logged.
    """

    __tablename__ = "guardian_access_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guardian_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guardian_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guardian_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # status | image | timeline | live | recap | search | alerts_change
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
