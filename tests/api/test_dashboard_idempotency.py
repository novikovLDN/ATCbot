"""Idempotency-Key on mutating dashboard endpoints: one key = one
execution, whether the duplicate arrives later or concurrently."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402
from app.api import dashboard  # noqa: E402
from app.api.dashboard import idempotency, security  # noqa: E402
from app.services import admin_auth  # noqa: E402

ORIGIN = "http://testserver"
GRANT = "/dashboard/api/users/555/grant"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)

    async def lookup(token):
        return 1 if token in ("good", "other") else None

    async def no_redis():
        return None

    monkeypatch.setattr(admin_auth, "lookup_session", lookup)
    monkeypatch.setattr(idempotency, "_redis", no_redis)
    monkeypatch.setattr(security, "_redis", no_redis)
    idempotency.clear_memory_state()
    yield
    idempotency.clear_memory_state()


@pytest.fixture
def grant(monkeypatch):
    m = AsyncMock(return_value=(datetime(2026, 10, 1, tzinfo=timezone.utc), "vless://key"))
    monkeypatch.setattr(database, "admin_grant_access_atomic", m, raising=False)
    return m


def app() -> FastAPI:
    a = FastAPI()
    a.include_router(dashboard.router, prefix="/dashboard/api")
    return a


def client(cookie: str = "good") -> TestClient:
    c = TestClient(app())
    c.cookies.set(admin_auth.COOKIE_NAME, cookie)
    return c


def post(c, key=None, body=None, url=GRANT):
    headers = {"Origin": ORIGIN}
    if key:
        headers["Idempotency-Key"] = key
    return c.post(url, headers=headers, json=body or {"days": 30, "tariff": "basic"})


def test_same_key_runs_once(grant):
    c = client()
    first = post(c, "k-aaaaaaaa-1")
    second = post(c, "k-aaaaaaaa-1")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert second.headers.get("idempotency-replayed") == "true"
    assert grant.await_count == 1


def test_different_keys_run_twice(grant):
    c = client()
    post(c, "k-aaaaaaaa-1")
    post(c, "k-bbbbbbbb-2")
    assert grant.await_count == 2


def test_no_key_keeps_old_behaviour(grant):
    c = client()
    post(c)
    post(c)
    assert grant.await_count == 2


def test_key_reused_with_other_body_is_refused(grant):
    c = client()
    post(c, "k-aaaaaaaa-1")
    r = post(c, "k-aaaaaaaa-1", body={"days": 90, "tariff": "basic"})
    assert r.status_code == 422
    assert r.json()["detail"] == "idempotency_key_reused"
    assert grant.await_count == 1


def test_keys_are_scoped_to_the_session(grant):
    post(client("good"), "k-aaaaaaaa-1")
    post(client("other"), "k-aaaaaaaa-1")
    assert grant.await_count == 2


def test_server_error_is_not_stored(monkeypatch):
    m = AsyncMock(side_effect=[RuntimeError("panel down"),
                               (datetime(2026, 10, 1, tzinfo=timezone.utc), "vless://key")])
    monkeypatch.setattr(database, "admin_grant_access_atomic", m, raising=False)
    c = client()
    assert post(c, "k-aaaaaaaa-1").status_code == 500
    assert post(c, "k-aaaaaaaa-1").status_code == 200  # retry with the same key re-runs
    assert m.await_count == 2


def test_invalid_key_rejected(grant):
    r = post(client(), "bad key!")
    assert r.status_code == 400
    assert grant.await_count == 0


def test_cross_origin_cannot_replay(grant):
    c = client()
    post(c, "k-aaaaaaaa-1")
    r = c.post(GRANT, headers={"Origin": "https://evil.example", "Idempotency-Key": "k-aaaaaaaa-1"},
               json={"days": 30, "tariff": "basic"})
    assert r.status_code == 403


def test_unauthenticated_is_not_stored(grant):
    r = post(client("stale"), "k-aaaaaaaa-1")
    assert r.status_code == 401
    assert post(client("good"), "k-aaaaaaaa-1").status_code == 200
    assert grant.await_count == 1


def test_balance_double_submit_credits_once(monkeypatch):
    inc = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "increase_balance", inc, raising=False)
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=100.0), raising=False)
    c = client()
    for _ in range(3):
        r = post(c, "k-balance-0001", body={"delta_rubles": 100, "reason": "test"},
                 url="/dashboard/api/users/555/balance")
        assert r.status_code == 200
    assert inc.await_count == 1


async def test_concurrent_duplicates_run_once(monkeypatch):
    started = asyncio.Event()

    async def slow_grant(*a, **kw):
        started.set()
        await asyncio.sleep(0.05)
        return datetime(2026, 10, 1, tzinfo=timezone.utc), "vless://key"

    m = AsyncMock(side_effect=slow_grant)
    monkeypatch.setattr(database, "admin_grant_access_atomic", m, raising=False)
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN,
                                 cookies={admin_auth.COOKIE_NAME: "good"}) as ac:
        headers = {"Origin": ORIGIN, "Idempotency-Key": "k-concurrent-1"}
        body = {"days": 30, "tariff": "basic"}
        r1, r2 = await asyncio.gather(
            ac.post(GRANT, headers=headers, json=body),
            ac.post(GRANT, headers=headers, json=body),
        )
    assert r1.status_code == r2.status_code == 200
    assert m.await_count == 1
