"""Tiny in-process TTL cache for dashboard reads.

The bot runs as one process (advisory lock), so a module-level dict is
enough. Concurrent callers of the same key share one in-flight load
instead of stampeding the database or the panel.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

_store: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def cached(key: str, ttl: float, loader: Callable[[], Awaitable[Any]]) -> Any:
    hit = _store.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _store.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        value = await loader()
        _store[key] = (time.monotonic() + ttl, value)
        return value


def clear() -> None:
    _store.clear()
