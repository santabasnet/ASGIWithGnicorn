"""
worker.py — pgqueuer background job consumer.

Policy & Technical Details:
- Execution Model:
  * Runs as a separate background process using the `pgq run` CLI.
- Shared Resources:
  * Initializes an asyncpg Connection pool and a redis.asyncio client on startup.
- Job Handlers:
  * Consumes pgqueuer tasks (e.g., generate_reply, send_email, export_report).

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import asyncio
import asyncpg
import redis.asyncio as aioredis
from pgqueuer import PgQueuer
from pgqueuer.models import Context, Job, Schedule

from app.sessions import append_message, get_session
from scripts.cpu_stress import run_stress


@asynccontextmanager
async def main():
    """
    pgqueuer factory (pgq run worker:main).

    Lifecycle:
      startup  → open resources, register handlers, yield PgQueuer
      shutdown → SIGTERM received, finally block closes connections
    """
    pid = os.getpid()
    print(f"[worker {pid}] starting up …")

    database_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("PG_DSN")
        or "postgresql://santa:santa@localhost:5432/santa"
    )
    redis_url = os.environ.get("REDIS_URL", "redis://:santa@localhost:6379/0")

    pool: asyncpg.Pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=8,
        command_timeout=60,
    )
    redis_client: aioredis.Redis = aioredis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=10,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    pgq_conn: asyncpg.Connection = await asyncpg.connect(database_url)

    resources: dict = {"pool": pool, "redis": redis_client}
    pgq = PgQueuer.from_asyncpg_connection(pgq_conn, resources=resources)

    # ── Job handlers ──────────────────────────────────────────────────────────

    @pgq.entrypoint("generate_reply", accepts_context=True)
    async def generate_reply(job: Job, ctx: Context) -> None:
        """
        Generate an AI reply and append it to the chat session.

        Payload: {"session_id": "<uuid>", "last_role": "user"}
        """
        redis: aioredis.Redis = ctx.resources["redis"]
        data = json.loads(job.payload or b"{}")
        session_id: str = data.get("session_id", "")

        if not session_id:
            print(f"[generate_reply] missing session_id — payload: {data}")
            return

        session = await get_session(redis, session_id)
        if session is None:
            print(
                f"[generate_reply] session {session_id} not found (expired or deleted)"
            )
            return

        last_user_msg = next(
            (
                m["content"]
                for m in reversed(session.get("messages", []))
                if m["role"] == "user"
            ),
            "",
        )

        # Replace with a real LLM call, e.g. await call_openai(session["messages"])
        reply = f"[mock reply to: '{last_user_msg}']"

        await append_message(redis, session_id, "assistant", reply)
        print(f"[generate_reply] session={session_id} reply appended (pid={pid})")

    @pgq.entrypoint("send_email")
    async def send_email(job: Job) -> None:
        """Payload: {"to": "...", "subject": "...", "body": "..."}"""
        data = json.loads(job.payload or b"{}")
        print(f"[send_email] → {data.get('to')} | {data.get('subject')} (pid={pid})")

    @pgq.entrypoint("export_report")
    async def export_report(job: Job) -> None:
        """Payload: {"report_id": "...", "format": "csv|json"}"""
        data = json.loads(job.payload or b"{}")
        print(
            f"[export_report] id={data.get('report_id')} fmt={data.get('format')} (pid={pid})"
        )

    @pgq.entrypoint("stress_cpu")
    async def stress_cpu(job: Job) -> None:
        """Payload: {"duration": 15}"""
        data = json.loads(job.payload or b"{}")
        duration = int(data.get("duration", 15))
        print(
            f"[stress_cpu] starting CPU stress test for {duration} seconds... (pid={pid})"
        )
        # Run run_stress in a separate OS thread to avoid blocking pgqueuer event loop
        await asyncio.to_thread(run_stress, duration)
        print(f"[stress_cpu] CPU stress test finished (pid={pid})")

    @pgq.schedule("heartbeat_log", "* * * * *")
    async def heartbeat_log(schedule: Schedule) -> None:
        print(f"[heartbeat] worker alive (pid={pid})")

    try:
        yield pgq
    finally:
        await redis_client.aclose()
        await pool.close()
        await pgq_conn.close()
        print(f"[worker {pid}] shut down")
