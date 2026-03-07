"""
=============================================================================
  REAL-TIME GROUP CHAT PLATFORM — DATABASE MODELS (Phase 1)
  Stack: FastAPI + SQLAlchemy (async) + PostgreSQL (Neon)
=============================================================================
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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
    """Shared declarative base for all models."""
    pass


# ---------------------------------------------------------------------------
# Association Table  —  User ↔ Group  (Many-to-Many)
# ---------------------------------------------------------------------------
# A user can belong to many groups; a group can have many users.
# This join table lives between them and carries no extra columns,
# so a plain Table object (not a full model class) is the right choice.

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
    """
    Represents a registered user.

    Relationships
    -------------
    • groups   : Many-to-Many via user_group_association
                 A user can be a member of multiple groups.
    • messages : One-to-Many
                 A user can author many messages.
    """

    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)

    # Stored as a bcrypt hash — NEVER store plain text passwords.
    hashed_password = Column(String(255), nullable=False)

    # Grants access to admin-only API routes when True.
    is_admin = Column(Boolean, nullable=False, default=False)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────────
    groups = relationship(
        "Group",
        secondary=user_group_association,
        back_populates="members",
        lazy="selectin",       # async-friendly eager load
    )
    messages = relationship(
        "Message",
        back_populates="author",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} admin={self.is_admin}>"


# ---------------------------------------------------------------------------
# Model: Group
# ---------------------------------------------------------------------------

class Group(Base):
    """
    A top-level community space (e.g. "Study", "Announcement", "Off-Topic").

    Relationships
    -------------
    • members  : Many-to-Many → User  (via user_group_association)
    • channels : One-to-Many → Channel
                 Every group owns one or more channels (e.g. #general).
    """

    __tablename__ = "groups"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Hashed at the service layer (bcrypt) before being stored.
    # Users must supply the correct plain-text value to join.
    join_password = Column(String(255), nullable=False)

    # When True only admins can post; useful for "Announcement" groups.
    is_read_only = Column(Boolean, nullable=False, default=False)

    created_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Relationships ────────────────────────────────────────────────────────
    members = relationship(
        "User",
        secondary=user_group_association,
        back_populates="groups",
        lazy="selectin",
    )
    channels = relationship(
        "Channel",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Group id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Model: Channel
# ---------------------------------------------------------------------------

class Channel(Base):
    """
    A sub-space inside a Group where messages are actually posted
    (e.g. Group "Study" → Channels "#math", "#physics").

    Relationships
    -------------
    • group    : Many-to-One → Group
    • messages : One-to-Many → Message
    """

    __tablename__ = "channels"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    name = Column(String(100), nullable=False)          # e.g. "general"
    topic = Column(String(255), nullable=True)           # optional channel description

    group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Relationships ────────────────────────────────────────────────────────
    group = relationship("Group", back_populates="channels")
    messages = relationship(
        "Message",
        back_populates="channel",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return f"<Channel id={self.id} name={self.name!r} group_id={self.group_id}>"


# ---------------------------------------------------------------------------
# Model: Message
# ---------------------------------------------------------------------------

class Message(Base):
    """
    A single chat message sent by a User inside a Channel.

    Relationships
    -------------
    • author  : Many-to-One → User
    • channel : Many-to-One → Channel
    """

    __tablename__ = "messages"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    content = Column(Text, nullable=False)

    # Soft-delete / edit tracking
    is_deleted = Column(Boolean, nullable=False, default=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)

    # ── Foreign Keys ─────────────────────────────────────────────────────────
    author_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,      # keep message record even if user is deleted
        index=True,
    )
    channel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # ── Relationships ────────────────────────────────────────────────────────
    author = relationship("User", back_populates="messages", lazy="joined")
    channel = relationship("Channel", back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} author_id={self.author_id} "
            f"channel_id={self.channel_id}>"
        )
