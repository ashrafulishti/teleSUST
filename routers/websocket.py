"""
=============================================================================
  routers/websocket.py — Real-time messaging + User Presence
=============================================================================
  FIX (Bug 10): _evict() now properly cleans up _user_channels.

  The old _evict() only removed the dead socket from _rooms and
  _user_sockets, but never touched _user_channels. This meant:

    • A user with a dead socket in channel X still had X in
      _user_channels[uid], so online_user_ids(X) would keep
      reporting them as present even after they'd gone.
    • _user_channels entries for users with no remaining sockets
      were never pruned — a slow memory leak over time.

  Fix: after discarding the socket from _user_sockets, for every
  channel entry in _user_channels[uid] we check whether the user
  still has a live socket in that channel's room. If not, we remove
  that channel from _user_channels[uid]. Finally, if _user_channels[uid]
  is now empty we delete the key entirely.

  This mirrors the same logic already used in disconnect(), making
  _evict a proper "silent disconnect" that leaves no stale state.
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

MAX_MESSAGE_LENGTH = 2000
HISTORY_LIMIT      = 50


# =============================================================================
#  ConnectionManager  (with Presence)
# =============================================================================

class ConnectionManager:
    """
    Tracks live WebSocket connections grouped by channel_id,
    and user presence across all channels.

    Internal structures
    -------------------
    _rooms         : dict[channel_id_str → set[WebSocket]]
                     All active sockets per channel room.

    _user_channels : dict[user_id_str → set[channel_id_str]]
                     Which channels this user currently has open sockets in.
                     A user may have multiple tabs/devices open simultaneously.

    _user_sockets  : dict[user_id_str → set[WebSocket]]
                     All active sockets for a user, across all channels.
                     Used to determine true online/offline state:
                       len == 0  → offline
                       len >= 1  → online

    Presence rule
    -------------
    "online"  is broadcast when a user's socket count goes from 0 → 1
    "offline" is broadcast when a user's socket count goes from 1 → 0
    Intermediate connects/disconnects (multi-tab) are silent — no spam.
    """

    def __init__(self) -> None:
        self._rooms:         Dict[str, Set[WebSocket]] = defaultdict(set)
        self._user_channels: Dict[str, Set[str]]       = defaultdict(set)
        self._user_sockets:  Dict[str, Set[WebSocket]] = defaultdict(set)

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(
        self,
        channel_id: str,
        user_id: str,
        ws: WebSocket,
    ) -> bool:
        """
        Accept the WebSocket handshake and register the connection.

        Returns
        -------
        bool : True if this is the user's first connection (was offline),
               False if they were already online in another channel/tab.
        """
        await ws.accept()

        was_offline = len(self._user_sockets[user_id]) == 0

        self._rooms[channel_id].add(ws)
        self._user_channels[user_id].add(channel_id)
        self._user_sockets[user_id].add(ws)

        return was_offline

    def disconnect(
        self,
        channel_id: str,
        user_id: str,
        ws: WebSocket,
    ) -> bool:
        """
        Remove a socket from its room and from the user's socket set.

        Returns
        -------
        bool : True if this was the user's LAST socket (now offline),
               False if they still have other connections open.
        """
        self._rooms[channel_id].discard(ws)
        if not self._rooms[channel_id]:
            del self._rooms[channel_id]

        self._user_sockets[user_id].discard(ws)
        self._user_channels[user_id].discard(channel_id)

        is_now_offline = len(self._user_sockets[user_id]) == 0

        # Housekeeping — remove empty user entries
        if is_now_offline:
            self._user_sockets.pop(user_id, None)
            self._user_channels.pop(user_id, None)

        return is_now_offline

    # ── Messaging ─────────────────────────────────────────────────────────────

    async def broadcast(
        self,
        channel_id: str,
        payload: dict,
        exclude: WebSocket | None = None,
    ) -> None:
        """
        Broadcast JSON payload to all sockets in a channel room.

        exclude : skip this socket (pass the sender's ws to avoid echo,
                  or None to send to everyone including the sender).
        Dead sockets are evicted via _evict() which now fully cleans up
        all three internal structures.
        """
        recipients = list(self._rooms.get(channel_id, set()))
        if not recipients:
            return

        text = json.dumps(payload, default=str)

        async def _send_safe(ws: WebSocket) -> None:
            if ws is exclude:
                return
            try:
                await ws.send_text(text)
            except Exception:
                self._evict(channel_id, ws)

        await asyncio.gather(*(_send_safe(ws) for ws in recipients))

    async def broadcast_to_user_groups(
        self,
        user_id: str,
        payload: dict,
        exclude_channel: str | None = None,
    ) -> None:
        """
        Broadcast payload to EVERY channel the user currently has open.

        Used for presence events so all of a user's open chat windows
        receive the status_update simultaneously.
        """
        channels = set(self._user_channels.get(user_id, set()))
        tasks = []
        for ch_id in channels:
            if ch_id == exclude_channel:
                continue
            tasks.append(self.broadcast(ch_id, payload))
        if tasks:
            await asyncio.gather(*tasks)

    async def send_personal(self, ws: WebSocket, payload: dict) -> None:
        """Send payload to a single WebSocket only."""
        await ws.send_text(json.dumps(payload, default=str))

    # ── Presence queries ──────────────────────────────────────────────────────

    def is_online(self, user_id: str) -> bool:
        """Return True if the user has at least one active connection."""
        return len(self._user_sockets.get(user_id, set())) > 0

    def online_user_ids(self, channel_id: str) -> list[str]:
        """
        Return the user_ids of every user currently in a channel room.
        """
        result = []
        for uid, sockets in self._user_sockets.items():
            for sock in sockets:
                if sock in self._rooms.get(channel_id, set()):
                    result.append(uid)
                    break
        return result

    def connection_count(self, channel_id: str) -> int:
        return len(self._rooms.get(channel_id, set()))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _evict(self, channel_id: str, ws: WebSocket) -> None:
        """
        Silently remove a dead socket without triggering presence broadcasts.

        FIX: Now fully cleans up all three internal structures:

          1. Remove ws from _rooms[channel_id]. Delete key if room is empty.

          2. Find which user owns this socket by scanning _user_sockets,
             remove the socket from their set.

          3. For every channel in _user_channels[uid], check whether the
             user still has at least one live socket in that room. If not,
             remove the channel from _user_channels[uid].
             If _user_channels[uid] is now empty, delete the key.

          4. If _user_sockets[uid] is now empty, delete the key.

        This guarantees _user_channels never references a channel the user
        is no longer actually connected to, which was the root cause of the
        stale presence bug.
        """
        # Step 1 — remove socket from room
        self._rooms[channel_id].discard(ws)
        if not self._rooms[channel_id]:
            del self._rooms[channel_id]

        # Step 2 — find the owning user and remove the socket
        owning_uid: str | None = None
        for uid, socks in list(self._user_sockets.items()):
            if ws in socks:
                socks.discard(ws)
                owning_uid = uid
                break

        if owning_uid is None:
            # Socket wasn't tracked — nothing more to do
            return

        # Step 3 — prune _user_channels: remove any channel where this
        # user no longer has a live socket in the room
        channels = self._user_channels.get(owning_uid)
        if channels:
            stale = {
                ch for ch in channels
                if not (self._user_sockets.get(owning_uid, set()) & self._rooms.get(ch, set()))
            }
            channels -= stale
            if not channels:
                del self._user_channels[owning_uid]

        # Step 4 — prune _user_sockets if empty
        if not self._user_sockets.get(owning_uid):
            self._user_sockets.pop(owning_uid, None)


# Module-level singleton — imported by routers/messages.py
manager = ConnectionManager()


# =============================================================================
#  DB helpers
# =============================================================================

async def _authenticate_ws(token: str, db: AsyncSession) -> User | None:
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            return None
    except JWTError:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    return user if (user and user.is_active) else None


async def _get_channel_and_group(
    channel_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[Channel, Group] | tuple[None, None]:
    """Return (Channel, Group) or (None, None) if either is missing."""
    ch_result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel   = ch_result.scalar_one_or_none()
    if channel is None:
        return None, None

    grp_result = await db.execute(select(Group).where(Group.id == channel.group_id))
    group      = grp_result.scalar_one_or_none()
    return channel, group


def _is_group_member(group: Group, user: User) -> bool:
    return any(m.id == user.id for m in group.members)


async def _fetch_history(channel_id: uuid.UUID, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Message)
        .where(Message.channel_id == channel_id, Message.is_deleted == False)  # noqa: E712
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    messages = list(reversed(result.scalars().all()))
    return [
        {
            "type":       "history",
            "id":         str(msg.id),
            "content":    msg.content,
            "author_id":  str(msg.author_id),
            "username":   msg.author.username if msg.author else "[deleted]",
            "channel_id": str(msg.channel_id),
            "timestamp":  msg.created_at.isoformat(),
            "is_edited":  msg.is_edited,
            "updated_at": msg.updated_at.isoformat() if msg.updated_at else None,
        }
        for msg in messages
    ]


async def _persist_message(
    content: str,
    author_id: uuid.UUID,
    channel_id: uuid.UUID,
) -> Message:
    async with AsyncSessionLocal() as session:
        msg = Message(content=content, author_id=author_id, channel_id=channel_id)
        session.add(msg)
        await session.flush()
        await session.refresh(msg)

        author_result = await session.execute(select(User).where(User.id == author_id))
        author        = author_result.scalar_one_or_none()
        await session.commit()

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
    WS /ws/{channel_id}/{token}

    Wire protocol — server → client frame types:

      "history"       — replayed on connect (last HISTORY_LIMIT messages)
      "message"       — new chat message broadcast to channel
      "edit"          — message was edited   (from PUT  /messages/{id})
      "delete"        — message was deleted  (from DELETE /messages/{id})
      "status_update" — user came online or went offline
      "system"        — join / leave announcements
      "error"         — validation error sent to sender only

    Close codes:
      4001  invalid / expired JWT
      4003  not a member of this channel's group
      4004  channel or group not found
    """
    channel_id_str = str(channel_id)

    # ── Auth + membership gate (before accept) ────────────────────────────
    async with AsyncSessionLocal() as db:
        user = await _authenticate_ws(token, db)
        if user is None:
            await ws.close(code=4001, reason="Invalid or expired token.")
            return

        channel, group = await _get_channel_and_group(channel_id, db)
        if channel is None:
            await ws.close(code=4004, reason="Channel not found.")
            return
        if group is None:
            await ws.close(code=4004, reason="Parent group not found.")
            return
        if not _is_group_member(group, user):
            await ws.close(code=4003, reason="You are not a member of this group.")
            return

        # ── Accept + register ─────────────────────────────────────────────
        user_id_str     = str(user.id)
        author_username = user.username

        is_first_connection = await manager.connect(channel_id_str, user_id_str, ws)

        # ── Replay history to this client only ────────────────────────────
        history = await _fetch_history(channel_id, db)
        for entry in history:
            await manager.send_personal(ws, entry)

    # ── Auth session closed — no DB held open ────────────────────────────

    # ── Presence: broadcast "online" if this was the user's first socket ─
    presence_payload = {
        "type":       "status_update",
        "user_id":    user_id_str,
        "username":   author_username,
        "status":     "online",
        "channel_id": channel_id_str,
    }
    if is_first_connection:
        await manager.broadcast(channel_id_str, presence_payload, exclude=ws)
        await manager.broadcast_to_user_groups(
            user_id_str, presence_payload, exclude_channel=channel_id_str
        )

    # ── System join message ───────────────────────────────────────────────
    await manager.broadcast(
        channel_id_str,
        payload={"type": "system", "content": f"{author_username} has joined the channel."},
        exclude=ws,
    )

    # ── Main message loop ─────────────────────────────────────────────────
    try:
        while True:
            raw     = await ws.receive_text()
            content = raw.strip()

            if not content:
                await manager.send_personal(ws, {"type": "error", "content": "Message cannot be empty."})
                continue

            if len(content) > MAX_MESSAGE_LENGTH:
                await manager.send_personal(ws, {
                    "type":    "error",
                    "content": f"Message exceeds {MAX_MESSAGE_LENGTH} character limit.",
                })
                continue

            msg = await _persist_message(
                content    = content,
                author_id  = user.id,
                channel_id = channel_id,
            )

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
                    "is_edited":  False,
                    "updated_at": None,
                },
                exclude=None,
            )

    except WebSocketDisconnect:
        is_now_offline = manager.disconnect(channel_id_str, user_id_str, ws)

        await manager.broadcast(
            channel_id_str,
            payload={"type": "system", "content": f"{author_username} has left the channel."},
            exclude=None,
        )

        # ── Presence: broadcast "offline" only when truly last connection ─
        if is_now_offline:
            offline_payload = {
                "type":       "status_update",
                "user_id":    user_id_str,
                "username":   author_username,
                "status":     "offline",
                "channel_id": channel_id_str,
            }
            await manager.broadcast(channel_id_str, offline_payload)
            await manager.broadcast_to_user_groups(
                user_id_str, offline_payload, exclude_channel=channel_id_str
            )

    except Exception:
        manager.disconnect(channel_id_str, user_id_str, ws)
