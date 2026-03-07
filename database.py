"""
=============================================================================
  database.py — Async SQLAlchemy engine + session factory (Neon / PostgreSQL)
=============================================================================
  FIX: asyncpg does NOT accept ?sslmode=require in the connection string.
  It requires a Python ssl.SSLContext object passed via connect_args instead.
=============================================================================
"""

import os
import ssl
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base


# ---------------------------------------------------------------------------
# Connection URL
# ---------------------------------------------------------------------------
# In your .env / Render dashboard set DATABASE_URL to the plain form:
#
#   DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/chatdb
#
# ⚠️  Do NOT append ?sslmode=require — asyncpg will raise:
#       TypeError: connect() got an unexpected keyword argument 'sslmode'
#     SSL is handled below via ssl_context instead.

_raw_url: str = os.environ["DATABASE_URL"]

# Strip any accidental ?sslmode=... suffix so the app doesn't crash even if
# someone pastes in the psycopg2-style URL from the Neon dashboard.
DATABASE_URL = _raw_url.split("?")[0]

# Ensure the scheme is the asyncpg variant (guard against copy-paste mistakes).
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)


# ---------------------------------------------------------------------------
# SSL context
# ---------------------------------------------------------------------------
# Neon requires an encrypted connection but uses certificates that asyncpg's
# strict verifier can reject depending on the runtime environment.
#
# ssl.PROTOCOL_TLS_CLIENT  — enforces TLS (no plain-text fallback)
# check_hostname = False   — skips hostname verification (safe for Neon's
#                            pooled endpoints which use SNI, not hostname match)
# verify_mode = CERT_NONE  — skips cert-chain verification
#                            (acceptable here: traffic is still encrypted;
#                             only the identity check is relaxed)
#
# If your security policy requires full cert verification, replace with:
#   ssl_context.verify_mode = ssl.CERT_REQUIRED
#   ssl_context.check_hostname = True
#   ssl_context.load_default_certs()

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

engine = create_async_engine(
    DATABASE_URL,
    echo=False,             # flip to True locally to log SQL statements
    pool_size=10,           # sufficient for ~100 concurrent users
    max_overflow=20,
    pool_pre_ping=True,     # detects stale connections (critical for Neon's
                            # auto-suspend feature on free-tier branches)
    connect_args={
        "ssl": ssl_context  # ← the correct way to pass SSL to asyncpg
    },
)


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects stay usable after commit in async context
)


# ---------------------------------------------------------------------------
# Dependency — inject into FastAPI routes via Depends(get_db)
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
# Table creation helper — called once at startup in main.py lifespan
# (safe to re-run: SQLAlchemy uses CREATE TABLE IF NOT EXISTS)
# ---------------------------------------------------------------------------

async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
