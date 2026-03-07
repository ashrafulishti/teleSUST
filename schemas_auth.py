"""
=============================================================================
  schemas/auth.py — Pydantic v2 request / response shapes for auth endpoints
=============================================================================
  Schemas are intentionally separate from SQLAlchemy models:
    • They define what comes IN from the HTTP client (request bodies)
    • They define what goes OUT in the HTTP response
    • They NEVER expose sensitive fields like hashed_password
=============================================================================
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas  (what the client sends)
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Body for POST /auth/register"""

    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_\-]+$",      # alphanumeric, underscore, hyphen only
        examples=["alice_99"],
    )
    email: EmailStr = Field(..., examples=["alice@example.com"])
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        examples=["Str0ng!Pass"],
    )

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        """Require at least one digit and one letter for basic strength."""
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_letter and has_digit):
            raise ValueError("Password must contain at least one letter and one digit.")
        return v


class LoginRequest(BaseModel):
    """Body for POST /auth/login"""

    username: str = Field(..., examples=["alice_99"])
    password: str = Field(..., examples=["Str0ng!Pass"])


# ---------------------------------------------------------------------------
# Response schemas  (what the server returns)
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    """Returned on successful login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(
        description="Token lifetime in seconds."
    )


class UserResponse(BaseModel):
    """
    Returned on successful registration.
    Mirrors the User SQLAlchemy model — sensitive fields are intentionally absent.
    """

    id: uuid.UUID
    username: str
    email: EmailStr
    is_admin: bool
    is_active: bool
    created_at: datetime

    # Pydantic v2: replaces orm_mode = True
    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    """Wraps UserResponse with a friendly message."""

    message: str = "Registration successful."
    user: UserResponse
