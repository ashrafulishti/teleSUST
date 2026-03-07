"""
=============================================================================
  schemas/group.py — Pydantic v2 request / response models for Groups
=============================================================================
  Model fields sourced directly from models.py:
    Group.name          String(100)   unique, not null
    Group.description   Text          nullable
    Group.join_password String(255)   hashed, not null
    Group.is_read_only  Boolean       default False
    Group.created_by_id UUID          FK → users.id
    Group.created_at    DateTime
=============================================================================
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ── REQUEST schemas ───────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class GroupCreateRequest(BaseModel):
    """
    Body for  POST /groups

    join_password is accepted as plain text here — it is hashed with bcrypt
    inside the router before it ever touches the database.
    """

    name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        examples=["Study"],
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        examples=["A group for study sessions."],
    )
    join_password: str = Field(
        ...,
        min_length=4,
        max_length=128,
        examples=["study2025"],
        description="Plain-text password. Will be bcrypt-hashed before storage.",
    )
    is_read_only: bool = Field(
        default=False,
        description="Set True for announcement-style groups where only admins post.",
    )


class GroupJoinRequest(BaseModel):
    """
    Body for  POST /groups/{group_id}/join

    The user supplies the plain-text password; the router calls
    verify_password() against the stored bcrypt hash.
    """

    join_password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        examples=["study2025"],
    )


# ---------------------------------------------------------------------------
# ── RESPONSE schemas ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class GroupResponse(BaseModel):
    """
    Safe view of a Group row.

    join_password is intentionally absent — never expose the hash.
    channels is included so the frontend can render sub-channels
    immediately without a second round-trip.
    """

    id:           uuid.UUID
    name:         str
    description:  Optional[str]
    is_read_only: bool
    created_by_id: Optional[uuid.UUID]
    created_at:   datetime
    member_count: int = Field(
        default=0,
        description="Number of members currently in this group.",
    )

    model_config = {"from_attributes": True}


class GroupListResponse(BaseModel):
    """Returned by  GET /groups"""

    groups: List[GroupResponse]
    total:  int = Field(description="Total number of groups the user belongs to.")


class GroupJoinResponse(BaseModel):
    """Returned on a successful  POST /groups/{group_id}/join"""

    message: str = "Successfully joined the group."
    group:   GroupResponse
