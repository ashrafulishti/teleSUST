"""
=============================================================================
  routers/channels.py — Channel management endpoints
=============================================================================
  POST /groups/{group_id}/channels    create a channel inside a group
  GET  /groups/{group_id}/channels    list all channels in a group
=============================================================================
  Key design notes
  ----------------
  • Both endpoints require the caller to be a MEMBER of the target group.
    Non-members receive HTTP 403 — they shouldn't even know what channels
    a group has until they've joined.
  • Channel names are unique within a group (not globally).  A "general"
    channel can exist in every group — checked with a scoped query.
  • Creating channels in a read-only (announcement) group is restricted
    to admins only.
=============================================================================
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Channel, Group, User
from routers.auth import get_current_user
from schemas.channel import (
    ChannelCreateRequest,
    ChannelListResponse,
    ChannelResponse,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Guards — membership + group existence
# ---------------------------------------------------------------------------

async def _get_group_or_404(group_id: uuid.UUID, db: AsyncSession) -> Group:
    """Fetch a Group by PK or raise HTTP 404."""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group  = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )
    return group


def _assert_member(group: Group, user: User) -> None:
    """
    Raise HTTP 403 if *user* is not in *group*.members.
    Called on every channel endpoint — non-members are fully blind to
    a group's channels until they join.
    """
    is_member = any(m.id == user.id for m in group.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of this group to access its channels.",
        )


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/channels  —  Create a channel
# ---------------------------------------------------------------------------

@router.post(
    "",                                    # prefix "/groups/{group_id}/channels"
    response_model=ChannelResponse,        # is set in main.py
    status_code=status.HTTP_201_CREATED,
    summary="Create a new channel inside a group",
)
async def create_channel(
    group_id: uuid.UUID,
    body: ChannelCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelResponse:
    """
    Create a channel inside a group.

    Steps
    -----
    1. Fetch group or 404
    2. Caller must be a group member → 403
    3. read_only groups: only admins can add channels → 403
    4. Channel name must be unique within this group → 409
    5. Persist and return ChannelResponse
    """

    # ── 1. Group exists? ──────────────────────────────────────────────────────
    group = await _get_group_or_404(group_id, db)

    # ── 2. Must be a member ───────────────────────────────────────────────────
    _assert_member(group, current_user)

    # ── 3. Read-only group: admin only ────────────────────────────────────────
    if group.is_read_only and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can create channels in a read-only group.",
        )

    # ── 4. Duplicate channel name (scoped to this group) ─────────────────────
    existing = await db.execute(
        select(Channel).where(
            Channel.group_id == group_id,
            Channel.name     == body.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A channel named '{body.name}' already exists in this group.",
        )

    # ── 5. Persist ────────────────────────────────────────────────────────────
    new_channel = Channel(
        name     = body.name,
        topic    = body.topic,
        group_id = group_id,
    )
    db.add(new_channel)
    await db.flush()
    await db.refresh(new_channel)

    return ChannelResponse.model_validate(new_channel)


# ---------------------------------------------------------------------------
# GET /groups/{group_id}/channels  —  List channels
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ChannelListResponse,
    summary="List all channels in a group",
)
async def list_channels(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChannelListResponse:
    """
    Return all channels belonging to a group.

    The caller must be a member of the group — non-members are not shown
    channel names even for discovery purposes.

    Channels are returned ordered by creation date (oldest first) so the
    UI renders them in a stable, predictable order.
    """

    # ── 1. Group exists? ──────────────────────────────────────────────────────
    group = await _get_group_or_404(group_id, db)

    # ── 2. Must be a member ───────────────────────────────────────────────────
    _assert_member(group, current_user)

    # ── 3. Query channels ordered by creation time ────────────────────────────
    result = await db.execute(
        select(Channel)
        .where(Channel.group_id == group_id)
        .order_by(Channel.created_at.asc())
    )
    channels: List[Channel] = list(result.scalars().all())

    return ChannelListResponse(
        channels = [ChannelResponse.model_validate(c) for c in channels],
        total    = len(channels),
    )
