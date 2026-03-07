"""
=============================================================================
  utils/security.py — Password hashing and JWT encode / decode
  Libraries: passlib[bcrypt]  |  python-jose[cryptography]
=============================================================================
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

# ---------------------------------------------------------------------------
# Config  (read from environment — set these in .env / Render dashboard)
# ---------------------------------------------------------------------------

SECRET_KEY: str = os.environ["SECRET_KEY"]
ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
)

# ---------------------------------------------------------------------------
# Passlib context
# ---------------------------------------------------------------------------
# bcrypt is the only scheme; deprecated="auto" means passlib will
# transparently re-hash any legacy hash on next login if you ever migrate.

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*.  Use for both user and group passwords."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*, False otherwise."""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(
    subject: str,           # typically str(user.id)
    is_admin: bool = False,
    extra_claims: Optional[dict] = None,
) -> str:
    """
    Encode a signed JWT.

    Payload claims
    --------------
    sub       — subject (user UUID as string)
    is_admin  — mirrors User.is_admin; used by admin-only route guards
    exp       — expiry timestamp (UTC)
    iat       — issued-at timestamp (UTC)

    The token is signed with SECRET_KEY using ALGORITHM (default HS256).
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict = {
        "sub": subject,
        "is_admin": is_admin,
        "iat": now,
        "exp": expire,
    }

    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a JWT.

    Returns the full payload dict on success.
    Raises jose.JWTError (which callers should catch and convert to HTTP 401).

    Checks performed by python-jose automatically:
      • Signature validity
      • Token expiry (`exp` claim)
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
