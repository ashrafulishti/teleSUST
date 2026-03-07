"""
=============================================================================
  routers/groups.py — Group management endpoints
=============================================================================
  POST /groups                    create a new group  (any auth user)
  GET  /groups                    list groups the current user belongs to
  POST /groups/{group_id}/join    verify join_password, add user to group
=============================================================================
  Key design notes
  ----------------
  • join_password is bcrypt-hashed on CREATE, verified with verify_password
    on JOIN.  The plain-text value is never stored or returned.
  • The creator is automatically added as the first member after creation.
  • Joining a group you already belong to returns HTTP 409 (not silent).
  • member_count is computed in Python from the already-loaded relationship
    (lazy="selectin") — no extra DB query.
=============================================================================
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
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
# Helper — ORM Group  →  GroupResponse
# ---------------------------------------------------------------------------
# member_count can't be set via from_attributes because it isn't a column,
# so we build the response manually every time.

def _group_to_response(group: Group) -> GroupResponse:
    return GroupResponse(
        id            = group.id,
        name          = group.name,
        description   = group.description,
        is_read_only  = group.is_read_only,
        created_by_id = group.created_by_id,
        created_at    = group.created_at,
        member_count  = len(group.members),   # relationship is selectin-loaded
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

    Steps
    -----
    1. Reject duplicate group names → HTTP 409
    2. Hash the plain-text join_password with bcrypt
    3. Persist the Group row
    4. Insert the creator into user_group (the M2M association table)
    5. Refresh and return GroupResponse
    """

    # ── 1. Duplicate name check ───────────────────────────────────────────────
    existing = await db.execute(select(Group).where(Group.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A group named '{body.name}' already exists.",
        )

    # ── 2 + 3. Hash password and persist ─────────────────────────────────────
    new_group = Group(
        name          = body.name,
        description   = body.description,
        join_password = hash_password(body.join_password),  # ← NEVER store plain text
        is_read_only  = body.is_read_only,
        created_by_id = current_user.id,
    )
    db.add(new_group)
    await db.flush()          # populates new_group.id before the relationship insert

    # ── 4. Auto-join creator ──────────────────────────────────────────────────
    # Append directly to the ORM relationship — SQLAlchemy writes the
    # user_group association row on flush/commit automatically.
    new_group.members.append(current_user)

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

    The User.groups relationship (lazy="selectin") is already loaded by
    get_current_user, so this endpoint requires no additional DB queries.
    """
    # Refresh to ensure the selectin relationship is populated in this session
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

    Steps
    -----
    1. Fetch the group or 404
    2. Check the user isn't already a member → HTTP 409
    3. Verify plain-text body.join_password against Group.join_password
       using verify_password() (bcrypt constant-time comparison)
       Wrong password → HTTP 403  (not 401 — user IS authenticated,
                                    they just supplied the wrong group key)
    4. Append user to group.members (writes user_group row on commit)
    5. Return GroupJoinResponse
    """

    # ── 1. Fetch group ────────────────────────────────────────────────────────
    group = await _get_group_or_404(group_id, db)

    # ── 2. Already a member? ──────────────────────────────────────────────────
    already_member = any(m.id == current_user.id for m in group.members)
    if already_member:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this group.",
        )

    # ── 3. Verify join password ───────────────────────────────────────────────
    if not verify_password(body.join_password, group.join_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Incorrect group password.",
        )

    # ── 4. Add member ─────────────────────────────────────────────────────────
    group.members.append(current_user)
    await db.flush()
    await db.refresh(group)

    # ── 5. Return ─────────────────────────────────────────────────────────────
    return GroupJoinResponse(
        group=_group_to_response(group)
    )
