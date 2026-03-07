"""
=============================================================================
  routers/auth.py — Authentication endpoints
  POST /auth/register   →  create a new user account
  POST /auth/login      →  verify credentials, return JWT
  GET  /auth/me         →  return the currently authenticated user
=============================================================================
"""

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import User
from schemas.auth import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse, UserResponse
from utils.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

# OAuth2-style bearer token extractor
# — reads the `Authorization: Bearer <token>` header automatically.
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency: get_current_user
# ---------------------------------------------------------------------------
# Reusable across ALL protected routes in every future router.
# Usage:  current_user: User = Depends(get_current_user)

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Decode the JWT, look up the user in the DB, and return the User object.
    Raises HTTP 401 on any failure (expired token, bad signature, user gone).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Extends get_current_user — additionally requires is_admin == True.
    Usage:  admin: User = Depends(get_current_admin)
    """
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

    • Rejects duplicate usernames and emails with a clear 409 error.
    • Hashes the password with bcrypt before persisting.
    • Returns the created user (no sensitive fields).
    """

    # ── 1. Check for duplicate username ──────────────────────────────────────
    existing_username = await db.execute(
        select(User).where(User.username == body.username)
    )
    if existing_username.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken.",
        )

    # ── 2. Check for duplicate email ─────────────────────────────────────────
    existing_email = await db.execute(
        select(User).where(User.email == body.email)
    )
    if existing_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # ── 3. Create and persist the new user ───────────────────────────────────
    new_user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        # is_admin defaults to False in the model — never trust client input
        # is_active defaults to True in the model
    )
    db.add(new_user)
    await db.flush()        # assigns new_user.id without a full commit
    await db.refresh(new_user)

    return RegisterResponse(user=UserResponse.model_validate(new_user))


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
    Authenticate a user and return a signed JWT.

    • Deliberately uses the same vague error for 'user not found' and
      'wrong password' to prevent username enumeration attacks.
    • Rejects inactive accounts before issuing a token.
    """

    # ── 1. Look up user ───────────────────────────────────────────────────────
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

    # ── 3. Check account is still active ─────────────────────────────────────
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    # ── 4. Issue JWT ──────────────────────────────────────────────────────────
    token = create_access_token(
        subject=str(user.id),
        is_admin=user.is_admin,
    )

    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,   # convert to seconds
    )


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the currently authenticated user",
)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """
    Protected route — requires a valid Bearer token.
    Returns the profile of whoever owns the token.
    Useful for the frontend to hydrate user state on load.
    """
    return UserResponse.model_validate(current_user)
