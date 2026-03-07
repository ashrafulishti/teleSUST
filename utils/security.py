"""
=============================================================================
  utils/security.py — Password hashing (passlib/bcrypt) + JWT (python-jose)
=============================================================================
  ⚠️  COMPATIBILITY NOTE — passlib 1.7.4 + bcrypt ≥ 4.1
  ------------------------------------------------------
  bcrypt ≥ 4.1 removed the `__about__` attribute that passlib 1.7.4 reads
  at import time.  This causes:
      AttributeError: module 'bcrypt' has no attribute '__about__'
  The monkey-patch below restores that attribute before passlib loads.
  requirements.txt pins bcrypt==4.0.1 as a belt-and-suspenders guard, but
  the patch means the app also survives if that pin ever drifts.
=============================================================================
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Monkey-patch ─────────────────────────────────────────────────────────────
# MUST come BEFORE `from passlib.context import CryptContext`
import bcrypt
if not hasattr(bcrypt, "__about__"):
    class _About:
        __version__ = bcrypt.__version__
    bcrypt.__about__ = _About()
# ─────────────────────────────────────────────────────────────────────────────

from passlib.context import CryptContext
from jose import JWTError, jwt  # noqa: F401  (JWTError re-exported for callers)


# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------
# Set in .env (local) or Render environment variables (production):
#
#   SECRET_KEY                  — openssl rand -hex 32
#   ALGORITHM                   — default: HS256
#   ACCESS_TOKEN_EXPIRE_MINUTES — default: 60

SECRET_KEY: str = os.environ["SECRET_KEY"]
ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
)


# ---------------------------------------------------------------------------
# Passlib context
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """
    Return a bcrypt hash of *plain*.
    Used for both User.hashed_password and Group.join_password.
    """
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Return True if *plain* matches *hashed*.
    Uses constant-time comparison — safe against timing attacks.
    """
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(
    subject: str,
    is_admin: bool = False,
    extra_claims: Optional[dict] = None,
) -> str:
    """
    Encode a signed JWT.

    Parameters
    ----------
    subject      : User.id as a string  →  stored in `sub` claim
    is_admin     : mirrors User.is_admin so route guards skip a DB query
    extra_claims : any additional k/v pairs to embed in the payload

    Auto-included claims:
      sub, is_admin, iat (issued-at), exp (expiry)
    """
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict = {
        "sub":      subject,
        "is_admin": is_admin,
        "iat":      now,
        "exp":      expire,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a JWT.  Returns the full payload dict.

    Raises jose.JWTError on bad signature, expiry, or malformed token.
    Callers should catch JWTError and raise HTTP 401.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
