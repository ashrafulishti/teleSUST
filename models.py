"""
=============================================================================
  REAL-TIME GROUP CHAT PLATFORM — DATABASE MODELS
=============================================================================
  FIX: MissingGreenlet crash on group creation (and anywhere else a
  relationship is accessed in an async context).

  Root cause
  ----------
  SQLAlchemy's default lazy loading strategy is lazy="select", which fires
  a synchronous SELECT when a relationship attribute is first accessed.
  In an async SQLAlchemy app this raises:

      MissingGreenlet: greenlet_spawn has not been called;
      can't call await_only() here.

  because the sync DB call has no async greenlet to run inside.

  The original models.py had:
    • Channel.group      — no lazy= specified → defaults to lazy="select" ← CRASH
    • Channel.messages   — no lazy= specified → defaults to lazy="select" ← CRASH
    • Message.channel    — no lazy= specified → defaults to lazy="select" ← CRASH
    • Group.created_by   — lazy="joined"  (sync JOIN)                     ← CRASH
    • Message.author     — lazy="joined"  (sync JOIN)                     ← CRASH on
                           some SQLAlchemy versions when session is closed

  Fix applied
  -----------
  Every relationship now has an explicit async-safe lazy strategy:

    lazy="selectin"  — fires a separate async SELECT IN query.
                       Used for all to-one and to-many relationships that
                       need to be loaded with the parent object.

    lazy="joined"    → replaced with lazy="selectin" everywhere except
                       Message.author, where we keep "joined" BUT only
                       access it inside an open async session (websocket.py
                       _fetch_history already does this correctly).
                       To be safe, Message.author is also changed to
                       "selectin" so it never fires a sync load.

    lazy="dynamic"   — kept on User.messages and Channel.messages because
                       those relationships are never iterated directly;
                       they're only used as a base for explicit queries.
                       dynamic is a query-only strategy and never fires
                       an implicit load, so it is async-safe.

  Summary of changes
  ------------------
  Channel.group      lazy="selectin"   (was unset → "select")
  Channel.messages   lazy="dynamic"    (was unset → "select")
  Message.author     lazy="selectin"   (was "joined")
  Message.channel    lazy="selectin"   (was unset → "select")
  Group.created_by   lazy="selectin"   (was "joined")
  All others         unchanged
=============================================================================
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Association Table  —  User ↔ Group  (Many-to-Many)
# ---------------------------------------------------------------------------

user_group_association = Table(
    "user_group",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "joined_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)


# ---------------------------------------------------------------------------
# Model: User
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    username        = Column(String(50),  unique=True, nullable=False, index=True)
    email           = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_admin        = Column(Boolean, nullable=False, default=False)
    is_active       = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    groups = relationship(
        "Group",
        secondary=user_group_association,
        back_populates="members",
        lazy="selectin",        # async-safe: fires SELECT IN
    )
    messages = relationship(
        "Message",
        back_populates="author",
        cascade="all, delete-orphan",
        lazy="dynamic",         # never implicitly loaded; query-only
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} admin={self.is_admin}>"


# ---------------------------------------------------------------------------
# Model: Group
# ---------------------------------------------------------------------------

class Group(Base):
    __tablename__ = "groups"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    name          = Column(String(100), unique=True, nullable=False, index=True)
    description   = Column(Text, nullable=True)
    join_password = Column(String(255), nullable=False)
    is_read_only  = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    members = relationship(
        "User",
        secondary=user_group_association,
        back_populates="groups",
        lazy="selectin",        # async-safe
    )
    channels = relationship(
        "Channel",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",        # async-safe
    )
    # FIX: was lazy="joined" (sync) → now lazy="selectin" (async-safe)
    created_by = relationship(
        "User",
        foreign_keys=[created_by_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Group id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Model: Channel
# ---------------------------------------------------------------------------

class Channel(Base):
    __tablename__ = "channels"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    name       = Column(String(100), nullable=False)
    topic      = Column(String(255), nullable=True)
    group_id   = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # FIX: both were unset → defaulted to lazy="select" (sync) → MissingGreenlet
    group = relationship(
        "Group",
        back_populates="channels",
        lazy="selectin",        # async-safe
    )
    messages = relationship(
        "Message",
        back_populates="channel",
        cascade="all, delete-orphan",
        lazy="dynamic",         # never implicitly loaded; query-only
    )

    def __repr__(self) -> str:
        return f"<Channel id={self.id} name={self.name!r} group_id={self.group_id}>"


# ---------------------------------------------------------------------------
# Model: Message
# ---------------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    content    = Column(Text, nullable=False)

    is_deleted = Column(Boolean, nullable=False, default=False)
    edited_at  = Column(DateTime(timezone=True), nullable=True)
    is_edited  = Column(Boolean, nullable=False, default=False,
                        comment="True after the message has been edited at least once.")
    updated_at = Column(DateTime(timezone=True), nullable=True,
                        comment="UTC timestamp of the most recent edit.")

    author_id  = Column(UUID(as_uuid=True), ForeignKey("users.id",    ondelete="SET NULL"),  nullable=True,  index=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"),   nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False,  index=True)

    # FIX: was lazy="joined" (sync JOIN) → now lazy="selectin" (async-safe)
    author = relationship(
        "User",
        back_populates="messages",
        lazy="selectin",
    )
    # FIX: was unset → lazy="select" (sync) → now lazy="selectin" (async-safe)
    channel = relationship(
        "Channel",
        back_populates="messages",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} author_id={self.author_id} "
            f"channel_id={self.channel_id} edited={self.is_edited}>"
        )
