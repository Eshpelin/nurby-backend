import asyncio
import glob
import logging
import platform
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.discovery.onvif import (
    discover_onvif_cameras,
    ptz_continuous_move,
    ptz_get_presets,
    ptz_goto_preset,
    ptz_stop,
)
from shared.auth import get_current_user, require_admin
from shared.config import settings
from shared.database import get_db
from shared.models import Camera, CameraStatusLog, User
from shared.schemas import (
    CameraCreate,
    CameraReorderItem,
    CameraStatusLogResponse,
    CameraUpdate,
)

logger = logging.getLogger("nurby.api.cameras")

# Redis key prefix for signaling stream restart to manager
RESTART_KEY_PREFIX = "nurby:stream_restart:"


def _camera_to_response(camera: Camera) -> dict:
    """Convert Camera model to response dict, masking credentials."""
    data = {c.name: getattr(camera, c.name) for c in Camera.__table__.columns}
    data["has_credentials"] = bool(camera.username or camera.auth_token)
    # Never expose raw credentials in API responses
    data.pop("username", None)
    data.pop("password", None)
    data.pop("auth_token", None)
    return data

router = APIRouter()

_device_probe_pool = ThreadPoolExecutor(max_workers=4)


class DiscoveredDevice(BaseModel):
    index: int
    path: str
    name: str
    resolution: str


def _probe_device(index: int, path: str) -> DiscoveredDevice | None:
    """Try opening a single video device and grab a test frame.

    Returns a DiscoveredDevice if the device works, None otherwise.
    This runs in a thread because cv2.VideoCapture blocks.
    """
    import cv2  # noqa: import here so the module loads only when needed

    cap = None
    try:
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            return None

        # Grab a test frame to confirm the device actually works
        ret, _ = cap.read()
        if not ret:
            return None

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        resolution = f"{width}x{height}" if width and height else "unknown"

        # Try to get a backend name for friendlier display
        backend = cap.getBackendName() if hasattr(cap, "getBackendName") else ""
        device_name = f"Camera {index}"
        if backend:
            device_name = f"Camera {index} ({backend})"

        return DiscoveredDevice(
            index=index,
            path=path,
            name=device_name,
            resolution=resolution,
        )
    except Exception:
        return None
    finally:
        if cap is not None:
            cap.release()


async def _probe_with_timeout(index: int, path: str, timeout: float = 2.0) -> DiscoveredDevice | None:
    """Run the blocking probe in a thread pool with a timeout."""
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_device_probe_pool, _probe_device, index, path),
            timeout=timeout,
        )
        return result
    except (asyncio.TimeoutError, Exception):
        return None


@router.get("/devices", response_model=list[DiscoveredDevice])
async def discover_devices(_current_user: User = Depends(get_current_user)):
    """Scan for available USB and local video capture devices.

    Probes indices 0 through 9. On Linux, also scans /dev/video* paths.
    Each probe runs in a thread pool with a 2-second timeout to avoid
    hanging on unresponsive devices.
    """
    candidates: list[tuple[int, str]] = []

    if platform.system() == "Linux":
        video_paths = sorted(glob.glob("/dev/video*"))
        seen_indices: set[int] = set()
        for vp in video_paths:
            # Extract the numeric index from /dev/videoN
            suffix = vp.replace("/dev/video", "")
            try:
                idx = int(suffix)
                candidates.append((idx, vp))
                seen_indices.add(idx)
            except ValueError:
                continue
        # Fill in any indices 0-9 not already covered
        for i in range(10):
            if i not in seen_indices:
                candidates.append((i, str(i)))
    else:
        # macOS / Windows. Just probe indices 0-9
        candidates = [(i, str(i)) for i in range(10)]

    tasks = [_probe_with_timeout(idx, path) for idx, path in candidates]
    results = await asyncio.gather(*tasks)

    devices = [d for d in results if d is not None]
    return devices


class DiscoveredOnvifDevice(BaseModel):
    ip: str
    port: int
    name: str
    manufacturer: str
    model: str
    firmware: str | None
    onvif_url: str
    stream_url: str | None
    profiles: list[str]
    auth_required: bool
    resolution: str | None
    already_added: bool = False


@router.get("/discover", response_model=list[DiscoveredOnvifDevice])
async def discover_onvif(
    timeout: int = Query(default=5, ge=1, le=15),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Scan the local network for ONVIF-compatible IP cameras.

    Uses WS-Discovery multicast to find devices, then queries each
    one for manufacturer info, stream profiles, and RTSP URLs.
    """
    try:
        devices = await discover_onvif_cameras(timeout=float(timeout))
    except Exception:
        # Discovery should never crash the server
        devices = []

    if not devices:
        return []

    # Cross-reference discovered IPs and stream URLs against existing cameras
    result = await db.execute(select(Camera))
    existing_cameras = result.scalars().all()

    existing_ips: set[str] = set()
    existing_urls: set[str] = set()
    for cam in existing_cameras:
        existing_urls.add(cam.stream_url.lower())
        # Extract IP from existing stream URLs
        try:
            from urllib.parse import urlparse as _urlparse

            parsed = _urlparse(cam.stream_url)
            if parsed.hostname:
                existing_ips.add(parsed.hostname)
        except Exception:
            pass

    response: list[DiscoveredOnvifDevice] = []
    for dev in devices:
        already_added = False
        if dev.get("ip") in existing_ips:
            already_added = True
        if dev.get("stream_url") and dev["stream_url"].lower() in existing_urls:
            already_added = True

        response.append(
            DiscoveredOnvifDevice(
                ip=dev.get("ip", ""),
                port=dev.get("port", 80),
                name=dev.get("name", "Unknown"),
                manufacturer=dev.get("manufacturer", "Unknown"),
                model=dev.get("model", "Unknown"),
                firmware=dev.get("firmware"),
                onvif_url=dev.get("onvif_url", ""),
                stream_url=dev.get("stream_url"),
                profiles=dev.get("profiles", []),
                auth_required=dev.get("auth_required", False),
                resolution=dev.get("resolution"),
                already_added=already_added,
            )
        )

    return response


@router.get("/status-logs", response_model=list[CameraStatusLogResponse])
async def list_status_logs(
    camera_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Fetch camera online/offline status change history."""
    query = select(CameraStatusLog).order_by(CameraStatusLog.timestamp.desc()).limit(limit)
    if camera_id:
        query = query.where(CameraStatusLog.camera_id == camera_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("")
async def list_cameras(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Camera).order_by(Camera.display_order, Camera.created_at)
    )
    return [_camera_to_response(c) for c in result.scalars().all()]


@router.get("/{camera_id}/actions")
async def camera_action_timeline(
    camera_id: uuid.UUID,
    hours: int = Query(default=24, ge=1, le=168),
    action: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=500, ge=1, le=2000),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Operator-facing HAR activity timeline for a camera: merged per-person action segments
    over the last ``hours``. Empty until HAR is enabled. Guardian families use the
    delay/consent/reveal-gated /guardian endpoints instead, not this one."""
    from datetime import timedelta

    from services.perception.har_segments import camera_segments

    cam = await db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = await camera_segments(db, camera_id, since=since, action=action, limit=limit)
    return {"items": items, "count": len(items), "hours": hours}


@router.post("", status_code=201)
async def create_camera(body: CameraCreate, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    payload = body.model_dump()
    # New cameras land at the end of the ordering.
    max_result = await db.execute(select(Camera.display_order))
    orders = [o for (o,) in max_result.all()]
    payload["display_order"] = (max(orders) + 1) if orders else 0
    camera = Camera(**payload)
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    # Register a MediaMTX path so ingestion pulls from the mux instead of
    # the camera directly. Handles USB push bridges, RTSP/HLS pull-source
    # registration, and no-ops for non-mux types. Best-effort. the camera
    # manager's periodic sync will retry on failure.
    try:
        from services.ingestion.mediamtx_mux import mux_manager
        await mux_manager.ensure(camera)
    except Exception:
        pass
    return _camera_to_response(camera)


@router.post("/demo", status_code=201)
async def create_demo_camera(_current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Create a ready-to-use demo camera that streams looping sample
    footage, so a new user can try Nurby without owning a camera. The
    source URL is configurable via NURBY_DEMO_VIDEO_URL."""
    import os

    demo_url = os.environ.get(
        "NURBY_DEMO_VIDEO_URL",
        "https://nurby.ai/static/SecurityCamCompilation.mp4",
    )
    # Idempotent. If a demo camera already exists (magic run twice, or the
    # dashboard demo button clicked again), reuse it instead of stacking
    # duplicate rows.
    existing = await db.execute(
        select(Camera).where(
            Camera.stream_type == "file", Camera.stream_url == demo_url
        )
    )
    found = existing.scalars().first()
    if found is not None:
        return _camera_to_response(found)

    max_result = await db.execute(select(Camera.display_order))
    orders = [o for (o,) in max_result.all()]
    camera = Camera(
        name="Demo Camera",
        stream_url=demo_url,
        stream_type="file",
        location_label="Demo footage",
        scene_mode="outdoor",
        detect_objects=True,
        detect_faces=True,
        recording_enabled=False,
        # recording_mode is what the ingestion worker actually honors
        # (recording_enabled is deprecated). Without this the demo would
        # record its looping feed to disk forever and fill the volume.
        recording_mode="off",
        display_order=(max(orders) + 1) if orders else 0,
    )
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    try:
        from services.ingestion.mediamtx_mux import mux_manager
        await mux_manager.ensure(camera)
    except Exception:
        pass
    return _camera_to_response(camera)


@router.post("/reorder", status_code=200)
async def reorder_cameras(
    items: list[CameraReorderItem],
    _current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Persist a new ordering for the camera sidebar. Items that are not
    passed keep their existing display_order."""
    if not items:
        return {"updated": 0}
    id_to_order = {str(i.id): i.display_order for i in items}
    result = await db.execute(select(Camera).where(Camera.id.in_(list(id_to_order.keys()))))
    cams = result.scalars().all()
    for c in cams:
        c.display_order = id_to_order[str(c.id)]
    await db.commit()
    return {"updated": len(cams)}


# ---------------------------------------------------------------------------
# Test connection endpoint (must be before /{camera_id} catch-all)
# ---------------------------------------------------------------------------


class TestConnectionRequest(BaseModel):
    stream_url: str
    stream_type: str = "rtsp"
    username: str | None = None
    password: str | None = None
    auth_token: str | None = None


def _test_stream_connection(url: str, stream_type: str) -> dict:
    """Probe a camera stream. Runs in thread pool. Returns status dict."""
    import cv2

    if stream_type == "usb":
        try:
            source = int(url)
        except ValueError:
            source = url
    elif stream_type == "hls":
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return {"ok": False, "error": "Failed to open HLS stream"}
        ret, _ = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        cap.release()
        if not ret:
            return {"ok": False, "error": "Connected but failed to read frame"}
        return {"ok": True, "width": w, "height": h, "fps": round(fps, 1)}
    else:
        source = url

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        return {"ok": False, "error": "Failed to open stream. Check URL and credentials."}
    ret, _ = cap.read()
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    cap.release()
    if not ret:
        return {"ok": False, "error": "Connected but failed to read frame"}
    return {"ok": True, "width": w, "height": h, "fps": round(fps, 1)}


@router.post("/test-connection")
async def test_camera_connection(
    body: TestConnectionRequest,
    _current_user: User = Depends(get_current_user),
):
    """Test camera connectivity before creating. Returns resolution and FPS on success."""
    from services.ingestion.stream import build_auth_url

    # Build authed URL
    if body.stream_type == "http_snapshot":
        # Snapshot streams use httpx, not OpenCV
        import httpx
        headers = {}
        auth = None
        if body.auth_token:
            headers["Authorization"] = f"Bearer {body.auth_token}"
        elif body.username:
            auth = httpx.BasicAuth(body.username, body.password or "")
        try:
            async with httpx.AsyncClient(timeout=10, auth=auth, headers=headers) as client:
                resp = await client.get(body.stream_url)
                resp.raise_for_status()
                img_array = np.frombuffer(resp.content, dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                if frame is None:
                    return {"ok": False, "error": "Got response but could not decode as image"}
                h, w = frame.shape[:2]
                return {"ok": True, "width": w, "height": h, "fps": round(1.0 / 2.0, 1)}
        except httpx.TimeoutException:
            return {"ok": False, "error": "Connection timed out after 10 seconds"}
        except httpx.HTTPStatusError as exc:
            return {"ok": False, "error": f"HTTP {exc.response.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    url = build_auth_url(body.stream_url, body.username, body.password)
    if body.auth_token and body.stream_type in ("http_mjpeg", "hls"):
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}token={body.auth_token}"

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_device_probe_pool, _test_stream_connection, url, body.stream_type),
            timeout=15.0,
        )
        return result
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Connection timed out after 15 seconds"}


# ---------------------------------------------------------------------------
# PTZ control endpoints (must be registered before /{camera_id} catch-all)
# ---------------------------------------------------------------------------


class PTZMoveRequest(BaseModel):
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0


class PTZGotoRequest(BaseModel):
    preset_token: str


class PTZPresetResponse(BaseModel):
    token: str
    name: str


def _extract_ip_port(stream_url: str) -> tuple[str, int]:
    """Pull the host and port from a camera stream URL."""
    parsed = urlparse(stream_url)
    ip = parsed.hostname or ""
    port = parsed.port or 80
    return ip, port


async def _get_camera_for_ptz(
    camera_id: uuid.UUID, db: AsyncSession
) -> Camera:
    """Load a camera by ID and verify it supports PTZ (RTSP only)."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.stream_type != "rtsp":
        raise HTTPException(status_code=400, detail="PTZ is only supported for RTSP cameras")
    return camera


@router.post("/{camera_id}/ptz/move")
async def ptz_move(
    camera_id: uuid.UUID,
    body: PTZMoveRequest,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Start continuous PTZ movement on a camera."""
    camera = await _get_camera_for_ptz(camera_id, db)
    ip, port = _extract_ip_port(camera.stream_url)
    profile_token = "Profile_1"

    ok = await ptz_continuous_move(
        ip=ip,
        port=port,
        username=camera.username,
        password=camera.password,
        profile_token=profile_token,
        pan_speed=body.pan,
        tilt_speed=body.tilt,
        zoom_speed=body.zoom,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Camera did not accept the PTZ move command")
    return {"status": "moving"}


@router.post("/{camera_id}/ptz/stop")
async def ptz_stop_movement(
    camera_id: uuid.UUID,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Stop all PTZ movement on a camera."""
    camera = await _get_camera_for_ptz(camera_id, db)
    ip, port = _extract_ip_port(camera.stream_url)
    profile_token = "Profile_1"

    ok = await ptz_stop(
        ip=ip,
        port=port,
        username=camera.username,
        password=camera.password,
        profile_token=profile_token,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Camera did not accept the PTZ stop command")
    return {"status": "stopped"}


@router.get("/{camera_id}/ptz/presets", response_model=list[PTZPresetResponse])
async def ptz_list_presets(
    camera_id: uuid.UUID,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """List saved PTZ presets for a camera."""
    camera = await _get_camera_for_ptz(camera_id, db)
    ip, port = _extract_ip_port(camera.stream_url)
    profile_token = "Profile_1"

    presets = await ptz_get_presets(
        ip=ip,
        port=port,
        username=camera.username,
        password=camera.password,
        profile_token=profile_token,
    )
    return presets


@router.post("/{camera_id}/ptz/goto")
async def ptz_goto(
    camera_id: uuid.UUID,
    body: PTZGotoRequest,
    _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Move the camera to a saved preset position."""
    camera = await _get_camera_for_ptz(camera_id, db)
    ip, port = _extract_ip_port(camera.stream_url)
    profile_token = "Profile_1"

    ok = await ptz_goto_preset(
        ip=ip,
        port=port,
        username=camera.username,
        password=camera.password,
        profile_token=profile_token,
        preset_token=body.preset_token,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Camera did not accept the PTZ goto command")
    return {"status": "moving_to_preset"}


@router.get("/{camera_id}")
async def get_camera(camera_id: uuid.UUID, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    # Include latest status reason from status log
    latest_log = await db.execute(
        select(CameraStatusLog)
        .where(CameraStatusLog.camera_id == camera_id)
        .order_by(CameraStatusLog.timestamp.desc())
        .limit(1)
    )
    log_entry = latest_log.scalar_one_or_none()
    resp = _camera_to_response(camera)
    resp["status_reason"] = log_entry.reason if log_entry else None
    return resp


@router.patch("/{camera_id}")
async def update_camera(
    camera_id: uuid.UUID, body: CameraUpdate, _current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    updates = body.model_dump(exclude_unset=True)

    # Track if stream-affecting fields changed
    stream_fields = {"stream_url", "stream_type", "username", "password", "auth_token", "snapshot_interval"}
    stream_changed = any(
        field in updates and getattr(camera, field) != value
        for field, value in updates.items()
        if field in stream_fields
    )

    for field, value in updates.items():
        setattr(camera, field, value)

    await db.commit()
    await db.refresh(camera)

    # Signal stream restart if connection params changed
    if stream_changed:
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(settings.redis_url)
            await r.setex(f"{RESTART_KEY_PREFIX}{camera_id}", 30, "1")
            await r.aclose()
        except Exception:
            pass  # best-effort signal

    return _camera_to_response(camera)


# --- Browser webcam frame upload -----------------------------------------
#
# For stream_type == "webcam", the browser (same machine, same browser
# session as the dashboard) captures the local camera via getUserMedia and
# POSTs a JPEG here on a short interval. No MediaMTX, no RTSP, no ingestion
# worker. This route is the sole entry point.
#
# Latest frame is cached in Redis under a small per-camera key so the
# dashboard can read it back as a preview. Every frame is also pushed onto
# the same nurby:motion Redis stream the ingestion service uses, so the
# perception pipeline (VLM, YOLO, rules) treats webcam frames identically
# to any other source.

WEBCAM_FRAME_KEY_PREFIX = "nurby:webcam_frame:"
WEBCAM_FRAME_TTL = 15  # seconds. tile goes "offline" if publisher stops

# Rate-limit motion stream emission per camera. Frames keep arriving at 1 Hz
# for the live preview, but we only hand one off to perception every N
# seconds so observations / VLM calls stay meaningful. Cadence comes from
# Camera.snapshot_interval so users can tune it per camera. If the camera
# still has the model default (2s), we bump to 10s for webcams.
WEBCAM_MOTION_COOLDOWN_KEY = "nurby:webcam_motion_cooldown:"
WEBCAM_MOTION_COOLDOWN_DEFAULT_S = 10


def _webcam_motion_cooldown(camera: Camera) -> int:
    """Seconds between motion-stream emissions for a webcam."""
    interval = float(getattr(camera, "snapshot_interval", 0) or 0)
    # Treat the generic 2.0 model default as "unset" for webcams.
    if interval <= 2.0:
        return WEBCAM_MOTION_COOLDOWN_DEFAULT_S
    return max(int(round(interval)), 2)

MOTION_STREAM_KEY = "nurby:motion"
MOTION_STREAM_MAXLEN = 1000

# Fast lane for live detection overlay. Every frame goes here. YOLO-only
# consumer runs inline and caches detections for the dashboard overlay.
LIVE_MOTION_STREAM_KEY = "nurby:live_motion"
LIVE_MOTION_STREAM_MAXLEN = 50
LIVE_DET_CACHE_PREFIX = "nurby:live_det:"


@router.post("/{camera_id}/frame", status_code=204)
async def upload_webcam_frame(
    camera_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accept a single JPEG frame from the browser webcam publisher.

    Body is the raw JPEG bytes. Content-Type should be image/jpeg.
    """
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.stream_type != "webcam":
        raise HTTPException(status_code=400, detail="Camera is not a webcam")

    body = await request.body()
    if not body or len(body) < 64:
        raise HTTPException(status_code=400, detail="Empty or tiny frame")
    if len(body) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Frame too large")

    # Decode once to confirm it's a valid JPEG and read dimensions. We
    # don't need the pixel array beyond that. Cheap sanity check.
    arr = np.frombuffer(body, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Could not decode frame")
    height, width = img.shape[:2]

    # Update camera dimensions once if they changed.
    if camera.width != width or camera.height != height:
        camera.width = width
        camera.height = height

    # Flip status to live/recording if we weren't already.
    desired_status = (
        "recording"
        if (camera.recording_enabled and getattr(camera, "recording_mode", "always") == "always")
        else "live"
    )
    if camera.status != desired_status:
        previous = camera.status
        camera.status = desired_status
        db.add(
            CameraStatusLog(
                camera_id=camera_id,
                status=desired_status,
                previous_status=previous,
                reason="webcam frame received",
            )
        )
    await db.commit()

    # Stash to Redis. latest frame cache + motion stream entry.
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        try:
            await r.set(
                f"{WEBCAM_FRAME_KEY_PREFIX}{camera_id}",
                body,
                ex=WEBCAM_FRAME_TTL,
            )
            # Fast lane. every frame, no cadence gate. Small stream.
            await r.xadd(
                LIVE_MOTION_STREAM_KEY,
                {
                    "camera_id": str(camera_id),
                    "frame": body,
                },
                maxlen=LIVE_MOTION_STREAM_MAXLEN,
                approximate=True,
            )

            cooldown_key = f"{WEBCAM_MOTION_COOLDOWN_KEY}{camera_id}"
            # SET NX with TTL acts as a per-camera rate gate. If the key
            # already exists we skip this frame for perception but keep
            # the cached preview fresh.
            gate_ok = await r.set(
                cooldown_key, "1", nx=True, ex=_webcam_motion_cooldown(camera)
            )
            if gate_ok:
                await r.xadd(
                    MOTION_STREAM_KEY,
                    {
                        "camera_id": str(camera_id),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "motion_score": "1.0",
                        "frame": body,
                    },
                    maxlen=MOTION_STREAM_MAXLEN,
                    approximate=True,
                )
        finally:
            await r.aclose()
    except Exception:
        logger.exception("failed to publish webcam frame for %s", camera_id)

    return None


@router.get("/{camera_id}/frame")
async def latest_webcam_frame(
    camera_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the last JPEG frame uploaded for this webcam.

    Used as a fallback preview. The dashboard usually renders the live
    MediaStream directly in the browser tab that owns the camera, but
    other tabs/devices can poll this endpoint.
    """
    from fastapi.responses import Response

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.stream_type != "webcam":
        raise HTTPException(status_code=400, detail="Camera is not a webcam")

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        try:
            data = await r.get(f"{WEBCAM_FRAME_KEY_PREFIX}{camera_id}")
        finally:
            await r.aclose()
    except Exception:
        data = None

    if not data:
        raise HTTPException(status_code=404, detail="No recent frame")
    return Response(content=data, media_type="image/jpeg")


@router.get("/{camera_id}/live-detections")
async def live_detections(
    camera_id: uuid.UUID,
    _current_user: User = Depends(get_current_user),
):
    """Return the latest cached fast-lane YOLO detections for overlay.

    The live detector writes a fresh entry per frame with a short TTL,
    so callers polling a few times a second see near-realtime boxes
    that track movement between full observation cadences.
    """
    import json as _json

    import redis.asyncio as aioredis

    try:
        r = aioredis.from_url(settings.redis_url)
        try:
            data = await r.get(f"{LIVE_DET_CACHE_PREFIX}{camera_id}")
        finally:
            await r.aclose()
    except Exception:
        data = None

    if not data:
        return {"camera_id": str(camera_id), "detections": [], "width": 0, "height": 0}
    try:
        return _json.loads(data)
    except Exception:
        return {"camera_id": str(camera_id), "detections": [], "width": 0, "height": 0}


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    await db.delete(camera)
    await db.commit()
    try:
        from services.ingestion.mediamtx_mux import mux_manager
        await mux_manager.remove(camera_id)
    except Exception:
        pass
