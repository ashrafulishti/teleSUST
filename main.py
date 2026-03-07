"""
=============================================================================
  main.py — FastAPI application entry point
  teleSUST Real-Time Group Chat Platform
=============================================================================
  CORS origins are now read from the ALLOWED_ORIGINS environment variable
  instead of being hardcoded. No hosting URLs live in this file.

  Format in .env:
      ALLOWED_ORIGINS=https://tele-sust.vercel.app,http://localhost:5173
  Multiple origins are comma-separated, no spaces.
=============================================================================
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import create_tables
from routers import auth, channels, groups, messages, websocket


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("⚙️  teleSUST: connecting to database and creating tables...")
    await create_tables()
    print("✅  teleSUST: database tables verified / created successfully.")
    yield
    print("🔌  teleSUST: shutting down gracefully.")


app = FastAPI(
    title="teleSUST",
    description="A lightweight, secure, real-time group chat platform.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Origins are read from ALLOWED_ORIGINS env var — comma-separated, no spaces.
# Falls back to localhost only if the variable is not set (local dev safety net).
#
# allow_origins=["*"] is INVALID when allow_credentials=True — the browser
# spec forbids wildcard origins on credentialed requests.

_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins     = allowed_origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router,      prefix="/auth",                   tags=["Auth"])
app.include_router(groups.router,    prefix="/groups",                 tags=["Groups"])
app.include_router(
    channels.router,
    prefix="/groups/{group_id}/channels",
    tags=["Channels"],
)
app.include_router(messages.router,  prefix="/messages",               tags=["Messages"])
app.include_router(websocket.router, prefix="/ws",                     tags=["WebSocket"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "app": "teleSUST"}


@app.get("/", tags=["Root"])
async def root():
    return {"message": "Welcome to teleSUST 👋", "docs": "/docs"}
