"""
=============================================================================
  routers/auth.py — Authentication endpoints
=============================================================================
  POST /auth/register  →  create a new user account
  POST /auth/login     →  verify credentials, return a JWT
  GET  /auth/me        →  return the current authenticated user's profile

  Re-usable dependencies (import these in every future protected router):
    get_current_user   — any authenticated user
    get_current_admin  — authenticated user WHERE is_admin == True
=============================================================================
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import User
from schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from utils.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()

# OAuth2PasswordBearer reads the `Authorization: Bearer <token>` header.
# tokenUrl tells /docs where to point its "Authorize" button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------------------------------------------------------------------
# Dependency: get_current_user
# ---------------------------------------------------------------------------
# Import and use this in ANY route that requires authentication:
#
#   from routers.auth import get_current_user
#   @router.get("/protected")
#   async def protected(user: User = Depends(get_current_user)):
#       ...

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Decode the JWT → look up user in DB → return User ORM object.
    Raises HTTP 401 for any failure (bad token, expired, user deleted).
    Raises HTTP 403 if the account has been deactivated.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 1. Decode the token
    try:
        payload  = decode_access_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    # 2. Fetch user from DB
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()

    if user is None:
        raise credentials_exc

    # 3. Guard against deactivated accounts
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    return user


# ---------------------------------------------------------------------------
# Dependency: get_current_admin
# ---------------------------------------------------------------------------
# Extends get_current_user — additionally enforces is_admin == True.
#
#   from routers.auth import get_current_admin
#   @router.delete("/users/{id}")
#   async def delete_user(admin: User = Depends(get_current_admin)):
#       ...

async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Raises HTTP 403 if the authenticated user is not an admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required.",
        )
    return current_user


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    """
    Create a new user.

    Steps
    -----
    1. Reject duplicate username  → HTTP 409
    2. Reject duplicate email     → HTTP 409
    3. Hash the password with bcrypt
    4. Persist the new User row
    5. Return the safe UserResponse (no hashed_password)
    """

    # ── 1. Duplicate username check ───────────────────────────────────────────
    existing = await db.execute(
        select(User).where(User.username == body.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken.",
        )

    # ── 2. Duplicate email check ──────────────────────────────────────────────
    existing = await db.execute(
        select(User).where(User.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    # ── 3 + 4. Hash password and persist ──────────────────────────────────────
    new_user = User(
        username        = body.username,
        email           = body.email,
        hashed_password = hash_password(body.password),
        # is_admin  defaults to False in models.py — never trust client input
        # is_active defaults to True  in models.py
    )
    db.add(new_user)

    # flush → assigns new_user.id (UUID) without a full commit
    # get_db() commits automatically on a clean exit, rolls back on exception
    await db.flush()
    await db.refresh(new_user)

    # ── 5. Return ─────────────────────────────────────────────────────────────
    return RegisterResponse(
        user=UserResponse.model_validate(new_user)
    )


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive a JWT access token",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate a user and issue a signed JWT.

    Security notes
    --------------
    • "Incorrect username or password" is returned for BOTH 'user not found'
      and 'wrong password' — this prevents username enumeration attacks.
    • verify_password uses constant-time comparison (passlib) to prevent
      timing attacks.
    """

    # ── 1. Look up user by username ───────────────────────────────────────────
    result = await db.execute(
        select(User).where(User.username == body.username)
    )
    user = result.scalar_one_or_none()

    # ── 2. Verify password (same error for not-found vs wrong password) ───────
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── 3. Reject deactivated accounts ───────────────────────────────────────
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    # ── 4. Issue JWT ──────────────────────────────────────────────────────────
    token = create_access_token(
        subject  = str(user.id),   # UUID → string for JWT `sub` claim
        is_admin = user.is_admin,
    )

    return TokenResponse(
        access_token = token,
        expires_in   = ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # minutes → seconds
    )


# ---------------------------------------------------------------------------
# GET /auth/me  (protected)
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the currently authenticated user's profile",
)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """
    Protected — requires a valid `Authorization: Bearer <token>` header.

    Use this on the frontend to hydrate user state after a page load
    (e.g. restore username, avatar, admin flag from a stored token).
    """
    return UserResponse.model_validate(current_user)
