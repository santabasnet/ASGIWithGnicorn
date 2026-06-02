"""
gunicorn.conf.py — Gunicorn configuration for the FastAPI + pgqueuer stack.

Policy & Technical Details:
- Workers Strategy:
  * Spawns UvicornWorker processes.
  * Number of workers defaults to 60% of total CPU cores (minimum 1), overrideable via WEB_CONCURRENCY.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

import multiprocessing
import os

# ── Binding ──────────────────────────────────────────────────────────────────
host = os.environ.get("HOST", "0.0.0.0")
port = os.environ.get("PORT", "8000")
bind = f"{host}:{port}"

# ── Workers (60 % of cores → HTTP) ───────────────────────────────────────────
_cores = multiprocessing.cpu_count()
_default_web_workers = max(1, round(_cores * 0.6))
workers = int(os.environ.get("WEB_CONCURRENCY", _default_web_workers))

# Gunicorn uses Uvicorn's ASGI worker so every process gets an asyncio loop.
worker_class = "uvicorn.workers.UvicornWorker"

# ── Timeouts ─────────────────────────────────────────────────────────────────
timeout = 120
keepalive = 5
graceful_timeout = 30

# ── Logging ──────────────────────────────────────────────────────────────────
loglevel = os.environ.get("LOG_LEVEL", "info")
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sμs'


# ── Lifecycle hooks ───────────────────────────────────────────────────────────
def on_starting(server):
    total = multiprocessing.cpu_count()
    server.log.info(
        f"Gunicorn starting: {workers} web workers "
        f"(~60% of {total} cores). "
        f"Start pgqueuer separately: make worker"
    )


def on_exit(server):
    server.log.info("Gunicorn master exiting")


def post_fork(server, worker):
    server.log.info(f"UvicornWorker spawned (pid={worker.pid})")


def worker_exit(server, worker):
    server.log.info(f"UvicornWorker exiting (pid={worker.pid})")
