"""
=============================================================================
  schemas/auth.py — Pydantic v2 request / response models for auth endpoints
=============================================================================
  Schemas are the HTTP boundary layer:
    • Request schemas  — validate what arrives from the client
    • Response schemas — control exactly what leaves the server
    • They never expose sensitive DB fields (e.g. hashed_password)
=============================================================================
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# ── REQUEST schemas (client → server) ────────────────────────────────────────
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """
    Body for  POST /auth/register

    Validation rules
    ----------------
    username  : 3–50 chars, alphanumeric / underscore / hyphen only
    email     : valid e-mail address (Pydantic EmailStr)
    password  : 8–128 chars, must contain ≥1 letter AND ≥1 digit
    """

    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        examples=["alice_99"],
    )
    email: EmailStr = Field(
        ...,
        examples=["alice@example.com"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        examples=["MyPass123"],
    )

    @field_validator("password")
    @classmethod
    def password_must_have_letter_and_digit(cls, v: str) -> str:
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        return v


class LoginRequest(BaseModel):
    """
    Body for  POST /auth/login

    Accepts username + password.
    We use our own JSON body (not OAuth2 form) to keep the API consistent
    and easy to call from a JS/mobile frontend.
    """

    username: str = Field(..., examples=["alice_99"])
    password: str = Field(..., examples=["MyPass123"])


# ---------------------------------------------------------------------------
# ── RESPONSE schemas (server → client) ───────────────────────────────────────
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    """
    Returned by  POST /auth/login

    Fields
    ------
    access_token : the signed JWT string
    token_type   : always "bearer" — tells clients how to attach it
    expires_in   : token lifetime in SECONDS (not minutes) for easy JS use
    """

    access_token: str
    token_type:   str = "bearer"
    expires_in:   int = Field(description="Token lifetime in seconds.")


class UserResponse(BaseModel):
    """
    Safe view of a User row — hashed_password is intentionally absent.
    Returned by  POST /auth/register  and  GET /auth/me

    model_config from_attributes=True  (Pydantic v2 equivalent of orm_mode)
    allows constructing this from a SQLAlchemy User instance directly:
        UserResponse.model_validate(user_orm_object)
    """

    id:         uuid.UUID
    username:   str
    email:      EmailStr
    is_admin:   bool
    is_active:  bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    """
    Returned by  POST /auth/register
    Wraps UserResponse with a human-readable confirmation message.
    """

    message: str = "Registration successful."
    user:    UserResponse
