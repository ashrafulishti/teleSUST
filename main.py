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
from routers import auth                   # ← Phase 2: Auth now live


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # ← tighten to your domain before production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth.router, prefix="/auth", tags=["Auth"])

# Uncomment as you build each Phase:
# from routers import groups, channels, messages, websocket
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


@app.get("/", tags=["Root"])
async def root():
    return {"message": "Welcome to teleSUST 👋", "docs": "/docs"}
