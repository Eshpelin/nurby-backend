"""Privacy zones API. list / patch / delete.

Auto-zones populate via the perception pipeline. This route lets
the user toggle them on/off, lock them so the auto refresh stops
overwriting their polygon, or delete them entirely. It also exposes
a list of supported target labels so the camera-edit UI can show
a chip picker.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import get_current_user
from shared.database import get_db
from shared.models import PrivacyZone, User
from services.perception.privacy import SUPPORTED_TARGETS

router = APIRouter()


def _serialize(z: PrivacyZone) -> dict[str, Any]:
    return {
        "id": str(z.id),
        "camera_id": str(z.camera_id),
        "label": z.label,
        "polygon": z.polygon,
        "source": z.source,
        "auto_score": z.auto_score,
        "active": z.active,
        "locked": z.locked,
        "detected_at": z.detected_at.isoformat() if z.detected_at else None,
        "last_seen_at": z.last_seen_at.isoformat() if z.last_seen_at else None,
        "ptz_pose": z.ptz_pose,
        "stale_after_seconds": z.stale_after_seconds,
    }


@router.get("")
async def list_zones(
    camera_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(PrivacyZone).where(PrivacyZone.camera_id == camera_id)
        )
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/targets")
async def list_supported_targets(_user: User = Depends(get_current_user)):
    """Static list of labels the auto detector knows how to match.
    Frontend uses this for the chip picker so users can't type
    something the pipeline doesn't understand."""
    return sorted(SUPPORTED_TARGETS)


class ZonePatch(BaseModel):
    active: bool | None = None
    locked: bool | None = None
    label: str | None = None
    polygon: list[list[float]] | None = None
    ptz_pose: dict | None = None
    stale_after_seconds: int | None = None


@router.patch("/{zone_id}")
async def patch_zone(
    zone_id: uuid.UUID,
    body: ZonePatch,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    z = await db.get(PrivacyZone, zone_id)
    if z is None:
        raise HTTPException(status_code=404, detail="zone not found")
    updates = body.model_dump(exclude_unset=True)
    # Manual edits flip source to manual so the auto refresh stops
    # overwriting them.
    if "polygon" in updates and updates["polygon"]:
        z.polygon = updates["polygon"]
        z.source = "manual"
    if "label" in updates and updates["label"]:
        z.label = updates["label"]
    if "active" in updates and updates["active"] is not None:
        z.active = bool(updates["active"])
    if "locked" in updates and updates["locked"] is not None:
        z.locked = bool(updates["locked"])
    if "ptz_pose" in updates:
        z.ptz_pose = updates["ptz_pose"]
    if "stale_after_seconds" in updates and updates["stale_after_seconds"] is not None:
        z.stale_after_seconds = max(5, int(updates["stale_after_seconds"]))
    await db.commit()
    await db.refresh(z)
    return _serialize(z)


@router.delete("/{zone_id}", status_code=204)
async def delete_zone(
    zone_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    z = await db.get(PrivacyZone, zone_id)
    if z is None:
        raise HTTPException(status_code=404, detail="zone not found")
    await db.delete(z)
    await db.commit()
