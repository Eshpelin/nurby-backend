import asyncio
import glob
import platform
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
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
from shared.schemas import CameraCreate, CameraResponse, CameraStatusLogResponse, CameraUpdate

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
    result = await db.execute(select(Camera).order_by(Camera.created_at))
    return [_camera_to_response(c) for c in result.scalars().all()]


@router.post("", status_code=201)
async def create_camera(body: CameraCreate, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    camera = Camera(**body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    return _camera_to_response(camera)


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


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, _current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    await db.delete(camera)
    await db.commit()
