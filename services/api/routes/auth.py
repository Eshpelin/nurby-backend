from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import create_access_token, get_current_user, hash_password, verify_password
from shared.database import get_db
from shared.models import InviteKey, User, UserCameraAccess
from shared.schemas import AdminSetup, TokenResponse, UserCreate, UserLogin, UserResponse

router = APIRouter()


@router.post("/setup", response_model=TokenResponse, status_code=201)
async def initial_admin_setup(body: AdminSetup, db: AsyncSession = Depends(get_db)):
    """Create the first admin account. Only works when no users exist in the system."""
    count_result = await db.execute(select(func.count()).select_from(User))
    user_count = count_result.scalar()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed. An admin account exists.",
        )

    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user with an invite key."""
    # Validate invite key
    result = await db.execute(select(InviteKey).where(InviteKey.key == body.invite_key))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invite key",
        )

    # Check expiry
    if invite.expires_at is not None:
        now = datetime.now(timezone.utc)
        if invite.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite key has expired",
            )

    # Check usage limit
    if invite.use_count >= invite.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite key has reached its usage limit",
        )

    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    # Create user with the role specified in the invite
    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role=invite.role,
        is_active=True,
    )
    db.add(user)

    # Increment invite usage
    invite.use_count += 1

    await db.flush()

    # Auto-grant camera access from invite
    if invite.camera_ids:
        for cam_id_str in invite.camera_ids:
            access = UserCameraAccess(
                user_id=user.id,
                camera_id=cam_id_str if not isinstance(cam_id_str, str) else cam_id_str,
                granted_by_id=invite.created_by_id,
            )
            db.add(access)

    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate with email and password, returns a JWT."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # Update last login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user
