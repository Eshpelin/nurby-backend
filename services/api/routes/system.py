import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user, require_admin
from shared.config import settings
from shared.database import get_db
from shared.email import send_email
from shared.models import Camera, Observation, Recording, User
from shared.schemas import CameraStorageStats, StorageResponse, SystemStatus
from services.perception.vlm_queue import get_vlm_stats

router = APIRouter()


@router.get("/status", response_model=SystemStatus)
async def get_system_status(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from services.api.main import START_TIME

    total = await db.scalar(select(func.count()).select_from(Camera))
    online = await db.scalar(
        select(func.count()).select_from(Camera).where(Camera.status != "offline")
    )
    recording = await db.scalar(
        select(func.count()).select_from(Camera).where(Camera.status == "recording")
    )

    return SystemStatus(
        version="0.1.0",
        cameras_total=total or 0,
        cameras_online=online or 0,
        cameras_recording=recording or 0,
        uptime_seconds=time.time() - START_TIME,
    )


@router.get("/storage", response_model=StorageResponse)
async def get_storage_stats(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    cameras_result = await db.execute(select(Camera))
    cameras = cameras_result.scalars().all()

    rec_stats = await db.execute(
        select(
            Recording.camera_id,
            func.count(Recording.id).label("recording_count"),
            func.coalesce(func.sum(Recording.file_size_bytes), 0).label("recording_bytes"),
        ).group_by(Recording.camera_id)
    )
    rec_by_camera = {row.camera_id: row for row in rec_stats.all()}

    obs_stats = await db.execute(
        select(
            Observation.camera_id,
            func.count(Observation.id).label("observation_count"),
        ).group_by(Observation.camera_id)
    )
    obs_by_camera = {row.camera_id: row.observation_count for row in obs_stats.all()}

    camera_stats = []
    total_bytes = 0
    total_obs = 0

    for cam in cameras:
        rec = rec_by_camera.get(cam.id)
        rec_count = rec.recording_count if rec else 0
        rec_bytes = int(rec.recording_bytes) if rec else 0
        obs_count = obs_by_camera.get(cam.id, 0)

        total_bytes += rec_bytes
        total_obs += obs_count

        camera_stats.append(
            CameraStorageStats(
                camera_id=cam.id,
                camera_name=cam.name,
                recording_count=rec_count,
                recording_bytes=rec_bytes,
                observation_count=obs_count,
                retention_mode=cam.retention_mode,
                retention_days=cam.retention_days,
                retention_gb=cam.retention_gb,
            )
        )

    return StorageResponse(
        cameras=camera_stats,
        total_recording_bytes=total_bytes,
        total_observations=total_obs,
    )


@router.get("/vlm-stats")
async def get_vlm_queue_stats(_current_user: User = Depends(get_current_user)):
    """Get VLM processing stats per camera. Latency, queue depth, errors."""
    return get_vlm_stats()


@router.get("/health")
async def get_health(_current_user: User = Depends(get_current_user)):
    """Lightweight host-level CPU / RAM / disk / GPU snapshot for the
    footer.

    Sampled with psutil. cpu_percent uses interval=None so the call
    returns immediately (uses the value since the last call). The
    frontend polls on a coarse cadence so this stays cheap.

    GPU stats are best-effort. ``nvidia-smi`` is queried with a 1.5s
    timeout when present. NULL on non-NVIDIA hosts and on hosts where
    the binary is not on PATH; the UI hides the GPU pill in that case.
    """
    import psutil

    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    # Disk usage on the storage root the app actually writes to. Falls
    # back to '/' if the configured path does not exist yet.
    storage_path = settings.storage_path if hasattr(settings, "storage_path") else "/"
    disk_target = storage_path
    try:
        import os
        if not os.path.isdir(disk_target):
            disk_target = "/"
    except Exception:
        disk_target = "/"
    disk = psutil.disk_usage(disk_target)
    load_avg = None
    try:
        load_avg = list(psutil.getloadavg())
    except (AttributeError, OSError):
        pass

    gpus = _read_nvidia_smi()

    return {
        "cpu_percent": round(cpu, 1),
        "cpu_count": psutil.cpu_count(logical=True),
        "load_avg": load_avg,
        "mem": {
            "total_bytes": mem.total,
            "used_bytes": mem.used,
            "available_bytes": mem.available,
            "percent": mem.percent,
        },
        "disk": {
            "path": disk_target,
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "percent": disk.percent,
        },
        "gpus": gpus,
    }


def _read_nvidia_smi() -> list[dict] | None:
    """Shell out to nvidia-smi for a tight CSV. Returns None when the
    binary is missing or fails. Cached implicitly on every call so the
    cost is just a fork; ~30ms when the driver is up. Frontend polls
    every 10s so this is fine.
    """
    import shutil
    import subprocess

    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.total,memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=True,
        ).stdout
    except Exception:
        return None
    rows: list[dict] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            rows.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "util_percent": float(parts[2]),
                    "mem_total_mb": float(parts[3]),
                    "mem_used_mb": float(parts[4]),
                    "temp_c": float(parts[5]),
                }
            )
        except ValueError:
            continue
    return rows or None


@router.get("/smtp")
async def get_smtp_config(_current_user: User = Depends(require_admin)):
    """Return current SMTP configuration with masked password."""
    masked_password = ""
    if settings.smtp_password:
        pw = settings.smtp_password
        masked_password = pw[:2] + "***" + pw[-2:] if len(pw) >= 4 else "***"
    return {
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_user": settings.smtp_user,
        "smtp_password": masked_password,
        "smtp_from": settings.smtp_from,
        "smtp_tls": settings.smtp_tls,
    }


class SmtpTestRequest(BaseModel):
    to: str


@router.post("/smtp-test")
async def test_smtp(body: SmtpTestRequest, _current_user: User = Depends(require_admin)):
    """Send a test email to verify SMTP configuration."""
    if not settings.smtp_host:
        return {"ok": False, "message": "SMTP not configured. Set SMTP_HOST in your environment or .env file"}

    try:
        await send_email(
            to=body.to,
            subject="Nurby SMTP Test",
            body="This is a test email from Nurby. Your SMTP configuration is working correctly.",
        )
        return {"ok": True, "message": f"Test email sent to {body.to}"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


# ── Runtime app settings ──

class AppSettingsBody(BaseModel):
    nudity_blur: bool | None = None
    nudity_blur_min_score: float | None = None


@router.get("/settings")
async def get_settings(_current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Current runtime flags merged with defaults."""
    from shared.app_settings import get_all_settings
    return await get_all_settings(db)


@router.patch("/settings")
async def patch_settings(body: AppSettingsBody, _current_user: User = Depends(require_admin)):
    """Update a subset of runtime flags."""
    from shared.app_settings import set_setting, get_all_settings
    from shared.database import async_session
    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        await set_setting(k, v)
    async with async_session() as db:
        return await get_all_settings(db)
