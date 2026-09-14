"""Read-only connections for dashboard metrics.

The dashboard runs in the same process and on the same pool as the bot,
so a slow report is a slow bot. Every metric query goes through
`read_conn(pool)`:

  * READ ONLY transaction — a metric can never write, even by mistake;
  * `SET LOCAL statement_timeout` — a query that goes pathological (a
    grown table, a lost index) is cancelled after DASHBOARD_STATEMENT_
    TIMEOUT_MS instead of holding a pool connection. LOCAL = it resets
    when the transaction ends, so the pooled connection goes back to the
    bot with its normal settings.

The pool is passed in (callers use their module-level `get_pool`, which
tests monkeypatch). Results are cached by the routes
(app/services/dashboard_cache) and no caller holds the connection across
an HTTP call.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

DASHBOARD_STATEMENT_TIMEOUT_MS = int(os.getenv("DASHBOARD_STATEMENT_TIMEOUT_MS", "5000"))


@asynccontextmanager
async def read_conn(pool: Any, timeout_ms: Optional[int] = None) -> AsyncIterator[Any]:
    """Yield a connection inside a read-only transaction with a statement
    timeout, or None when the pool is not up (degraded mode)."""
    if pool is None:
        yield None
        return
    ms = int(timeout_ms or DASHBOARD_STATEMENT_TIMEOUT_MS)
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            # An int built here, not user input; SET does not accept $1.
            await conn.execute(f"SET LOCAL statement_timeout = {ms}")
            yield conn
