"""
=============================================================================
  main.py — FastAPI application entry point
  teleSUST Real-Time Group Chat Platform
=============================================================================
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import create_tables


# ---------------------------------------------------------------------------
# Lifespan — replaces deprecated @app.on_event("startup")
# ---------------------------------------------------------------------------
# Everything inside the `async with` block (before `yield`) runs at startup.
# Everything after `yield` runs at shutdown.
# create_tables() calls Base.metadata.create_all — safe to run every boot
# because SQLAlchemy uses CREATE TABLE IF NOT EXISTS under the hood.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    print("⚙️  teleSUST: connecting to database and creating tables...")
    await create_tables()
    print("✅  teleSUST: database tables verified / created successfully.")
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    print("🔌  teleSUST: shutting down gracefully.")


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="teleSUST",
    description="A lightweight, secure, real-time group chat platform.",
    version="1.0.0",
    lifespan=lifespan,
    # Disable /docs and /redoc on production by setting these to None.
    # Keep them on during development for easy endpoint testing.
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------
# Restrict `allow_origins` to your actual frontend URL before going to prod.
# e.g. ["https://telesust.onrender.com"] or your custom domain.

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # ← tighten this before production launch
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
# Uncomment each line as you build the corresponding Phase.
#
# from routers import auth, groups, channels, messages, websocket
#
# app.include_router(auth.router,      prefix="/auth",      tags=["Auth"])
# app.include_router(groups.router,    prefix="/groups",    tags=["Groups"])
# app.include_router(channels.router,  prefix="/channels",  tags=["Channels"])
# app.include_router(messages.router,  prefix="/messages",  tags=["Messages"])
# app.include_router(websocket.router, prefix="/ws",        tags=["WebSocket"])


# ---------------------------------------------------------------------------
# Health check — Render pings this to confirm the service is alive
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "app": "teleSUST"}


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Welcome to teleSUST 👋",
        "docs": "/docs",
    }
