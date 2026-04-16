import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import require_admin
from shared.database import get_db
from shared.models import Camera, User, UserCameraAccess
from shared.schemas import (
    CameraResponse,
    SetCameraAccessRequest,
    UserCameraAccessResponse,
    UserResponse,
    UserUpdate,
)

router = APIRouter()


@router.get("", response_model=list[UserResponse])
async def list_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users. Admin only."""
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user by ID. Admin only."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's role or active status. Admin only."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def deactivate_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a user by setting is_active to False. Admin only."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await db.commit()


@router.get("/{user_id}/cameras", response_model=list[CameraResponse])
async def list_user_cameras(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all cameras a user has access to. Admin only."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(Camera)
        .join(UserCameraAccess, UserCameraAccess.camera_id == Camera.id)
        .where(UserCameraAccess.user_id == user_id)
        .order_by(Camera.created_at)
    )
    return result.scalars().all()


@router.put("/{user_id}/cameras", response_model=list[UserCameraAccessResponse])
async def set_user_cameras(
    user_id: uuid.UUID,
    body: SetCameraAccessRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Replace a user's entire camera access list. Admin only."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Remove all existing access
    existing = await db.execute(
        select(UserCameraAccess).where(UserCameraAccess.user_id == user_id)
    )
    for row in existing.scalars().all():
        await db.delete(row)

    # Grant new access
    new_rows = []
    for camera_id in body.camera_ids:
        access = UserCameraAccess(
            user_id=user_id,
            camera_id=camera_id,
            granted_by_id=admin.id,
        )
        db.add(access)
        new_rows.append(access)

    await db.commit()

    # Refresh to get server-generated fields
    for row in new_rows:
        await db.refresh(row)

    return new_rows


@router.post("/{user_id}/cameras/{camera_id}", response_model=UserCameraAccessResponse, status_code=201)
async def grant_camera_access(
    user_id: uuid.UUID,
    camera_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Grant a user access to a single camera. Admin only."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    # Check if access already exists
    existing = await db.execute(
        select(UserCameraAccess).where(
            UserCameraAccess.user_id == user_id,
            UserCameraAccess.camera_id == camera_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="User already has access to this camera")

    access = UserCameraAccess(
        user_id=user_id,
        camera_id=camera_id,
        granted_by_id=admin.id,
    )
    db.add(access)
    await db.commit()
    await db.refresh(access)
    return access


@router.delete("/{user_id}/cameras/{camera_id}", status_code=204)
async def revoke_camera_access(
    user_id: uuid.UUID,
    camera_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a user's access to a single camera. Admin only."""
    result = await db.execute(
        select(UserCameraAccess).where(
            UserCameraAccess.user_id == user_id,
            UserCameraAccess.camera_id == camera_id,
        )
    )
    access = result.scalar_one_or_none()
    if access is None:
        raise HTTPException(status_code=404, detail="Camera access not found")

    await db.delete(access)
    await db.commit()
