import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.database import get_db
from shared.models import User, UserCameraAccess

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours)
    payload = {
        "sub": str(user_id),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID | None:
    """Decode a JWT and return the user UUID, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        return uuid.UUID(sub)
    except (JWTError, ValueError):
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency that extracts and validates the current user from the Authorization header."""
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )
    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """FastAPI dependency that ensures the current user has admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def require_camera_access(camera_id_param: str = "camera_id"):
    """Factory that returns a FastAPI dependency checking camera access for the current user.

    Admins always have access. Viewers must have an explicit UserCameraAccess row.
    """

    async def _check(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        **kwargs,
    ) -> User:
        # Admins bypass per-camera checks
        if current_user.role == "admin":
            return current_user

        # Extract camera_id from path params via the request
        from fastapi import Request

        request = kwargs.get("request")
        if request is None:
            raise HTTPException(status_code=500, detail="Cannot resolve camera_id")

        camera_id_str = request.path_params.get(camera_id_param)
        if camera_id_str is None:
            raise HTTPException(status_code=400, detail="Missing camera_id")

        try:
            camera_id = uuid.UUID(camera_id_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid camera_id format")

        result = await db.execute(
            select(UserCameraAccess).where(
                UserCameraAccess.user_id == current_user.id,
                UserCameraAccess.camera_id == camera_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to this camera",
            )
        return current_user

    return _check
