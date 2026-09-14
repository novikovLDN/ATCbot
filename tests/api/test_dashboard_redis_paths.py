"""Dashboard security state through Redis — the production path.

RUNBOOK: with PROD_REDIS_URL set (recommended), login lockout counters,
Idempotency-Key answers and admin sessions live in Redis. Every existing test
forces `_redis()` to None, so only the in-memory fallback ever ran (coverage
09: security.register_failure/_count_attempt/_set_lock/locked_for Redis
branches, idempotency._load/_save, admin_auth.lookup_session/purge_all_sessions
unrun). A bug there fails OPEN and silently: brute force not locked out, a
double-submitted balance credit executed twice, sessions surviving a password
reset.

FakeRedis implements only the commands these modules use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI, HTTPException  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402
from app.api import dashboard  # noqa: E402
from app.api.dashboard import idempotency, security  # noqa: E402
from app.services import admin_auth  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.kv: dict = {}
        self.ttl_s: dict = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v.encode() if isinstance(v, str) else v
        if ex:
            self.ttl_s[k] = int(ex)
        return True

    async def incr(self, k):
        n = int(self.kv.get(k, b"0")) + 1
        self.kv[k] = str(n).encode()
        return n

    async def expire(self, k, s):
        self.ttl_s[k] = int(s)
        return True

    async def ttl(self, k):
        if k not in self.kv:
            return -2
        return self.ttl_s.get(k, -1)

    async def delete(self, *ks):
        n = 0
        for k in ks:
            n += self.kv.pop(k, None) is not None
            self.ttl_s.pop(k, None)
        return n

    async def scan(self, cursor=0, match=None, count=None):
        prefix = (match or "").rstrip("*")
        return 0, [k for k in list(self.kv) if k.startswith(prefix)]


class DeadRedis:
    """Configured, but every command fails (Redis restarting / network)."""

    def __getattr__(self, name):
        async def fail(*a, **kw):
            raise ConnectionError("redis down")
        return fail


def _use(monkeypatch, module, r):
    async def _redis():
        return r
    monkeypatch.setattr(module, "_redis", _redis)


@pytest.fixture(autouse=True)
def clean():
    security.clear_memory_state()
    idempotency.clear_memory_state()
    admin_auth._MEM_SESSIONS.clear()
    yield
    security.clear_memory_state()
    idempotency.clear_memory_state()
    admin_auth._MEM_SESSIONS.clear()


# ── login lockout ────────────────────────────────────────────────────

CHECKS = ((security.LOGIN_USER, "boss"),)


async def _fail_login():
    counts = await security.begin_attempt(*CHECKS)
    await security.attempt_failed(CHECKS, counts)


async def test_lockout_is_stored_in_redis_and_refuses_the_right_password(monkeypatch):
    r = FakeRedis()
    _use(monkeypatch, security, r)
    for _ in range(security.LOGIN_USER.max_failures):
        await _fail_login()
    assert await security.locked_for(security.LOGIN_USER, "boss") == security.LOGIN_USER.lockout_seconds
    with pytest.raises(HTTPException) as e:
        await security.ensure_not_locked(*CHECKS)
    assert e.value.status_code == 429 and e.value.headers["Retry-After"]
    # the state is in Redis (shared across restarts), not in process memory
    assert security._mem_lock == {} and security._mem_fail == {}
    assert any(k.startswith("dashboard:rl:lock:") for k in r.kv)


async def test_attempt_over_the_limit_is_refused_before_verification(monkeypatch):
    _use(monkeypatch, security, FakeRedis())
    for _ in range(security.LOGIN_USER.max_failures):
        await security.begin_attempt(*CHECKS)          # parallel requests, none finished yet
    with pytest.raises(HTTPException) as e:
        await security.begin_attempt(*CHECKS)
    assert e.value.status_code == 429


async def test_register_failure_locks_through_redis(monkeypatch):
    _use(monkeypatch, security, FakeRedis())
    policy = security.PASSKEY_IP
    for _ in range(policy.max_failures - 1):
        assert await security.register_failure(policy, "198.51.100.1") == 0
    assert await security.register_failure(policy, "198.51.100.1") == policy.lockout_seconds
    assert await security.locked_for(policy, "198.51.100.1") > 0


async def test_successful_login_resets_the_counter_in_redis(monkeypatch):
    r = FakeRedis()
    _use(monkeypatch, security, r)
    for _ in range(security.LOGIN_USER.max_failures - 1):
        await _fail_login()
    await security.reset(security.LOGIN_USER, "boss")
    await _fail_login()                                 # one more failure after a success
    assert await security.locked_for(security.LOGIN_USER, "boss") == 0


async def test_redis_down_falls_back_to_memory_and_still_locks(monkeypatch):
    _use(monkeypatch, security, DeadRedis())
    for _ in range(security.LOGIN_USER.max_failures):
        await _fail_login()
    with pytest.raises(HTTPException) as e:
        await security.ensure_not_locked(*CHECKS)
    assert e.value.status_code == 429


# ── Idempotency-Key ──────────────────────────────────────────────────

ORIGIN = "http://testserver"
BALANCE = "/dashboard/api/users/555/balance"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)

    async def lookup(token):
        return 1 if token == "good" else None
    monkeypatch.setattr(admin_auth, "lookup_session", lookup)
    _use(monkeypatch, security, None)
    inc = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "increase_balance", inc, raising=False)
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=100.0), raising=False)
    a = FastAPI()
    a.include_router(dashboard.router, prefix="/dashboard/api")
    c = TestClient(a)
    c.cookies.set(admin_auth.COOKIE_NAME, "good")
    return c, inc


def _credit(c, key, rub=100):
    return c.post(BALANCE, headers={"Origin": ORIGIN, "Idempotency-Key": key},
                  json={"delta_rubles": rub, "reason": "test"})


def test_double_submit_credits_once_via_redis(monkeypatch, api):
    c, inc = api
    r = FakeRedis()
    _use(monkeypatch, idempotency, r)
    first, second = _credit(c, "k-redis-0001"), _credit(c, "k-redis-0001")
    assert first.status_code == second.status_code == 200
    assert second.headers.get("idempotency-replayed") == "true"
    assert inc.await_count == 1
    stored = [k for k in r.kv if k.startswith("dashboard:idem:")]
    assert len(stored) == 1 and r.ttl_s[stored[0]] == idempotency.TTL_SECONDS
    assert idempotency._mem == {}                       # really went through Redis


def test_key_reused_with_another_amount_is_refused_via_redis(monkeypatch, api):
    c, inc = api
    _use(monkeypatch, idempotency, FakeRedis())
    _credit(c, "k-redis-0002", rub=100)
    r = _credit(c, "k-redis-0002", rub=1000)
    assert r.status_code == 422
    assert inc.await_count == 1


def test_redis_down_still_credits_once(monkeypatch, api):
    """Redis fails on every call: the answer falls back to memory, the second
    submit is still a replay (not a second credit)."""
    c, inc = api
    _use(monkeypatch, idempotency, DeadRedis())
    assert _credit(c, "k-redis-0003").status_code == 200
    assert _credit(c, "k-redis-0003").status_code == 200
    assert inc.await_count == 1


# ── admin sessions ───────────────────────────────────────────────────

async def test_session_lives_in_redis_and_revoke_ends_it(monkeypatch):
    r = FakeRedis()
    _use(monkeypatch, admin_auth, r)
    token = await admin_auth.create_session(777)
    assert admin_auth._MEM_SESSIONS == {}
    assert r.ttl_s[admin_auth._REDIS_KEY_PREFIX + token] == admin_auth.SESSION_TTL_SECONDS
    assert await admin_auth.lookup_session(token) == 777
    await admin_auth.revoke_session(token)
    assert await admin_auth.lookup_session(token) is None


async def test_password_reset_purges_every_redis_session(monkeypatch):
    r = FakeRedis()
    _use(monkeypatch, admin_auth, r)
    r.kv["unrelated:key"] = b"keep"
    tokens = [await admin_auth.create_session(777) for _ in range(3)]
    await admin_auth.purge_all_sessions()
    for t in tokens:
        assert await admin_auth.lookup_session(t) is None
    assert r.kv == {"unrelated:key": b"keep"}


async def test_unknown_or_garbage_session_is_refused(monkeypatch):
    r = FakeRedis()
    _use(monkeypatch, admin_auth, r)
    r.kv[admin_auth._REDIS_KEY_PREFIX + "garbage"] = b"not-a-number"
    assert await admin_auth.lookup_session("garbage") is None
    assert await admin_auth.lookup_session("missing") is None
    assert await admin_auth.lookup_session("") is None


async def test_redis_down_does_not_accept_a_redis_only_session(monkeypatch):
    r = FakeRedis()
    _use(monkeypatch, admin_auth, r)
    token = await admin_auth.create_session(777)
    _use(monkeypatch, admin_auth, DeadRedis())
    assert await admin_auth.lookup_session(token) is None   # fail closed, no crash


def test_fake_redis_ttl_semantics():
    """Guard for the fake itself: ttl() of a missing key is -2, like Redis."""
    import asyncio
    r = FakeRedis()
    assert asyncio.run(r.ttl("nope")) == -2
    assert datetime.now(timezone.utc)  # keep the import used
