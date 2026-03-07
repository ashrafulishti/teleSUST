"""
=============================================================================
  schemas/channel.py — Pydantic v2 request / response models for Channels
=============================================================================
  Model fields sourced directly from models.py:
    Channel.name       String(100)   not null
    Channel.topic      String(255)   nullable
    Channel.group_id   UUID          FK → groups.id
    Channel.created_at DateTime
=============================================================================
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ── REQUEST schemas ───────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class ChannelCreateRequest(BaseModel):
    """
    Body for  POST /groups/{group_id}/channels

    group_id is NOT in the request body — it comes from the URL path
    parameter so the client never needs to repeat it.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        examples=["general"],
        description="Channel name, e.g. 'general', 'math', 'announcements'.",
    )
    topic: Optional[str] = Field(
        default=None,
        max_length=255,
        examples=["Daily study discussion"],
        description="Optional one-line description of the channel's purpose.",
    )


# ---------------------------------------------------------------------------
# ── RESPONSE schemas ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class ChannelResponse(BaseModel):
    """Safe view of a Channel row."""

    id:         uuid.UUID
    name:       str
    topic:      Optional[str]
    group_id:   uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class ChannelListResponse(BaseModel):
    """Returned by  GET /groups/{group_id}/channels"""

    channels: List[ChannelResponse]
    total:    int = Field(description="Total channels in this group.")
