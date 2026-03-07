"""
=============================================================================
  database.py — Async SQLAlchemy engine + session factory (Neon / PostgreSQL)
=============================================================================
"""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base

# ---------------------------------------------------------------------------
# Connection URL
# ---------------------------------------------------------------------------
# Set DATABASE_URL in your .env file, e.g.:
#   DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.neon.tech/chatdb?sslmode=require
#
# asyncpg is the async driver; Neon requires sslmode=require.

DATABASE_URL: str = os.environ["DATABASE_URL"]

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
engine = create_async_engine(
    DATABASE_URL,
    echo=False,          # set True during development to log SQL
    pool_size=10,        # sufficient for ~100 concurrent users
    max_overflow=20,
    pool_pre_ping=True,  # detect stale connections (important for Neon's auto-suspend)
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects remain usable after commit in async context
)

# ---------------------------------------------------------------------------
# Dependency — use in FastAPI route handlers via `Depends(get_db)`
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Table creation helper (called once at startup; use Alembic for migrations)
# ---------------------------------------------------------------------------

async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
