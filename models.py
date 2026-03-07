"""
=============================================================================
  REAL-TIME GROUP CHAT PLATFORM — DATABASE MODELS
  Stack: FastAPI + SQLAlchemy (async) + PostgreSQL (Neon)
=============================================================================
"""

import uuid

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


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Association Table  —  User ↔ Group  (Many-to-Many)
# ---------------------------------------------------------------------------

user_group_association = Table(
    "user_group",
    Base.metadata,
    Column("user_id",   UUID(as_uuid=True), ForeignKey("users.id",  ondelete="CASCADE"), primary_key=True),
    Column("group_id",  UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("joined_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
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
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    groups   = relationship("Group",   secondary=user_group_association, back_populates="members", lazy="selectin")
    messages = relationship("Message", back_populates="author", cascade="all, delete-orphan",      lazy="dynamic")

    def __repr__(self):
        return f"<User id={self.id} username={self.username!r}>"


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

    members    = relationship("User",    secondary=user_group_association, back_populates="groups",              lazy="selectin")
    channels   = relationship("Channel", back_populates="group", cascade="all, delete-orphan",                  lazy="selectin")
    created_by = relationship("User",    foreign_keys=[created_by_id],                                          lazy="selectin")

    def __repr__(self):
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

    group    = relationship("Group",   back_populates="channels",                               lazy="selectin")
    messages = relationship("Message", back_populates="channel", cascade="all, delete-orphan",  lazy="dynamic")

    def __repr__(self):
        return f"<Channel id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Model: Message
# ---------------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    content    = Column(Text, nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    edited_at  = Column(DateTime(timezone=True), nullable=True)
    is_edited  = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    author_id  = Column(UUID(as_uuid=True), ForeignKey("users.id",    ondelete="SET NULL"),  nullable=True,  index=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"),   nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False,  index=True)

    author  = relationship("User",    back_populates="messages", lazy="selectin")
    channel = relationship("Channel", back_populates="messages", lazy="selectin")

    def __repr__(self):
        return f"<Message id={self.id} author_id={self.author_id} channel_id={self.channel_id}>"
