import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import require_admin
from shared.database import get_db
from shared.models import InviteKey, User
from shared.schemas import InviteKeyCreate, InviteKeyResponse

router = APIRouter()


@router.get("", response_model=list[InviteKeyResponse])
async def list_invites(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all invite keys. Admin only."""
    result = await db.execute(select(InviteKey).order_by(InviteKey.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=InviteKeyResponse, status_code=201)
async def create_invite(
    body: InviteKeyCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new invite key. Admin only. The key string is auto-generated."""
    # Convert camera_ids to JSON-serialisable list of strings
    camera_ids_json = None
    if body.camera_ids:
        camera_ids_json = [str(cid) for cid in body.camera_ids]

    invite = InviteKey(
        key=secrets.token_hex(16),
        created_by_id=admin.id,
        role=body.role,
        camera_ids=camera_ids_json,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.delete("/{invite_id}", status_code=204)
async def delete_invite(
    invite_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an invite key. Admin only."""
    invite = await db.get(InviteKey, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite key not found")

    await db.delete(invite)
    await db.commit()
