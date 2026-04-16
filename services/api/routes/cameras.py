import asyncio
import glob
import platform
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.discovery.onvif import discover_onvif_cameras
from shared.database import get_db
from shared.models import Camera, CameraStatusLog
from shared.schemas import CameraCreate, CameraResponse, CameraStatusLogResponse, CameraUpdate

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
async def discover_devices():
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
):
    """Fetch camera online/offline status change history."""
    query = select(CameraStatusLog).order_by(CameraStatusLog.timestamp.desc()).limit(limit)
    if camera_id:
        query = query.where(CameraStatusLog.camera_id == camera_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("", response_model=list[CameraResponse])
async def list_cameras(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).order_by(Camera.created_at))
    return result.scalars().all()


@router.post("", response_model=CameraResponse, status_code=201)
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    camera = Camera(**body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.patch("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: uuid.UUID, body: CameraUpdate, db: AsyncSession = Depends(get_db)
):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(camera, field, value)

    await db.commit()
    await db.refresh(camera)
    return camera


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    await db.delete(camera)
    await db.commit()
