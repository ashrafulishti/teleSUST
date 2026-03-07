"""
=============================================================================
  routers/websocket.py — Real-time messaging via WebSockets
=============================================================================
  Endpoint:
    WS  /ws/{channel_id}/{token}

  Flow per connection:
    1.  Client opens  ws://host/ws/<channel_id>/<jwt>
    2.  Server decodes the JWT from the URL path (browsers can't set headers
        on native WebSocket connections).
    3.  Server verifies the user is a member of the channel's parent group.
    4.  Connection is registered in ConnectionManager for that channel_id.
    5.  Server sends the last N messages as history so the UI can catch up.
    6.  On every incoming text frame:
          a. Strip / validate content (reject empty, enforce max length).
          b. Persist a Message row in its own DB session (fully async).
          c. Broadcast a JSON payload to every other socket in the channel.
    7.  On disconnect / any error the socket is cleanly removed.

  ConnectionManager:
    • Pure in-process dict: channel_id (UUID str) → set of WebSocket objects.
    • No Redis, no Celery — right-sized for ~100 concurrent users on one
      Render instance.
    • broadcast() uses asyncio.gather() so all sends happen concurrently,
      not sequentially.  Dead sockets are removed silently.

  Security:
    • JWT decoded before accept() — invalid tokens are rejected with WS
      close code 4001 (application-level auth failure) before the handshake
      completes.
    • User must be a member of the channel's parent group — outsiders are
      rejected with close code 4003.
    • Content is stripped and length-capped server-side regardless of client.
=============================================================================
"""

import asyncio
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import Channel, Group, Message, User
from utils.security import decode_access_token

router = APIRouter()

# Maximum characters accepted per message (server-side guard)
MAX_MESSAGE_LENGTH = 2000

# How many past messages to replay to a newly connected client
HISTORY_LIMIT = 50


# =============================================================================
#  ConnectionManager
# =============================================================================

class ConnectionManager:
    """
    Tracks live WebSocket connections grouped by channel_id.

    Internal structure:
        _rooms: dict[str, set[WebSocket]]
            key   → channel_id as a plain string
            value → set of active WebSocket objects in that channel

    All methods are intentionally simple — no locks needed because Python's
    asyncio event loop is single-threaded; dict/set mutations between awaits
    are atomic from the coroutine's perspective.
    """

    def __init__(self) -> None:
        self._rooms: Dict[str, Set[WebSocket]] = defaultdict(set)

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self, channel_id: str, ws: WebSocket) -> None:
        """Accept the WebSocket handshake and register the connection."""
        await ws.accept()
        self._rooms[channel_id].add(ws)

    def disconnect(self, channel_id: str, ws: WebSocket) -> None:
        """Remove a socket from its room.  Safe to call even if not present."""
        self._rooms[channel_id].discard(ws)
        # Clean up the room key when it goes empty to avoid unbounded growth
        if not self._rooms[channel_id]:
            del self._rooms[channel_id]

    # ── Messaging ─────────────────────────────────────────────────────────────

    async def broadcast(
        self,
        channel_id: str,
        payload: dict,
        exclude: WebSocket | None = None,
    ) -> None:
        """
        Send *payload* as JSON to every socket in *channel_id*.

        Parameters
        ----------
        channel_id : the target room
        payload    : dict that will be JSON-serialised
        exclude    : if provided, skip this socket (the sender)
                     Set to None to broadcast to ALL including sender
                     (useful for system messages like join/leave).

        Dead sockets (send raises an exception) are removed silently so
        one broken connection never blocks the rest.
        """
        recipients = list(self._rooms.get(channel_id, set()))
        if not recipients:
            return

        text = json.dumps(payload, default=str)   # default=str handles UUIDs/datetimes

        async def _send_safe(ws: WebSocket) -> None:
            if ws is exclude:
                return
            try:
                await ws.send_text(text)
            except Exception:
                # Socket died mid-send — quietly evict it
                self.disconnect(channel_id, ws)

        await asyncio.gather(*(_send_safe(ws) for ws in recipients))

    async def send_personal(self, ws: WebSocket, payload: dict) -> None:
        """Send *payload* to a single WebSocket only."""
        await ws.send_text(json.dumps(payload, default=str))

    # ── Introspection ─────────────────────────────────────────────────────────

    def connection_count(self, channel_id: str) -> int:
        """Return the number of active connections in a channel."""
        return len(self._rooms.get(channel_id, set()))


# Module-level singleton — shared across all requests in this process
manager = ConnectionManager()


# =============================================================================
#  Helpers
# =============================================================================

async def _authenticate_ws(token: str, db: AsyncSession) -> User | None:
    """
    Decode the JWT and return the matching User, or None on any failure.
    Called before accept() so we can reject bad tokens cleanly.
    """
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            return None
    except JWTError:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()

    if user is None or not user.is_active:
        return None

    return user


async def _get_channel_with_group(
    channel_id: uuid.UUID,
    db: AsyncSession,
) -> Channel | None:
    """Fetch the Channel (with its group pre-loaded) or return None."""
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id)
    )
    return result.scalar_one_or_none()


def _is_group_member(group: Group, user: User) -> bool:
    """Return True if user appears in group.members (selectin-loaded)."""
    return any(m.id == user.id for m in group.members)


async def _fetch_history(channel_id: uuid.UUID, db: AsyncSession) -> list[dict]:
    """
    Return the last HISTORY_LIMIT messages for the channel, oldest first.
    Each dict matches the wire format sent to clients.
    """
    result = await db.execute(
        select(Message)
        .where(
            Message.channel_id == channel_id,
            Message.is_deleted == False,            # noqa: E712
        )
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    messages = list(reversed(result.scalars().all()))   # flip to chronological

    history = []
    for msg in messages:
        history.append({
            "type":       "history",
            "id":         str(msg.id),
            "content":    msg.content,
            "author_id":  str(msg.author_id),
            "username":   msg.author.username if msg.author else "[deleted]",
            "channel_id": str(msg.channel_id),
            "timestamp":  msg.created_at.isoformat(),
        })
    return history


async def _persist_message(
    content: str,
    author_id: uuid.UUID,
    channel_id: uuid.UUID,
) -> Message:
    """
    Open a dedicated DB session, save the Message, commit, and return it.

    Using a separate session (not the one used for auth) keeps the
    WebSocket loop's long-lived connection from holding a transaction open
    for the entire life of the socket.
    """
    async with AsyncSessionLocal() as session:
        msg = Message(
            content    = content,
            author_id  = author_id,
            channel_id = channel_id,
        )
        session.add(msg)
        await session.flush()
        await session.refresh(msg)

        # Eagerly load the author so we can read .username after commit
        result = await session.execute(
            select(User).where(User.id == author_id)
        )
        author = result.scalar_one_or_none()

        await session.commit()

    # Attach author to the detached Message object for convenient access
    msg.author = author
    return msg


# =============================================================================
#  WebSocket endpoint
# =============================================================================

@router.websocket("/{channel_id}/{token}")
async def websocket_endpoint(
    ws: WebSocket,
    channel_id: uuid.UUID,
    token: str,
) -> None:
    """
    ws://host/ws/{channel_id}/{token}

    Authentication is via JWT in the URL path because the browser's native
    WebSocket API does not support custom headers during the handshake.
    The token is validated BEFORE accept() is called — if auth fails the
    server closes the socket with an application-level error code:

        4001  — invalid / expired JWT
        4003  — user is not a member of this channel's group

    Message wire format (server → client):

        System / history replay:
        {
          "type":    "history" | "system",
          "content": "...",
          ...
        }

        Chat message (broadcast):
        {
          "type":       "message",
          "id":         "<uuid>",
          "content":    "Hello!",
          "author_id":  "<uuid>",
          "username":   "alice",
          "channel_id": "<uuid>",
          "timestamp":  "2025-01-01T12:00:00+00:00"
        }

        Error (sent to sender only):
        {
          "type":    "error",
          "content": "Message cannot be empty."
        }

    Message wire format (client → server):
        Plain text string — just the message content.
    """

    channel_id_str = str(channel_id)

    # ── Open a DB session for the auth + history phase ────────────────────────
    async with AsyncSessionLocal() as db:

        # 1. Authenticate — decode JWT, look up user
        user = await _authenticate_ws(token, db)
        if user is None:
            await ws.close(code=4001, reason="Invalid or expired token.")
            return

        # 2. Fetch channel
        channel = await _get_channel_with_group(channel_id, db)
        if channel is None:
            await ws.close(code=4004, reason="Channel not found.")
            return

        # 3. Fetch the parent group (needed for membership check)
        group_result = await db.execute(
            select(Group).where(Group.id == channel.group_id)
        )
        group = group_result.scalar_one_or_none()
        if group is None:
            await ws.close(code=4004, reason="Parent group not found.")
            return

        # 4. Membership check
        if not _is_group_member(group, user):
            await ws.close(code=4003, reason="You are not a member of this group.")
            return

        # 5. Accept connection and register in the room
        await manager.connect(channel_id_str, ws)

        # 6. Replay history to the newly connected client only
        history = await _fetch_history(channel_id, db)
        for entry in history:
            await manager.send_personal(ws, entry)

        # Snapshot author info we'll reuse in the loop
        author_id       = user.id
        author_username = user.username

    # ── Auth session closed — DB is no longer held open ───────────────────────

    # 7. Announce join to all OTHER users in the channel
    await manager.broadcast(
        channel_id_str,
        payload={
            "type":    "system",
            "content": f"{author_username} has joined the channel.",
        },
        exclude=ws,
    )

    # ── Main message loop ─────────────────────────────────────────────────────
    try:
        while True:
            # receive_text() suspends here until a frame arrives
            raw = await ws.receive_text()

            # ── Validate content ──────────────────────────────────────────────
            content = raw.strip()

            if not content:
                await manager.send_personal(ws, {
                    "type":    "error",
                    "content": "Message cannot be empty.",
                })
                continue

            if len(content) > MAX_MESSAGE_LENGTH:
                await manager.send_personal(ws, {
                    "type":    "error",
                    "content": f"Message exceeds {MAX_MESSAGE_LENGTH} character limit.",
                })
                continue

            # ── Persist to database ───────────────────────────────────────────
            # Each message gets its own session → no long-lived transactions.
            msg = await _persist_message(
                content    = content,
                author_id  = author_id,
                channel_id = channel_id,
            )

            # ── Broadcast to all clients in the channel ───────────────────────
            # exclude=None → sender also receives their own message back,
            # which is the standard chat UX (confirms delivery).
            await manager.broadcast(
                channel_id_str,
                payload={
                    "type":       "message",
                    "id":         str(msg.id),
                    "content":    msg.content,
                    "author_id":  str(msg.author_id),
                    "username":   author_username,
                    "channel_id": channel_id_str,
                    "timestamp":  msg.created_at.isoformat(),
                },
                exclude=None,   # include sender — confirms message was saved
            )

    except WebSocketDisconnect:
        # Normal browser close / page navigation
        manager.disconnect(channel_id_str, ws)
        await manager.broadcast(
            channel_id_str,
            payload={
                "type":    "system",
                "content": f"{author_username} has left the channel.",
            },
            exclude=None,
        )

    except Exception:
        # Unexpected error — evict socket silently, don't crash the server
        manager.disconnect(channel_id_str, ws)
