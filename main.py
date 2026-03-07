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
# allow_origins=["*"] is INVALID when allow_credentials=True.
# The browser spec forbids wildcard origins on credentialed requests — it
# silently drops the request body, FastAPI receives None, and Pydantic throws
# "Input should be a valid dictionary or object to extract fields from."
# Solution: list every domain the frontend is actually served from.

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tele-sust.vercel.app",   # production frontend (Vercel)
        "http://localhost:5173",           # local Vite dev server
        "http://127.0.0.1:5173",           # local Vite dev server (alternate)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
