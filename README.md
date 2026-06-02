# FastAPI + Gunicorn + Redis Sessions + pgqueuer

A production-ready async Python web service template demonstrating:

- **FastAPI** served by **Gunicorn** (multi-process) with **Uvicorn** workers (async I/O per process)
- **Redis** as a shared, TTL-aware chat session store — consistent across all Gunicorn workers
- **pgqueuer** for durable, PostgreSQL-backed background job processing
- **uv** for dependency management and running all tooling

---

## Architecture

```
┌────────────────────────────────────────────────────┐
│  Gunicorn master process                           │
│  ├── UvicornWorker-1 ─┐                           │
│  ├── UvicornWorker-2 ─┼── Redis (session store)   │
│  └── UvicornWorker-N ─┘── PostgreSQL (pgq enqueue)│
└────────────────────────────────────────────────────┘
                │  enqueues jobs
                ▼
┌────────────────────────────────────────────────────┐
│  worker.py  (pgq run worker:main)                  │
│  └── asyncpg pool + Redis client                   │
│      → reads/writes sessions, sends AI replies     │
└────────────────────────────────────────────────────┘
```

**Why Redis for sessions?**
Gunicorn spawns multiple OS processes. A plain Python `dict` would be per-process — a user's session written by worker-1 would be invisible to worker-2. Redis is a shared in-memory store accessible to every worker, with native TTL expiry (no manual cleanup needed).

**Why pgqueuer?**
AI reply generation is slow. Instead of blocking the HTTP response, the web worker appends the user message to Redis, enqueues a `generate_reply` job, and returns immediately. The job worker picks it up asynchronously.

---

## Worker Process Allocation & Math

To ensure consistent performance under heavy loads (such as background CPU-stress workloads or concurrent LLM reasoning tasks), the project enforces a strict **60/40 CPU partitioning system** between the FastAPI REST API and pgqueuer background worker processes.

### 📐 The Allocation Formulas
For a system with a total count of CPU cores $C$:

1. **Web Workers ($W_{\text{web}}$)** (60% of CPU cores):
   $$W_{\text{web}} = \max(1, \text{round}(C \times 0.6))$$
   *Allocated to Gunicorn's `UvicornWorker` processes. Each worker handles high-concurrency async I/O requests.*

2. **Job Workers ($W_{\text{job}}$)** (40% of CPU cores):
   $$W_{\text{job}} = \max(1, \text{round}(C \times 0.4))$$
   *Allocated to pgqueuer background job consumer processes. These run computationally heavy or slow tasks.*

### 📊 Allocation Reference Table

| Total Cores ($C$) | Web Workers ($W_{\text{web}}$) | Job Workers ($W_{\text{job}}$) | Split Strategy Rationale |
| :---: | :---: | :---: | :--- |
| **1** | 1 | 1 | Basic concurrent execution of web routes and workers. |
| **2** | 1 | 1 | Isolated single core for web requests and single core for background worker. |
| **4** | 2 | 2 | Balance between active API endpoints and parallel task queues. |
| **8** | 5 | 3 | High-throughput API server with robust background processing capacity. |
| **16** | 10 | 6 | Massively concurrent API cluster with dedicated worker scaling. |

### 🧠 Architectural Rationale
- **Prevention of Starvation**: Under maximum CPU utilization (e.g., executing the `/jobs/enqueue` CPU stress job), the background pgqueuer processes will exhaust their assigned $W_{\text{job}}$ threads or processes. Because the remaining $W_{\text{web}}$ CPU share is physically isolated to separate web processes, the FastAPI API maintains full responsiveness to incoming health checks (`/` and `/info`) and liveness probes.
- **Context-Switching Minimization**: By allocating precise numbers of workers directly proportional to $C$, we prevent CPU thrashing and excessive OS-level context switching, maximizing real hardware throughput.
- **Isolated Lifespans**:
  - Web workers run under a Gunicorn master process using `UvicornWorker` for async network I/O.
  - Job workers run under the lightweight `pgq run` CLI, utilizing optimized `asyncpg` pools for ultra-low database polling latency.

---

## Project Structure

```
.
├── app/                    # Installable Python package (ASGI app + business logic)
│   ├── __init__.py
│   ├── main.py             # FastAPI application & route definitions
│   ├── cache.py            # Redis connection pool lifecycle (one client per worker)
│   ├── sessions.py         # Chat session CRUD operations (Redis-backed)
│   └── constants.py        # Centralized string keys, entrypoints, and pool configuration constants
├── tests/                  # pytest test suite
│   ├── conftest.py         # asyncio_mode=auto, shared fixtures
│   └── test_app.py         # Route-shape & integration tests (fully mocked)
├── worker.py               # pgqueuer background job consumer (entry: worker:main)
├── gunicorn.conf.py        # Gunicorn worker & timeout configuration
├── Makefile                # Developer workflow (install, dev, run, test, …)
├── pyproject.toml          # Project metadata, dependencies, build config
└── uv.lock                 # Locked dependency graph (commit this)
```

---

## Prerequisites

| Service | Version | Purpose |
|---|---|---|
| Python | ≥ 3.12 | Runtime |
| [uv](https://docs.astral.sh/uv/) | latest | Package manager & task runner |
| [Docker](https://docs.docker.com/get-docker/) | ≥ 24 | Dev infrastructure (Redis latest alpine, Postgres 18-alpine) |

Install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Quick Start (one command)

The fastest way to get the full stack running locally:

```bash
make install      # install Python deps
make start-dev    # start Redis + PostgreSQL (Docker) + web server + worker
```

`make start-dev` runs four steps automatically:

1. `docker compose up -d --wait` — starts Redis and PostgreSQL, blocks until their healthchecks pass
2. `make db-install` — creates the pgqueuer schema (idempotent, safe to re-run)
3. Uvicorn with `--reload` in the background → logs at `.pids/dev-web.log`
4. pgqueuer worker with `--log-level debug` in the background → logs at `.pids/dev-worker.log`

Then visit:
- **API**: `http://localhost:8000`
- **Interactive docs**: `http://localhost:8000/docs`
- **Live logs**: `make logs-dev`
- **Stop everything**: `make stop-dev`

```bash
make stop-dev     # kills Python processes + stops containers (data preserved)
make logs-dev     # tail web + worker logs live (Ctrl-C to exit)
```

> To wipe all data (including the PostgreSQL volume): `docker compose down -v`

---

## Manual Setup (without Docker)

If you already have Redis and PostgreSQL running locally, set the environment
variables and start each piece separately:

```bash
export DATABASE_URL=postgresql://santa:santa@localhost:5432/santa
export REDIS_URL=redis://:santa@localhost:6379/0

make install        # install Python deps
make db-install     # create pgqueuer schema
```

**Development (two terminals):**
```bash
make dev            # Terminal 1 — Uvicorn hot-reload
make dev-worker     # Terminal 2 — pgqueuer debug
```

**Production (Gunicorn, two terminals):**
```bash
make run            # Terminal 1 — Gunicorn + Uvicorn workers  [60% cores]
make worker         # Terminal 2 — pgqueuer consumer          [40% cores]
```

Or both in the background:
```bash
make start-all   # daemonises Gunicorn, backgrounds pgqueuer
make status      # show PIDs
make stop-all    # graceful shutdown
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://santa:santa@localhost:5432/santa` | PostgreSQL DSN (pgqueuer) |
| `REDIS_URL` | `redis://:santa@localhost:6379/0` | Redis DSN (session store) |
| `SESSION_TTL_SECONDS` | `86400` | Session TTL in seconds (24 h) |
| `APP_ENV` | `development` | Shown in `/info` response |
| `WEB_WORKERS` | 60% of CPU cores | Override Gunicorn worker count |
| `PORT` | `8000` | HTTP listen port |

---

## API Reference

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Liveness check, returns worker PID |
| `GET` | `/info` | Worker PID + APP_ENV |

### Chat Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat/sessions` | Create a new session |
| `GET` | `/chat/sessions?user_id=<id>` | List sessions for a user |
| `GET` | `/chat/sessions/{session_id}` | Fetch session + message history |
| `POST` | `/chat/sessions/{session_id}/messages` | Append a message (enqueues AI reply) |
| `DELETE` | `/chat/sessions/{session_id}` | Delete a session |

**Create session:**
```bash
curl -X POST http://localhost:8000/chat/sessions \
  -H "Content-Type: application/json" \
  -d '{"user_id": "alice"}'
```

**Send a message:**
```bash
curl -X POST http://localhost:8000/chat/sessions/<session_id>/messages \
  -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "Hello!"}'
```

### Jobs

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs/enqueue` | Manually enqueue any registered entrypoint |

---

## Redis Key Schema

| Key | Type | TTL | Purpose |
|---|---|---|---|
| `session:{uuid}` | String (JSON) | `SESSION_TTL_SECONDS` | Full session object |
| `user_sessions:{user_id}` | Set | none | Index of session IDs per user |

**Recommended Redis eviction policy:**
```
maxmemory-policy volatile-lru
```
Session keys have TTLs and are eligible for LRU eviction. User-index sets have no TTL and are never silently dropped. Stale index entries (from TTL-expired sessions) are lazily pruned during `list_sessions`.

---

## Background Jobs (pgqueuer)

Registered entrypoints in `worker.py`:

| Entrypoint | Trigger | Description |
|---|---|---|
| `generate_reply` | Auto (on message append) | Reads session from Redis, generates AI reply, appends it |
| `send_email` | Manual via `/jobs/enqueue` | Transactional email stub |
| `export_report` | Manual via `/jobs/enqueue` | Data export stub |
| `heartbeat_log` | Every minute (cron) | Confirms worker is alive |

To add a new job handler, register it with `@pgq.entrypoint("name")` in `worker.py`.

---

## Testing

```bash
make test           # run full test suite
uv run pytest tests/ -v -k "test_root"   # run a specific test
```

Tests use `httpx.AsyncClient` with ASGI transport — no real Redis or PostgreSQL required. All external dependencies are mocked via `unittest.mock`.

---

## Makefile Reference

```
# Dev stack (Docker-based, all-in-one)
make start-dev       ▶  Start infra + web + worker (background)
make stop-dev        ■  Stop app processes + Docker containers
make logs-dev        📋 Tail live logs (Ctrl-C to stop)

# Setup
make install         Install all deps with uv
make db-install      Create pgqueuer schema in PostgreSQL
make redis-check     Ping Redis to confirm connection

# Development (single terminal each)
make dev             Uvicorn, hot-reload (web only)
make dev-worker      pgqueuer consumer, debug logging

# Production
make run             Gunicorn + N UvicornWorkers  [60% cores]
make worker          pgqueuer consumer            [40% cores]
make start-all       Start both in background
make stop-all        Kill background processes
make status          Show running PIDs

# Quality
make test            Run pytest
make lint            Ruff check + format check
make fmt             Auto-format with ruff

# Cleanup
make clean           Remove .venv and caches
make clean-all       clean + docker compose down -v (wipes DB)
```
