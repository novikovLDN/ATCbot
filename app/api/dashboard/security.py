"""Dashboard request security: CSRF (Origin/Referer check) and login
rate limiting with lockout.

CSRF. Auth is an HttpOnly SameSite=Lax cookie. Lax already blocks the
classic cross-site form POST, but not same-site subdomains, old
browsers, or top-level navigations; every state-changing request
therefore must come from the dashboard's own origin. The check is strict
for cookie-authenticated requests: a POST/PUT/PATCH/DELETE carrying the
session cookie without a matching Origin (or Referer) is refused.

Rate limiting. Failures are counted per client IP and per username in
Redis when configured (the bot already uses it for sessions), otherwise
in process memory — the bot runs as a single process, so that is exact.
After too many failures the key is locked for LOCKOUT_SECONDS and even a
correct password is refused until the lock expires.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

import config
from app.services import admin_auth

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


# ── Client identity ──────────────────────────────────────────────────


def client_ip(request: Request) -> str:
    """The address the nearest proxy saw. Railway's edge appends the real
    client to X-Forwarded-For, so the LAST entry is the one a client
    cannot forge (anything before it is client-supplied)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


# ── CSRF ─────────────────────────────────────────────────────────────


def _origin_of(url: str) -> Optional[str]:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def allowed_origins(request: Request) -> set[str]:
    out: set[str] = set()
    base = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    if base:
        o = _origin_of(base)
        if o:
            out.add(o)
    host = request.headers.get("host")
    if host:
        # TLS terminates at the proxy, so the app may see http while the
        # browser says https. The host is what matters.
        out.add(f"https://{host}".lower())
        out.add(f"http://{host}".lower())
    return out


def check_origin(request: Request) -> None:
    """Raise 403 unless a state-changing request comes from our origin."""
    if request.method.upper() in SAFE_METHODS:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "csrf_cross_site")
    allowed = allowed_origins(request)
    origin = request.headers.get("origin")
    if origin and origin != "null":
        if origin.lower() in allowed:
            return
        raise HTTPException(403, "csrf_origin_mismatch")
    referer = request.headers.get("referer")
    if referer:
        if (_origin_of(referer) or "") in allowed:
            return
        raise HTTPException(403, "csrf_referer_mismatch")
    # No Origin and no Referer. Browsers always send Origin on fetch POST,
    # so this is a non-browser client. Allowed only when it carries no
    # ambient credential (the session cookie) — a bearer header or a body
    # token cannot be attached by a third-party page.
    if request.cookies.get(admin_auth.COOKIE_NAME):
        raise HTTPException(403, "csrf_origin_missing")


async def csrf_protect(request: Request) -> None:
    check_origin(request)


def check_ws_origin(origin: Optional[str], host: Optional[str]) -> bool:
    """Cross-site WebSocket hijacking guard for /dashboard/ws."""
    if not origin:
        return True  # non-browser client; the cookie is still required
    allowed = set()
    base = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    if base and _origin_of(base):
        allowed.add(_origin_of(base))
    if host:
        allowed |= {f"https://{host}".lower(), f"http://{host}".lower()}
    return origin.lower() in allowed


# ── Rate limiting / lockout ──────────────────────────────────────────


@dataclass(frozen=True)
class Policy:
    scope: str
    max_failures: int
    window_seconds: int
    lockout_seconds: int


LOGIN_IP = Policy("login_ip", max_failures=10, window_seconds=900, lockout_seconds=900)
LOGIN_USER = Policy("login_user", max_failures=5, window_seconds=900, lockout_seconds=900)
SETUP_IP = Policy("setup_ip", max_failures=5, window_seconds=900, lockout_seconds=900)
PASSKEY_IP = Policy("passkey_ip", max_failures=10, window_seconds=900, lockout_seconds=900)

_PREFIX = "dashboard:rl:"
_mem_fail: dict[str, tuple[int, float]] = {}   # key -> (count, window_ends_at)
_mem_lock: dict[str, float] = {}               # key -> locked_until


async def _redis():
    try:
        from app.utils.redis_client import get_redis, is_configured
        if not is_configured():
            return None
        return await get_redis()
    except Exception:
        return None


def _key(policy: Policy, ident: str) -> str:
    return f"{policy.scope}:{ident.lower()[:120]}"


async def locked_for(policy: Policy, ident: str) -> int:
    """Seconds until the lock on (policy, ident) expires; 0 = not locked."""
    key = _key(policy, ident)
    r = await _redis()
    if r is not None:
        try:
            ttl = await r.ttl(_PREFIX + "lock:" + key)
            return max(int(ttl), 0) if ttl and ttl > 0 else 0
        except Exception as e:
            logger.warning("rate-limit redis ttl failed: %s", e)
    until = _mem_lock.get(key, 0.0)
    left = int(until - time.time())
    if left <= 0:
        _mem_lock.pop(key, None)
        return 0
    return left


async def register_failure(policy: Policy, ident: str) -> int:
    """Count one failure; returns the lock duration if this one tripped it."""
    key = _key(policy, ident)
    r = await _redis()
    if r is not None:
        try:
            n = await r.incr(_PREFIX + "fail:" + key)
            if n == 1:
                await r.expire(_PREFIX + "fail:" + key, policy.window_seconds)
            if n >= policy.max_failures:
                await r.set(_PREFIX + "lock:" + key, "1", ex=policy.lockout_seconds)
                await r.delete(_PREFIX + "fail:" + key)
                return policy.lockout_seconds
            return 0
        except Exception as e:
            logger.warning("rate-limit redis incr failed, memory fallback: %s", e)
    now = time.time()
    count, ends = _mem_fail.get(key, (0, now + policy.window_seconds))
    if ends < now:
        count, ends = 0, now + policy.window_seconds
    count += 1
    if count >= policy.max_failures:
        _mem_lock[key] = now + policy.lockout_seconds
        _mem_fail.pop(key, None)
        return policy.lockout_seconds
    _mem_fail[key] = (count, ends)
    return 0


async def reset(policy: Policy, ident: str) -> None:
    key = _key(policy, ident)
    r = await _redis()
    if r is not None:
        try:
            await r.delete(_PREFIX + "fail:" + key)
        except Exception:
            pass
    _mem_fail.pop(key, None)


async def ensure_not_locked(*checks: tuple[Policy, str]) -> None:
    for policy, ident in checks:
        left = await locked_for(policy, ident)
        if left:
            logger.warning("DASHBOARD_AUTH_LOCKED scope=%s", policy.scope)
            raise HTTPException(429, "too_many_attempts", headers={"Retry-After": str(left)})


async def fail(*checks: tuple[Policy, str]) -> None:
    for policy, ident in checks:
        await register_failure(policy, ident)


async def _count_attempt(policy: Policy, ident: str) -> int:
    """Count one attempt in the policy window; returns the new count. Atomic:
    Redis INCR, or the memory read-modify-write with no await inside it."""
    key = _key(policy, ident)
    r = await _redis()
    if r is not None:
        try:
            n = await r.incr(_PREFIX + "fail:" + key)
            if n == 1:
                await r.expire(_PREFIX + "fail:" + key, policy.window_seconds)
            return int(n)
        except Exception as e:
            logger.warning("rate-limit redis incr failed, memory fallback: %s", e)
    now = time.time()
    count, ends = _mem_fail.get(key, (0, now + policy.window_seconds))
    if ends < now:
        count, ends = 0, now + policy.window_seconds
    count += 1
    _mem_fail[key] = (count, ends)
    return count


async def _set_lock(policy: Policy, ident: str) -> None:
    key = _key(policy, ident)
    r = await _redis()
    if r is not None:
        try:
            await r.set(_PREFIX + "lock:" + key, "1", ex=policy.lockout_seconds)
            return
        except Exception as e:
            logger.warning("rate-limit redis lock failed, memory fallback: %s", e)
    _mem_lock[key] = time.time() + policy.lockout_seconds


async def begin_attempt(*checks: tuple[Policy, str]) -> list[int]:
    """P1-6 increment-then-check (password login): count the attempt BEFORE the
    credential check, so parallel requests cannot all pass a "not locked yet"
    check. Only attempts whose count is <= max_failures reach verification;
    beyond that → lock + 429. The counter is not deleted on lock (it expires
    with its window): a racing request cannot restart the count. A successful
    login still resets it (reset())."""
    counts = [await _count_attempt(policy, ident) for policy, ident in checks]
    for (policy, ident), n in zip(checks, counts):
        if n > policy.max_failures:
            await _set_lock(policy, ident)
            logger.warning("DASHBOARD_AUTH_LOCKED scope=%s (attempt over the limit)", policy.scope)
            raise HTTPException(429, "too_many_attempts",
                                headers={"Retry-After": str(policy.lockout_seconds)})
    return counts


async def attempt_failed(checks: tuple[tuple[Policy, str], ...], counts: list[int]) -> None:
    """The attempt counted by begin_attempt failed: the max_failures-th failure
    locks, exactly as register_failure did."""
    for (policy, ident), n in zip(checks, counts):
        if n >= policy.max_failures:
            await _set_lock(policy, ident)


def clear_memory_state() -> None:
    """Tests only."""
    _mem_fail.clear()
    _mem_lock.clear()
