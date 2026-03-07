"""
=============================================================================
  routers/messages.py — Message management (edit + delete)
=============================================================================
  PUT    /messages/{message_id}   edit a message's content
  DELETE /messages/{message_id}   soft-delete a message

  Both endpoints:
    • Require JWT authentication (get_current_user)
    • Enforce ownership — only the message author can edit/delete
      (admins bypass the ownership check via get_current_admin)
    • After the DB write, broadcast the event to the channel's live
      WebSocket room using the shared `manager` singleton from websocket.py
      so every connected client updates its UI in real time

  Broadcast wire format (server → client):

    Edit event:
    {
      "type":       "edit",
      "id":         "<message_uuid>",
      "content":    "new content here",
      "author_id":  "<user_uuid>",
      "channel_id": "<channel_uuid>",
      "is_edited":  true,
      "updated_at": "2025-01-01T12:05:00+00:00"
    }

    Delete event:
    {
      "type":       "delete",
      "id":         "<message_uuid>",
      "channel_id": "<channel_uuid>"
    }

  Importing `manager` from websocket.py
  --------------------------------------
  The ConnectionManager singleton lives in websocket.py and is the single
  source of truth for all active connections.  Importing it here avoids
  creating a second, disconnected manager that would broadcast to nobody.
  There is no circular import because messages.py does NOT import anything
  that in turn imports messages.py.
=============================================================================
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Message, User
from routers.auth import get_current_user
# Import the shared singleton — this is the correct pattern.
# messages.py → websocket.manager (one-way, no cycle)
from routers.websocket import manager

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas (inline — small enough not to need a file)
# ---------------------------------------------------------------------------

class MessageEditRequest(BaseModel):
    content: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="New message content.",
        examples=["Edited: see you tomorrow instead!"],
    )


class MessageResponse(BaseModel):
    id:         uuid.UUID
    content:    str
    author_id:  uuid.UUID | None
    channel_id: uuid.UUID
    is_edited:  bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helper — fetch message or 404
# ---------------------------------------------------------------------------

async def _get_message_or_404(
    message_id: uuid.UUID,
    db: AsyncSession,
) -> Message:
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg    = result.scalar_one_or_none()
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{message_id}' not found.",
        )
    return msg


def _assert_not_deleted(msg: Message) -> None:
    if msg.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This message has already been deleted.",
        )


def _assert_ownership(msg: Message, user: User) -> None:
    """
    Allow if the user is the author OR an admin.
    Admins can edit/delete any message (moderation use-case).
    """
    if msg.author_id != user.id and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit or delete your own messages.",
        )


# ---------------------------------------------------------------------------
# PUT /messages/{message_id}  —  Edit
# ---------------------------------------------------------------------------

@router.put(
    "/{message_id}",
    response_model=MessageResponse,
    summary="Edit a message",
)
async def edit_message(
    message_id: uuid.UUID,
    body: MessageEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    """
    Update the content of a message and broadcast an 'edit' event
    to all connected clients in the same channel.

    Steps
    -----
    1. Fetch message or 404
    2. Reject if already deleted (410 Gone)
    3. Reject if caller is not the author and not admin (403)
    4. Update content, set is_edited=True, updated_at=now()
    5. Commit
    6. Broadcast { type: "edit", ... } to the channel room
    7. Return the updated MessageResponse
    """

    # ── 1–3. Fetch + guards ───────────────────────────────────────────────────
    msg = await _get_message_or_404(message_id, db)
    _assert_not_deleted(msg)
    _assert_ownership(msg, current_user)

    # ── 4. Apply edit ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    msg.content    = body.content.strip()
    msg.is_edited  = True
    msg.updated_at = now
    msg.edited_at  = now           # keep original field in sync

    db.add(msg)
    await db.flush()
    await db.refresh(msg)

    # ── 5. Commit handled by get_db() context ─────────────────────────────────

    # ── 6. Broadcast edit event to the channel room ───────────────────────────
    channel_id_str = str(msg.channel_id)
    await manager.broadcast(
        channel_id_str,
        payload={
            "type":       "edit",
            "id":         str(msg.id),
            "content":    msg.content,
            "author_id":  str(msg.author_id),
            "channel_id": channel_id_str,
            "is_edited":  True,
            "updated_at": now.isoformat(),
        },
        exclude=None,   # include editor's own socket — confirms the save
    )

    return MessageResponse.model_validate(msg)


# ---------------------------------------------------------------------------
# DELETE /messages/{message_id}  —  Soft-delete
# ---------------------------------------------------------------------------

@router.delete(
    "/{message_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a message",
)
async def delete_message(
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Soft-delete a message and broadcast a 'delete' event to all connected
    clients in the same channel.

    Soft-delete (is_deleted=True) rather than hard-delete means:
      • Message rows are preserved for audit / moderation purposes
      • The history query in websocket.py already filters is_deleted=False
        so deleted messages won't appear in history replays
      • A future admin "view deleted" feature is trivially possible

    Steps
    -----
    1. Fetch message or 404
    2. Reject if already deleted (410 Gone — idempotency with a clear signal)
    3. Reject if caller is not the author and not admin (403)
    4. Set is_deleted=True, updated_at=now()
    5. Commit
    6. Broadcast { type: "delete", id, channel_id } to the channel room
    7. Return confirmation
    """

    # ── 1–3. Fetch + guards ───────────────────────────────────────────────────
    msg = await _get_message_or_404(message_id, db)
    _assert_not_deleted(msg)
    _assert_ownership(msg, current_user)

    # ── 4. Soft-delete ────────────────────────────────────────────────────────
    now            = datetime.now(timezone.utc)
    msg.is_deleted = True
    msg.updated_at = now

    db.add(msg)
    await db.flush()

    # ── 5. Commit handled by get_db() context ─────────────────────────────────

    # ── 6. Broadcast delete event ─────────────────────────────────────────────
    channel_id_str = str(msg.channel_id)
    await manager.broadcast(
        channel_id_str,
        payload={
            "type":       "delete",
            "id":         str(msg.id),
            "channel_id": channel_id_str,
        },
        exclude=None,
    )

    return {"message": "Message deleted successfully.", "id": str(msg.id)}
