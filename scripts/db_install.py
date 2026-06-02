#!/usr/bin/env python3
"""
scripts/db_install.py — Installs the pgqueuer schema into PostgreSQL.

Policy & Technical Details:
- Connection:
  * Uses asyncpg to connect directly using DATABASE_URL.
- Idempotency:
  * Checks if the `pgqueuer_status` type exists in `pg_type` before attempting installation.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
from pgqueuer.db import AsyncpgDriver
from pgqueuer.queries import Queries


async def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"  Connecting to {url.split('@')[-1]} …")
    conn: asyncpg.Connection = await asyncpg.connect(url)
    try:
        already_installed = await conn.fetchval(
            "SELECT 1 FROM pg_type WHERE typname = 'pgqueuer_status'"
        )
        if already_installed:
            print("  ✓  pgqueuer schema already installed (skipped)")
        else:
            await Queries(AsyncpgDriver(conn)).install()
            print("  ✓  pgqueuer schema installed")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
