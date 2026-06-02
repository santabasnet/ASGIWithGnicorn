#!/usr/bin/env bash
# scripts/start_dev.sh
#
# Starts uvicorn and pgqueuer worker in the background and detaches them.
#
# Writer: Santa, Wiseyak
# Date: 2026-06-02

set -e

PID_DIR=".pids"
mkdir -p "$PID_DIR"

DEV_WEB_PID="$PID_DIR/dev-web.pid"
DEV_WORKER_PID="$PID_DIR/dev-worker.pid"
DEV_WEB_LOG="$PID_DIR/dev-web.log"
DEV_WORKER_LOG="$PID_DIR/dev-worker.log"

echo "[3/4] Starting web server (hot-reload) → logs: $DEV_WEB_LOG"
DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" SESSION_TTL_SECONDS="$SESSION_TTL_SECONDS" \
nohup uv run uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --reload --log-level info \
  > "$DEV_WEB_LOG" 2>&1 </dev/null &
echo $! > "$DEV_WEB_PID"
disown

echo "[4/4] Starting pgqueuer worker (debug) → logs: $DEV_WORKER_LOG"
PG_DSN="$DATABASE_URL" REDIS_URL="$REDIS_URL" SESSION_TTL_SECONDS="$SESSION_TTL_SECONDS" \
nohup uv run pgq run worker:main \
  --log-level debug \
  > "$DEV_WORKER_LOG" 2>&1 </dev/null &
echo $! > "$DEV_WORKER_PID"
disown
