"""
app/cache.py — redis.asyncio connection pool, shared across all FastAPI Uvicorn workers.

Policy & Technical Details:
- Connection Lifecycle:
  * Initialised once during the FastAPI app lifespan startup.
  * Closed cleanly on Uvicorn worker process shutdown.
- Thread/Coroutine Safety:
  * Module-level singleton client (_redis) shared per worker process.
- Environment Variables:
  * REDIS_URL: Redis DSN (default: redis://localhost:6379/0).
  * SESSION_TTL_SECONDS: Session expiration time (default: 86400).

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import os

import redis.asyncio as aioredis

# One client per Uvicorn worker process (module-level singleton).
_redis: aioredis.Redis | None = None

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
SESSION_TTL: int = int(os.environ.get("SESSION_TTL_SECONDS", 86_400))


def get_redis_url() -> str:
    return os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)


# ── Lifecycle ─────────────────────────────────────────────────────────────────


async def create_redis() -> aioredis.Redis:
    """Create and cache the redis.asyncio client for this worker process."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            get_redis_url(),
            encoding="utf-8",
            decode_responses=True,  # all responses are str, not bytes
            max_connections=20,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _redis


async def close_redis() -> None:
    """Gracefully close the Redis client on worker shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency: returns the active Redis client (must be initialised first)."""
    if _redis is None:
        raise RuntimeError("Redis client not initialised — check lifespan setup")
    return _redis
