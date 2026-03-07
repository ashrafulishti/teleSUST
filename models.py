"""
=============================================================================
  REAL-TIME GROUP CHAT PLATFORM — DATABASE MODELS
  Stack: FastAPI + SQLAlchemy (async) + PostgreSQL (Neon)
=============================================================================
  Phase 6 changes (Message model only):
    + is_edited  Boolean  default False  — set True on every PUT /messages
    + updated_at DateTime nullable       — timestamp of last edit
  All other models are unchanged.
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
    """Shared declarative base for all models."""
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
    """
    Represents a registered user.

    Relationships
    -------------
    • groups   : Many-to-Many via user_group_association
    • messages : One-to-Many
    """

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
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
        lazy="selectin",
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
    • members  : Many-to-Many → User
    • channels : One-to-Many  → Channel
    """

    __tablename__ = "groups"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    name          = Column(String(100), unique=True, nullable=False, index=True)
    description   = Column(Text, nullable=True)
    join_password = Column(String(255), nullable=False)
    is_read_only  = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    members  = relationship("User",    secondary=user_group_association, back_populates="groups",   lazy="selectin")
    channels = relationship("Channel", back_populates="group", cascade="all, delete-orphan",        lazy="selectin")
    created_by = relationship("User",  foreign_keys=[created_by_id],                                lazy="joined")

    def __repr__(self) -> str:
        return f"<Group id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Model: Channel
# ---------------------------------------------------------------------------

class Channel(Base):
    """
    A sub-space inside a Group where messages are posted.

    Relationships
    -------------
    • group    : Many-to-One → Group
    • messages : One-to-Many → Message
    """

    __tablename__ = "channels"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    name       = Column(String(100), nullable=False)
    topic      = Column(String(255), nullable=True)
    group_id   = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    group    = relationship("Group",   back_populates="channels")
    messages = relationship("Message", back_populates="channel", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Channel id={self.id} name={self.name!r} group_id={self.group_id}>"


# ---------------------------------------------------------------------------
# Model: Message
# ---------------------------------------------------------------------------

class Message(Base):
    """
    A single chat message sent by a User inside a Channel.

    Phase 6 additions
    -----------------
    is_edited  : set to True whenever content is updated via PUT /messages
    updated_at : UTC timestamp of the most recent edit; None until first edit

    Relationships
    -------------
    • author  : Many-to-One → User
    • channel : Many-to-One → Channel
    """

    __tablename__ = "messages"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    content    = Column(Text, nullable=False)

    # ── Lifecycle flags ───────────────────────────────────────────────────────
    is_deleted = Column(Boolean, nullable=False, default=False)
    # edited_at is the ORIGINAL field kept from Phase 5 (backward compat)
    edited_at  = Column(DateTime(timezone=True), nullable=True)

    # ── Phase 6: new fields ───────────────────────────────────────────────────
    is_edited  = Column(Boolean, nullable=False, default=False,
                        comment="True after the message has been edited at least once.")
    updated_at = Column(DateTime(timezone=True), nullable=True,
                        comment="UTC timestamp of the most recent edit.")

    # ── Foreign Keys ──────────────────────────────────────────────────────────
    author_id  = Column(UUID(as_uuid=True), ForeignKey("users.id",    ondelete="SET NULL"),  nullable=True,  index=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"),   nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False,  index=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    author  = relationship("User",    back_populates="messages", lazy="joined")
    channel = relationship("Channel", back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} author_id={self.author_id} "
            f"channel_id={self.channel_id} edited={self.is_edited}>"
        )
