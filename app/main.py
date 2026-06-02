"""
app/main.py — FastAPI ASGI application.

Policy & Technical Details:
- Process Model:
  * Gunicorn master process manages multiple Uvicorn worker processes.
- Resource Lifecycle:
  * FastAPI lifespan opens Redis client pool and dedicated pgqueuer asyncpg enqueue connection.
- Session Management:
  * All session read/write requests are routed to Redis via session dependencies.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.cache import close_redis, create_redis, get_redis
from app.constants import (
    DB_POOL_COMMAND_TIMEOUT,
    DB_POOL_MAX_SIZE_WEB,
    DB_POOL_MIN_SIZE,
    DEFAULT_APP_ENV,
    DEFAULT_DATABASE_URL,
    DEFAULT_LIMIT,
    DEFAULT_OFFSET,
    ENTRYPOINT_GENERATE_REPLY,
    HEADER_X_PROCESS_TIME_MS,
    HEADER_X_WORKER_PID,
    KEY_CONTENT,
    KEY_LAST_ROLE,
    KEY_ROLE,
    KEY_SESSION_ID,
    ROLE_USER,
)
from app.sessions import (
    append_message,
    create_session,
    delete_session,
    get_session,
    list_sessions,
)
from pgqueuer.db import AsyncpgDriver
from pgqueuer.queries import Queries


# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs ONCE per Uvicorn worker process.

    startup:
      • Open Redis client (session store)
      • Open a dedicated asyncpg connection for pgqueuer job enqueueing
    shutdown:
      • Close both cleanly
    """
    pid = os.getpid()
    print(f"[web worker {pid}] starting up …")

    await create_redis()

    db_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("PG_DSN")
        or DEFAULT_DATABASE_URL
    )
    pool: asyncpg.Pool = await asyncpg.create_pool(
        db_url,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE_WEB,
        command_timeout=DB_POOL_COMMAND_TIMEOUT,
    )
    app.state.pool = pool
    app.state.pid = pid

    yield

    await pool.close()
    await close_redis()
    print(f"[web worker {pid}] shut down")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="FastAPI + Gunicorn + Redis Sessions",
    description=(
        "FastAPI served by Gunicorn/Uvicorn workers with a Redis-backed chat "
        "session store and a pgqueuer background job worker."
    ),
    version="0.3.0",
    lifespan=lifespan,
)


# ── Middleware ────────────────────────────────────────────────────────────────
@app.middleware("http")
async def add_server_headers(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers[HEADER_X_WORKER_PID] = str(os.getpid())
    response.headers[HEADER_X_PROCESS_TIME_MS] = f"{(time.perf_counter() - t0) * 1000:.2f}"
    return response


# ── Dependencies ──────────────────────────────────────────────────────────────
async def redis_dep(request: Request) -> aioredis.Redis:
    return await get_redis()


async def queries_dep(request: Request):
    if hasattr(request.app.state, "queries"):
        yield request.app.state.queries
        return
    async with request.app.state.pool.acquire() as conn:
        yield Queries(AsyncpgDriver(conn))


# ══════════════════════════════════════════════════════════════════════════════
# Health
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "worker_pid": os.getpid()}


@app.get("/info", tags=["health"])
async def info():
    return {
        "worker_pid": os.getpid(),
        "env": os.environ.get("APP_ENV", DEFAULT_APP_ENV),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Chat session routes
# ══════════════════════════════════════════════════════════════════════════════


class CreateSessionRequest(BaseModel):
    user_id: str
    metadata: dict = {}


class MessageRequest(BaseModel):
    role: str = ROLE_USER  # "user" | "assistant" | "system"
    content: str


@app.post("/chat/sessions", tags=["chat"], status_code=201)
async def new_session(
    body: CreateSessionRequest,
    redis: aioredis.Redis = Depends(redis_dep),
):
    """Create a new chatbot session. Sessions auto-expire after SESSION_TTL_SECONDS."""
    return await create_session(redis, body.user_id, body.metadata)


@app.get("/chat/sessions/{session_id}", tags=["chat"])
async def read_session(
    session_id: str,
    redis: aioredis.Redis = Depends(redis_dep),
):
    """Fetch a session and its full message history."""
    session = await get_session(redis, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.post("/chat/sessions/{session_id}/messages", tags=["chat"])
async def add_message(
    session_id: str,
    body: MessageRequest,
    redis: aioredis.Redis = Depends(redis_dep),
    queries: Queries = Depends(queries_dep),
):
    """
    Append a message to a session and enqueue a background 'generate_reply' job.

    Flow:
      1. Store the message in Redis (visible to all workers instantly)
      2. Enqueue 'generate_reply' — pgqueuer worker picks it up asynchronously
      3. Return immediately (non-blocking)
    """
    updated = await append_message(redis, session_id, body.role, body.content)
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")

    payload = json.dumps({KEY_SESSION_ID: session_id, KEY_LAST_ROLE: body.role}).encode()
    await queries.enqueue([ENTRYPOINT_GENERATE_REPLY], [payload], [0])

    return {
        KEY_SESSION_ID: session_id,
        "appended": {KEY_ROLE: body.role, KEY_CONTENT: body.content},
        "job": f"{ENTRYPOINT_GENERATE_REPLY} queued",
        "worker_pid": os.getpid(),
    }


@app.get("/chat/sessions", tags=["chat"])
async def user_sessions(
    user_id: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    redis: aioredis.Redis = Depends(redis_dep),
):
    """List all active sessions for a user, sorted by last activity (newest first)."""
    return await list_sessions(redis, user_id, limit, offset)


@app.delete("/chat/sessions/{session_id}", tags=["chat"], status_code=204)
async def remove_session(
    session_id: str,
    redis: aioredis.Redis = Depends(redis_dep),
):
    if not await delete_session(redis, session_id):
        raise HTTPException(status_code=404, detail="Session not found")


# ══════════════════════════════════════════════════════════════════════════════
# Job enqueueing
# ══════════════════════════════════════════════════════════════════════════════


class EnqueueRequest(BaseModel):
    entrypoint: str
    payload: str = ""
    priority: int = 0


@app.post("/jobs/enqueue", tags=["jobs"])
async def enqueue_job(
    body: EnqueueRequest,
    queries: Queries = Depends(queries_dep),
):
    """Manually enqueue any registered pgqueuer entrypoint."""
    ids = await queries.enqueue(
        [body.entrypoint],
        [body.payload.encode()],
        [body.priority],
    )
    return {"queued": True, "job_ids": [str(i) for i in ids], "worker_pid": os.getpid()}
