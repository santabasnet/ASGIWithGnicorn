"""
app/sessions.py — Chatbot session store backed by Redis.

Policy & Technical Details:
- Key Schema:
  * session:{uuid} (String) — Holds JSON-serialized session dictionary, TTL = SESSION_TTL.
  * user_sessions:{user_id} (Set) — Set of session IDs associated with the user (no TTL).
- Eviction Policy:
  * Recommended: maxmemory-policy volatile-lru.
  * session:{uuid} has a TTL and is eligible for eviction.
  * user_sessions:{user_id} does not have a TTL and is never silently evicted.
- Lifecycle:
  * creation/appends update the session object and re-apply SESSION_TTL.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from app.cache import SESSION_TTL

# ── Constants ─────────────────────────────────────────────────────────────────
KEY_SESSION_ID = "session_id"
KEY_USER_ID = "user_id"
KEY_MESSAGES = "messages"
KEY_METADATA = "metadata"
KEY_CREATED_AT = "created_at"
KEY_UPDATED_AT = "updated_at"
KEY_ROLE = "role"
KEY_CONTENT = "content"
KEY_TS = "ts"

DEFAULT_LIMIT = 20
DEFAULT_OFFSET = 0


# ── Key helpers ───────────────────────────────────────────────────────────────


def _session_key(session_id: str) -> str:
    return f"session:{session_id}"


def _user_index_key(user_id: str) -> str:
    return f"user_sessions:{user_id}"


# ── Session operations ────────────────────────────────────────────────────────


async def create_session(
    redis: aioredis.Redis,
    user_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new empty session, persist it to Redis and return it."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    session: dict[str, Any] = {
        KEY_SESSION_ID: session_id,
        KEY_USER_ID: user_id,
        KEY_MESSAGES: [],
        KEY_METADATA: metadata or {},
        KEY_CREATED_AT: now,
        KEY_UPDATED_AT: now,
    }

    pipe = redis.pipeline()
    pipe.set(_session_key(session_id), json.dumps(session), ex=SESSION_TTL)
    pipe.sadd(_user_index_key(user_id), session_id)
    await pipe.execute()

    return session


async def get_session(
    redis: aioredis.Redis,
    session_id: str,
) -> dict[str, Any] | None:
    """Fetch a session by ID.  Returns None if not found or expired."""
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return None
    return json.loads(raw)


async def append_message(
    redis: aioredis.Redis,
    session_id: str,
    role: str,  # "user" | "assistant" | "system"
    content: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Append one message to the session's message list and reset the TTL.
    Returns the updated session or None if session_id was not found / expired.
    """
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return None

    session: dict[str, Any] = json.loads(raw)

    message: dict[str, Any] = {
        KEY_ROLE: role,
        KEY_CONTENT: content,
        KEY_TS: datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        message.update(extra)

    session[KEY_MESSAGES].append(message)
    session[KEY_UPDATED_AT] = datetime.now(timezone.utc).isoformat()

    # Overwrite the key and reset the TTL (activity refreshes the session).
    await redis.set(_session_key(session_id), json.dumps(session), ex=SESSION_TTL)
    return session


async def list_sessions(
    redis: aioredis.Redis,
    user_id: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
) -> list[dict[str, Any]]:
    """
    List sessions for a user, sorted by updated_at descending.

    Stale IDs in the user index (session key expired by Redis eviction) are
    silently filtered out.  A fan-out GET is used for simplicity; for very
    large user histories use a Redis Sorted Set keyed by updated_at score.
    """
    session_ids: set[str] = await redis.smembers(_user_index_key(user_id))
    if not session_ids:
        return []

    # Fan-out: fetch all sessions in a single pipeline round-trip.
    pipe = redis.pipeline()
    ordered_ids = list(session_ids)
    for sid in ordered_ids:
        pipe.get(_session_key(sid))
    raws = await pipe.execute()

    sessions: list[dict[str, Any]] = [
        json.loads(raw) for raw in raws if raw is not None
    ]
    stale_ids: list[str] = [sid for sid, raw in zip(ordered_ids, raws) if raw is None]

    # Clean up stale index entries (fire-and-forget).
    if stale_ids:
        await redis.srem(_user_index_key(user_id), *stale_ids)

    # Sort by updated_at descending then paginate.
    sessions.sort(key=lambda s: s[KEY_UPDATED_AT], reverse=True)
    return sessions[offset : offset + limit]


async def delete_session(redis: aioredis.Redis, session_id: str) -> bool:
    """
    Delete a session.  Returns True if the key existed and was deleted.
    Also removes the ID from the user-index set.
    """
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return False

    session: dict[str, Any] = json.loads(raw)
    user_id: str = session.get(KEY_USER_ID, "")

    pipe = redis.pipeline()
    pipe.delete(_session_key(session_id))
    if user_id:
        pipe.srem(_user_index_key(user_id), session_id)
    results = await pipe.execute()

    return bool(results[0])  # DEL returns number of keys deleted
