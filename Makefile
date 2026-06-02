# ─────────────────────────────────────────────────────────────────────────────
# Makefile — FastAPI (Gunicorn/Uvicorn) + pgqueuer consumer, managed by uv
#
# Process split:  60 % cores → Gunicorn web workers
#                 40 % cores → pgqueuer job consumer(s)
#
# Quick start:
#   Terminal 1:  make run        # web server
#   Terminal 2:  make worker     # background jobs
#
# Or run both in the background:
#   make start-all
#   make stop-all
# ─────────────────────────────────────────────────────────────────────────────

# ── Config ────────────────────────────────────────────────────────────────────
APP              := app.main:app
HOST             ?= 0.0.0.0
PORT             ?= 8000
LOG_LEVEL        ?= info
DATABASE_URL     ?= postgresql://santa:santa@localhost:5432/santa
REDIS_URL        ?= redis://:santa@localhost:6379/0
SESSION_TTL_SECONDS ?= 86400

# 60 % of cores → web, at least 1.
TOTAL_CORES      := $(shell python -c "import os; print(os.cpu_count())" 2>/dev/null || echo 2)
WEB_WORKERS      ?= $(shell python -c "print(max(1, round($(TOTAL_CORES) * 0.6)))" 2>/dev/null || echo 1)

# pgqueuer consumer config
PGQ_ENTRY        ?= worker:main
PGQ_LOG_LEVEL    ?= $(LOG_LEVEL)

UV               := uv
GUNICORN         := $(UV) run gunicorn
UVICORN          := $(UV) run uvicorn
PGQ              := $(UV) run pgq
REDIS_EXPORTS    := REDIS_URL=$(REDIS_URL) SESSION_TTL_SECONDS=$(SESSION_TTL_SECONDS)
DC               := docker compose

# PID & log files (.pids/ directory)
PID_DIR          := .pids
WEB_PID          := $(PID_DIR)/web.pid
WORKER_PID_FILE  := $(PID_DIR)/worker.pid
DEV_WEB_PID      := $(PID_DIR)/dev-web.pid
DEV_WORKER_PID   := $(PID_DIR)/dev-worker.pid
DEV_WEB_LOG      := $(PID_DIR)/dev-web.log
DEV_WORKER_LOG   := $(PID_DIR)/dev-worker.log

.DEFAULT_GOAL := help

# ── Help ──────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  FastAPI + pgqueuer  —  uv  |  60/40 core split"
	@echo ""
	@echo "  ── Setup ───────────────────────────────────────"
	@echo "  make install         Install all deps with uv"
	@echo "  make db-install      Create pgqueuer schema in PostgreSQL"
	@echo "  make redis-check     Ping Redis to confirm connection"
	@echo ""
	@echo "  ── Dev stack (docker-compose + app + worker) ───"
	@echo "  make start-dev       ▶  Start infra + web + worker (background)"
	@echo "  make stop-dev        ■  Stop app processes + docker containers"
	@echo "  make logs-dev        📋 Tail live logs (web & worker)"
	@echo ""
	@echo "  ── Development ─────────────────────────────────"
	@echo "  make dev             Single Uvicorn, hot-reload (web only)"
	@echo "  make dev-worker      pgqueuer consumer with debug logging"
	@echo ""
	@echo "  ── Production (two terminals) ───────────────────"
	@echo "  make run             Gunicorn + $(WEB_WORKERS) UvicornWorker(s)  [60% cores]"
	@echo "  make worker          pgqueuer consumer process   [40% cores]"
	@echo ""
	@echo "  ── Production (background) ──────────────────────"
	@echo "  make start-all       Start both in background (PIDs in .pids/)"
	@echo "  make stop-all        Kill both background processes"
	@echo "  make status          Show running PIDs"
	@echo ""
	@echo "  ── Quality ─────────────────────────────────────"
	@echo "  make test            Run pytest"
	@echo "  make lint            Ruff check + format check"
	@echo "  make fmt             Auto-format with ruff"
	@echo "  make clean           Remove .venv and caches"
	@echo ""
	@echo "  Cores detected: $(TOTAL_CORES)  →  web=$(WEB_WORKERS) workers"
	@echo "  DATABASE_URL:         $(DATABASE_URL)"
	@echo "  REDIS_URL:            $(REDIS_URL)"
	@echo "  SESSION_TTL_SECONDS:  $(SESSION_TTL_SECONDS)"
	@echo ""

# ── Installation ──────────────────────────────────────────────────────────────
.PHONY: install
install:
	$(UV) sync --all-extras
	@echo "✓  venv ready (.venv/)"

.PHONY: db-install
db-install:
	@echo "Creating pgqueuer schema …"
	DATABASE_URL=$(DATABASE_URL) $(UV) run python scripts/db_install.py
	@echo "✓  pgqueuer schema ready"

.PHONY: redis-check
redis-check:
	@echo "Pinging Redis at $(REDIS_URL) …"
	$(UV) run python -c "\
import asyncio, redis.asyncio as r; \
asyncio.run(r.from_url('$(REDIS_URL)').ping()) or print('PONG')"
	@echo "✓  Redis reachable"

# ── Dev stack: docker-compose + uvicorn (reload) + pgqueuer (debug) ─────────
# start-dev:  spins up infra first (waits for healthchecks), then backgrounds
#             the web server and worker, writing logs to .pids/*.log
.PHONY: start-dev
start-dev: $(PID_DIR)
	@echo "[1/4] Starting infrastructure (Redis + PostgreSQL) …"
	$(DC) up -d --wait
	@echo "[2/4] Ensuring pgqueuer schema exists …"
	$(MAKE) --no-print-directory db-install
	@DATABASE_URL=$(DATABASE_URL) REDIS_URL=$(REDIS_URL) SESSION_TTL_SECONDS=$(SESSION_TTL_SECONDS) \
	nohup ./scripts/start_dev.sh >/dev/null 2>&1 </dev/null
	@echo ""
	@echo "  ✓  Dev stack is running!"
	@echo "  API:  http://$(HOST):$(PORT)"
	@echo "  Docs: http://$(HOST):$(PORT)/docs"
	@echo "  Logs: make logs-dev"
	@echo "  Stop: make stop-dev"
	@echo ""

.PHONY: stop-dev
stop-dev:
	@echo "Stopping app processes …"
	@if [ -f $(DEV_WEB_PID) ]; then \
	  kill $$(cat $(DEV_WEB_PID)) 2>/dev/null && echo "  web server stopped" || true; \
	  rm -f $(DEV_WEB_PID); fi
	@if [ -f $(DEV_WORKER_PID) ]; then \
	  kill $$(cat $(DEV_WORKER_PID)) 2>/dev/null && echo "  worker stopped" || true; \
	  rm -f $(DEV_WORKER_PID); fi
	@echo "Stopping Docker containers (data preserved) …"
	$(DC) stop
	@echo "  ✓  Dev stack stopped."
	@echo "  To wipe volumes too: docker compose down -v"

.PHONY: logs-dev
logs-dev:
	@echo "[web] $(DEV_WEB_LOG)  |  [worker] $(DEV_WORKER_LOG)"
	@echo "Press Ctrl-C to stop tailing."
	@echo ""
	tail -f $(DEV_WEB_LOG) $(DEV_WORKER_LOG) 2>/dev/null || \
	  echo "No logs yet — run 'make start-dev' first."

# ── Development (single terminal) ────────────────────────────────────────────
.PHONY: dev
dev:
	DATABASE_URL=$(DATABASE_URL) \
	$(REDIS_EXPORTS) \
	$(UVICORN) $(APP) \
	  --host $(HOST) \
	  --port $(PORT) \
	  --reload \
	  --log-level $(LOG_LEVEL)

.PHONY: dev-worker
dev-worker:
	PG_DSN=$(DATABASE_URL) $(REDIS_EXPORTS) $(PGQ) run $(PGQ_ENTRY) --log-level debug

# ── Production: web server ────────────────────────────────────────────────────
# Gunicorn spawns WEB_WORKERS processes (60 % of cores), each running Uvicorn.
.PHONY: run
run:
	DATABASE_URL=$(DATABASE_URL) \
	$(REDIS_EXPORTS) \
	WEB_CONCURRENCY=$(WEB_WORKERS) \
	$(GUNICORN) $(APP) -c gunicorn.conf.py

# ── Production: pgqueuer consumer ────────────────────────────────────────────
# Separate from Gunicorn — owns 40 % of cores for background job processing.
# --shutdown-on-listener-failure: crash cleanly if LISTEN drops (supervisor restarts).
.PHONY: worker
worker:
	PG_DSN=$(DATABASE_URL) $(PGQ) run $(PGQ_ENTRY) \
	  --log-level $(PGQ_LOG_LEVEL) \
	  --shutdown-on-listener-failure

# ── Background mode (both processes) ─────────────────────────────────────────
.PHONY: start-all
start-all: $(PID_DIR)
	@echo "Starting web server in background …"
	DATABASE_URL=$(DATABASE_URL) $(REDIS_EXPORTS) WEB_CONCURRENCY=$(WEB_WORKERS) \
	$(GUNICORN) $(APP) -c gunicorn.conf.py \
	  --daemon --pid $(WEB_PID)
	@echo "Starting pgqueuer worker in background …"
	PG_DSN=$(DATABASE_URL) REDIS_URL=$(REDIS_URL) SESSION_TTL_SECONDS=$(SESSION_TTL_SECONDS) \
	nohup $(PGQ) run $(PGQ_ENTRY) \
	  --log-level $(PGQ_LOG_LEVEL) \
	  --shutdown-on-listener-failure \
	  > /dev/null 2>&1 </dev/null & echo $$! > $(WORKER_PID_FILE)
	@echo "✓  Both processes running.  Use 'make status' to check."

.PHONY: stop-all
stop-all:
	@if [ -f $(WEB_PID) ]; then \
	  kill $$(cat $(WEB_PID)) 2>/dev/null && echo "web server stopped" || true; \
	  rm -f $(WEB_PID); fi
	@if [ -f $(WORKER_PID_FILE) ]; then \
	  kill $$(cat $(WORKER_PID_FILE)) 2>/dev/null && echo "pgqueuer worker stopped" || true; \
	  rm -f $(WORKER_PID_FILE); fi

.PHONY: status
status:
	@echo "── web server ──────────────────"
	@if [ -f $(WEB_PID) ]; then \
	  PID=$$(cat $(WEB_PID)); \
	  ps -p $$PID -o pid,stat,command 2>/dev/null || echo "  not running (stale pid $$PID)"; \
	else echo "  not started (no $(WEB_PID))"; fi
	@echo "── pgqueuer worker ─────────────"
	@if [ -f $(WORKER_PID_FILE) ]; then \
	  PID=$$(cat $(WORKER_PID_FILE)); \
	  ps -p $$PID -o pid,stat,command 2>/dev/null || echo "  not running (stale pid $$PID)"; \
	else echo "  not started (no $(WORKER_PID_FILE))"; fi

$(PID_DIR):
	mkdir -p $(PID_DIR)

# ── Tests ─────────────────────────────────────────────────────────────────────
.PHONY: test
test:
	$(UV) run pytest tests/ -v

.PHONY: cpu-stress
cpu-stress:
	$(UV) run python scripts/cpu_stress.py


# ── Lint / Format ─────────────────────────────────────────────────────────────
.PHONY: lint
lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: fmt
fmt:
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

# ── Clean ─────────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache $(PID_DIR)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "✓  cleaned"

.PHONY: clean-all
clean-all: clean
	$(DC) down -v
	@echo "✓  Docker volumes removed"
