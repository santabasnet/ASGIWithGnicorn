"""
app/constants.py — Shared constants for the ASGIWithGnicorn stack.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

# ── Session Store JSON Keys ───────────────────────────────────────────────────
KEY_SESSION_ID = "session_id"
KEY_USER_ID = "user_id"
KEY_MESSAGES = "messages"
KEY_METADATA = "metadata"
KEY_CREATED_AT = "created_at"
KEY_UPDATED_AT = "updated_at"
KEY_ROLE = "role"
KEY_CONTENT = "content"
KEY_TS = "ts"
KEY_LAST_ROLE = "last_role"

# ── Pagination Defaults ───────────────────────────────────────────────────────
DEFAULT_LIMIT = 20
DEFAULT_OFFSET = 0

# ── pgqueuer Entrypoint Names ────────────────────────────────────────────────
ENTRYPOINT_GENERATE_REPLY = "generate_reply"
ENTRYPOINT_SEND_EMAIL = "send_email"
ENTRYPOINT_EXPORT_REPORT = "export_report"
ENTRYPOINT_STRESS_CPU = "stress_cpu"
ENTRYPOINT_HEARTBEAT_LOG = "heartbeat_log"

# ── Role Names ────────────────────────────────────────────────────────────────
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"

# ── Default Fallbacks ────────────────────────────────────────────────────────
DEFAULT_APP_ENV = "development"
DEFAULT_DATABASE_URL = "postgresql://santa:santa@localhost:5432/santa"
DEFAULT_REDIS_URL = "redis://:santa@localhost:6379/0"

# ── Custom Headers ────────────────────────────────────────────────────────────
HEADER_X_WORKER_PID = "X-Worker-PID"
HEADER_X_PROCESS_TIME_MS = "X-Process-Time-Ms"

# ── Database Connection Pool Configuration ────────────────────────────────────
DB_POOL_MIN_SIZE = 2
DB_POOL_MAX_SIZE_WEB = 10
DB_POOL_MAX_SIZE_WORKER = 8
DB_POOL_COMMAND_TIMEOUT = 60

# ── Redis Connection Configuration ───────────────────────────────────────────
REDIS_POOL_MAX_CONNECTIONS = 10
REDIS_SOCKET_CONNECT_TIMEOUT = 5
REDIS_SOCKET_TIMEOUT = 5
