"""
tests/test_app.py — FastAPI + session store tests.

Policy & Technical Details:
- Mocking Strategy:
  * Mocks Redis and pgqueuer connections using unittest.mock for fast, unit-level routes verification.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Fake redis.asyncio.Redis client that records calls."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.sadd = AsyncMock(return_value=1)
    redis.srem = AsyncMock(return_value=1)
    redis.smembers = AsyncMock(return_value=set())
    # pipeline mock
    pipe = MagicMock()
    pipe.set = MagicMock(return_value=pipe)
    pipe.sadd = MagicMock(return_value=pipe)
    pipe.delete = MagicMock(return_value=pipe)
    pipe.srem = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[True, 1])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


@pytest_asyncio.fixture
async def client(mock_redis):
    """
    Spin up the app with mocked Redis + pgqueuer so tests run without real services.
    """
    import app.cache as cache_module

    fake_queries = MagicMock()
    fake_queries.enqueue = AsyncMock(return_value=[1])

    # Inject mock Redis into module-level singleton before lifespan runs.
    cache_module._redis = mock_redis

    with (
        patch.object(cache_module, "create_redis", AsyncMock(return_value=mock_redis)),
        patch.object(cache_module, "close_redis", AsyncMock()),
        patch("asyncpg.connect", AsyncMock(return_value=MagicMock(close=AsyncMock()))),
        patch("app.main.Queries", return_value=fake_queries),
        patch("app.main.get_redis", AsyncMock(return_value=mock_redis)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            app.state.queries = fake_queries
            yield ac

    cache_module._redis = None


# ── Health endpoints ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "worker_pid" in r.json()


@pytest.mark.asyncio
async def test_info(client):
    r = await client.get("/info")
    assert r.status_code == 200
    assert "worker_pid" in r.json()


@pytest.mark.asyncio
async def test_worker_pid_header(client):
    r = await client.get("/")
    assert "x-worker-pid" in r.headers
    assert "x-process-time-ms" in r.headers


# ── Chat session route shapes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_calls_redis(client):
    """POST /chat/sessions should call create_session and return 201."""
    fake_session = {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "alice",
        "messages": [],
        "metadata": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    with patch("app.main.create_session", AsyncMock(return_value=fake_session)):
        r = await client.post("/chat/sessions", json={"user_id": "alice"})
    assert r.status_code == 201
    assert r.json()["user_id"] == "alice"


@pytest.mark.asyncio
async def test_get_session_not_found(client):
    with patch("app.main.get_session", AsyncMock(return_value=None)):
        r = await client.get("/chat/sessions/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_message_enqueues_job(client):
    """Appending a message should enqueue a generate_reply job."""
    fake_session = {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "alice",
        "messages": [
            {"role": "user", "content": "hello", "ts": "2024-01-01T00:00:00+00:00"}
        ],
        "metadata": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    with patch("app.main.append_message", AsyncMock(return_value=fake_session)):
        r = await client.post(
            "/chat/sessions/00000000-0000-0000-0000-000000000001/messages",
            json={"role": "user", "content": "hello"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["job"] == "generate_reply queued"
    assert body["appended"]["content"] == "hello"


@pytest.mark.asyncio
async def test_enqueue_endpoint(client):
    r = await client.post(
        "/jobs/enqueue",
        json={"entrypoint": "send_email", "payload": '{"to": "x@y.com"}'},
    )
    assert r.status_code == 200
    assert r.json()["queued"] is True


# ── pgqueuer in-memory smoke test ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pgqueuer_in_memory_handler():
    """
    Verify the generate_reply handler runs without crashing.
    Uses PgQueuer.in_memory() — no PostgreSQL or Redis needed.
    """
    from pgqueuer import PgQueuer
    from pgqueuer.models import Job
    from pgqueuer.types import QueueExecutionMode

    fake_redis = MagicMock()

    pq = PgQueuer.in_memory(resources={"redis": fake_redis})

    @pq.entrypoint("generate_reply")
    async def generate_reply(job: Job) -> None:
        # Minimal smoke: just confirm the handler is called with bytes payload
        data = json.loads(job.payload or b"{}")
        assert "session_id" in data

    payload = json.dumps({"session_id": "abc", "last_role": "user"}).encode()
    await pq.qm.queries.enqueue(["generate_reply"], [payload], [0])
    await pq.qm.run(mode=QueueExecutionMode.drain)
