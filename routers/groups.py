"""
=============================================================================
  routers/groups.py — Group management endpoints
=============================================================================
  FIX: MissingGreenlet crash on POST /groups

  Root cause
  ----------
  The original code did:
      new_group.members.append(current_user)

  new_group is a freshly-created ORM object that has never been loaded
  from the database. When SQLAlchemy sees .append() on an unloaded
  collection, it tries to load the existing members first (so it can
  build the in-memory list). That fires a lazy SELECT synchronously
  inside an async greenlet → MissingGreenlet crash.

  This happens even with lazy="selectin" on the relationship, because
  selectin only applies when the object is loaded via a query — it does
  NOT prevent the lazy load triggered by accessing an uninitialized
  collection on a new object.

  Fix
  ---
  Replace the .append() call with a direct INSERT into the user_group
  association table using db.execute(). This bypasses the relationship
  collection entirely — no lazy load is triggered, no collection needs
  to be initialized, and the row is inserted cleanly.

  Same fix applied to join_group() for the same reason.
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
# Helper — ORM Group → GroupResponse
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


# ---------------------------------------------------------------------------
# Helper — fetch a group by ID or raise 404
# ---------------------------------------------------------------------------

async def _get_group_or_404(group_id: uuid.UUID, db: AsyncSession) -> Group:
    result = await db.execute(select(Group).where(Group.id == group_id))
    group  = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )
    return group


# ---------------------------------------------------------------------------
# POST /groups  —  Create a group
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new group",
)
async def create_group(
    body: GroupCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupResponse:
    """
    Create a new group and immediately add the creator as its first member.
    """

    # ── 1. Duplicate name check ───────────────────────────────────────────
    existing = await db.execute(select(Group).where(Group.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A group named '{body.name}' already exists.",
        )

    # ── 2. Hash password and persist group ────────────────────────────────
    new_group = Group(
        name          = body.name,
        description   = body.description,
        join_password = hash_password(body.join_password),
        is_read_only  = body.is_read_only,
        created_by_id = current_user.id,
    )
    db.add(new_group)
    await db.flush()   # populates new_group.id before the association insert

    # ── 3. Auto-join creator ──────────────────────────────────────────────
    # FIX: Direct INSERT into the association table instead of using
    # new_group.members.append(current_user).
    #
    # .append() on a freshly-created (never DB-loaded) object triggers a
    # lazy SELECT to initialise the collection → MissingGreenlet in async.
    # A direct INSERT bypasses the relationship collection entirely.
    await db.execute(
        insert(user_group_association).values(
            user_id  = current_user.id,
            group_id = new_group.id,
            joined_at = datetime.now(timezone.utc),
        )
    )

    await db.flush()
    await db.refresh(new_group)

    return _group_to_response(new_group)


# ---------------------------------------------------------------------------
# GET /groups  —  List groups the current user belongs to
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=GroupListResponse,
    summary="List all groups the current user is a member of",
)
async def list_my_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupListResponse:
    """
    Return every group the authenticated user belongs to.
    """
    await db.refresh(current_user)

    group_responses: List[GroupResponse] = [
        _group_to_response(g) for g in current_user.groups
    ]

    return GroupListResponse(
        groups = group_responses,
        total  = len(group_responses),
    )


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/join  —  Join a group with a password
# ---------------------------------------------------------------------------

@router.post(
    "/{group_id}/join",
    response_model=GroupJoinResponse,
    summary="Join a group by providing its join password",
)
async def join_group(
    group_id: uuid.UUID,
    body: GroupJoinRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupJoinResponse:
    """
    Add the current user to a group after verifying the join password.
    """

    # ── 1. Fetch group ────────────────────────────────────────────────────
    group = await _get_group_or_404(group_id, db)

    # ── 2. Already a member? ──────────────────────────────────────────────
    already_member = any(m.id == current_user.id for m in group.members)
    if already_member:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this group.",
        )

    # ── 3. Verify join password ───────────────────────────────────────────
    if not verify_password(body.join_password, group.join_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Incorrect group password.",
        )

    # ── 4. Add member ─────────────────────────────────────────────────────
    # FIX: same as create_group — direct INSERT avoids lazy collection load.
    await db.execute(
        insert(user_group_association).values(
            user_id   = current_user.id,
            group_id  = group.id,
            joined_at = datetime.now(timezone.utc),
        )
    )

    await db.flush()
    await db.refresh(group)

    # ── 5. Return ─────────────────────────────────────────────────────────
    return GroupJoinResponse(
        group=_group_to_response(group)
    )
