"""
=============================================================================
  routers/groups.py — Group management endpoints
=============================================================================
  FIX: new_group.members.append(current_user) replaced with a direct
  INSERT into user_group association table to avoid MissingGreenlet crash.
=============================================================================
"""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Group, User, user_group_association
from routers.auth import get_current_user
from schemas.group import (
    GroupCreateRequest,
    GroupJoinRequest,
    GroupJoinResponse,
    GroupListResponse,
    GroupResponse,
)
from utils.security import hash_password, verify_password

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _group_to_response(group: Group) -> GroupResponse:
    return GroupResponse(
        id            = group.id,
        name          = group.name,
        description   = group.description,
        is_read_only  = group.is_read_only,
        created_by_id = group.created_by_id,
        created_at    = group.created_at,
        member_count  = len(group.members),
    )


async def _get_group_or_404(group_id: uuid.UUID, db: AsyncSession) -> Group:
    result = await db.execute(select(Group).where(Group.id == group_id))
    group  = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Group '{group_id}' not found.")
    return group


# ---------------------------------------------------------------------------
# POST /groups  —  Create a group
# ---------------------------------------------------------------------------

@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupResponse:

    # Duplicate name check
    existing = await db.execute(select(Group).where(Group.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"A group named '{body.name}' already exists.")

    # Create group
    new_group = Group(
        name          = body.name,
        description   = body.description,
        join_password = hash_password(body.join_password),
        is_read_only  = body.is_read_only,
        created_by_id = current_user.id,
    )
    db.add(new_group)
    await db.flush()  # get new_group.id before the association insert

    # FIX: direct INSERT instead of new_group.members.append(current_user)
    # .append() on a new unloaded object triggers a lazy SELECT → MissingGreenlet
    await db.execute(
        insert(user_group_association).values(
            user_id   = current_user.id,
            group_id  = new_group.id,
            joined_at = datetime.now(timezone.utc),
        )
    )

    await db.flush()
    await db.refresh(new_group)
    return _group_to_response(new_group)


# ---------------------------------------------------------------------------
# GET /groups  —  List groups the current user belongs to
# ---------------------------------------------------------------------------

@router.get("", response_model=GroupListResponse)
async def list_my_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupListResponse:

    await db.refresh(current_user)
    group_responses: List[GroupResponse] = [_group_to_response(g) for g in current_user.groups]
    return GroupListResponse(groups=group_responses, total=len(group_responses))


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/join  —  Join a group
# ---------------------------------------------------------------------------

@router.post("/{group_id}/join", response_model=GroupJoinResponse)
async def join_group(
    group_id: uuid.UUID,
    body: GroupJoinRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupJoinResponse:

    group = await _get_group_or_404(group_id, db)

    # Already a member?
    if any(m.id == current_user.id for m in group.members):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="You are already a member of this group.")

    # Verify password
    if not verify_password(body.join_password, group.join_password):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Incorrect group password.")

    # FIX: same direct INSERT as create_group
    await db.execute(
        insert(user_group_association).values(
            user_id   = current_user.id,
            group_id  = group.id,
            joined_at = datetime.now(timezone.utc),
        )
    )

    await db.flush()
    await db.refresh(group)
    return GroupJoinResponse(group=_group_to_response(group))
