"""Idempotency-Key for mutating dashboard endpoints.

A double click or a retry after a timeout used to run an admin action
twice — two grants, two balance credits, two broadcasts. The SPA now
sends `Idempotency-Key: <uuid>` (one per form submission, reused on
retry). For the same key the first response is stored for 10 minutes and
returned again without re-running the handler; concurrent requests with
the same key wait for the first one. Requests without the header behave
exactly as before.

Scope of a key: the caller's session (hash) + the key itself. Reusing a
key for a different method / path / body is refused with 422 rather than
silently answered with somebody else's result.

Storage: Redis when configured (same client as sessions and the login
rate limiter), otherwise process memory — the bot is a single process.
Only non-5xx answers are stored, so a failed attempt can be retried with
the same key; 401 / 403 / 429 are never stored.

Used as `APIRouter(route_class=IdempotentRoute)`. The CSRF origin check
runs before the cache is consulted, so a cross-site request cannot fish
for a stored answer.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Callable, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.dashboard.security import SAFE_METHODS, check_origin
from app.services import admin_auth

logger = logging.getLogger(__name__)

TTL_SECONDS = 600
HEADER = "idempotency-key"
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_PREFIX = "dashboard:idem:"
_NOT_STORED = frozenset({401, 403, 429})

_mem: dict[str, tuple[float, dict[str, Any]]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def _redis():
    try:
        from app.utils.redis_client import get_redis, is_configured
        if not is_configured():
            return None
        return await get_redis()
    except Exception:
        return None


async def _load(key: str) -> Optional[dict[str, Any]]:
    r = await _redis()
    if r is not None:
        try:
            raw = await r.get(_PREFIX + key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("idempotency redis get failed: %s", e)
    hit = _mem.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    _mem.pop(key, None)
    return None


async def _save(key: str, record: dict[str, Any]) -> None:
    r = await _redis()
    if r is not None:
        try:
            await r.set(_PREFIX + key, json.dumps(record), ex=TTL_SECONDS)
            return
        except Exception as e:
            logger.warning("idempotency redis set failed, memory fallback: %s", e)
    now = time.time()
    for k in [k for k, (exp, _) in _mem.items() if exp <= now]:
        _mem.pop(k, None)
    _mem[key] = (now + TTL_SECONDS, record)


def clear_memory_state() -> None:
    """Tests only."""
    _mem.clear()
    _locks.clear()


def _caller(request: Request) -> str:
    ident = request.cookies.get(admin_auth.COOKIE_NAME) or request.headers.get("authorization") or ""
    return hashlib.sha256(ident.encode()).hexdigest()[:32]


def _replay(record: dict[str, Any]) -> Response:
    return Response(
        content=record["body"].encode("latin-1"),
        status_code=record["status"],
        media_type=record.get("media_type") or "application/json",
        headers={"Idempotency-Replayed": "true"},
    )


class IdempotentRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            key = request.headers.get(HEADER)
            if request.method.upper() in SAFE_METHODS or not key:
                return await original(request)
            if not _KEY_RE.match(key):
                return JSONResponse({"detail": "invalid_idempotency_key"}, status_code=400)
            check_origin(request)

            body = await request.body()
            fingerprint = hashlib.sha256(
                request.method.encode() + b" " + request.url.path.encode() + b"\n" + body
            ).hexdigest()
            store_key = f"{_caller(request)}:{key}"

            lock = _locks.setdefault(store_key, asyncio.Lock())
            async with lock:
                stored = await _load(store_key)
                if stored is not None:
                    if stored["fingerprint"] != fingerprint:
                        return JSONResponse({"detail": "idempotency_key_reused"}, status_code=422)
                    logger.info("DASHBOARD_IDEMPOTENT_REPLAY path=%s", request.url.path)
                    return _replay(stored)

                response = await original(request)
                raw = getattr(response, "body", None)
                if isinstance(raw, (bytes, bytearray)) and response.status_code < 500 \
                        and response.status_code not in _NOT_STORED:
                    await _save(store_key, {
                        "fingerprint": fingerprint,
                        "status": response.status_code,
                        "media_type": response.media_type,
                        "body": bytes(raw).decode("latin-1"),
                    })
            if not lock.locked():
                _locks.pop(store_key, None)
            return response

        return handler
