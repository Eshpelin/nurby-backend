"""User access filter for agent tools.

Every agent tool funnels its result set through ``accessible_camera_ids``
before returning rows. Admin users see every camera. Regular users see
the union of cameras they own + cameras shared with them via the
``UserCameraAccess`` table. When a regular user has no rows in that
table at all, they fall through to the same default the existing
``GET /api/cameras`` endpoint serves, which is the full camera list.
Per-camera access is opt-in. once any row exists for a user the agent
narrows to that subset.

This mirrors the behaviour of ``services.api.routes.cameras.list_cameras``
which today returns every camera to any authenticated user. The
``require_camera_access`` factory in ``shared.auth`` enforces per-camera
checks on endpoints that explicitly opt into it; the agent tool surface
intentionally walks the same path as the read endpoints rather than
introducing a stricter gate.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Camera, User, UserCameraAccess


async def accessible_camera_ids(user: User, db: AsyncSession) -> set[uuid.UUID]:
    """Return the set of camera UUIDs this user is allowed to query.

    Admin users get every camera. Regular users get the union of cameras
    they have a ``UserCameraAccess`` row for. If a regular user has zero
    rows in that table, they fall through to all cameras (matches the
    read endpoints which do not gate at the row level).
    """
    role = getattr(user, "role", None)
    all_camera_ids = {
        row[0]
        for row in (await db.execute(select(Camera.id))).all()
    }
    if role == "admin":
        return all_camera_ids

    access_rows = (
        await db.execute(
            select(UserCameraAccess.camera_id).where(
                UserCameraAccess.user_id == user.id
            )
        )
    ).all()
    granted = {row[0] for row in access_rows}
    if not granted:
        # No explicit grants. fall through to the full set, matching the
        # existing list-cameras default.
        return all_camera_ids
    return granted & all_camera_ids
