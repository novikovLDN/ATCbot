"""P1-6: the password-login lockout must hold under parallel requests.

Before: the "not locked" check ran before `await get_credentials()` and the
failure was counted only AFTER the password check, so N parallel wrong
passwords all passed the check and all reached bcrypt (review: 200 attempts
against a 5/10 limit). Now the attempt is counted BEFORE the password check
(increment-then-check): at most LOGIN_USER.max_failures attempts per window
reach verification, the rest get 429. Sequential behaviour is unchanged.
"""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException, Response  # noqa: E402

import config  # noqa: E402
from app.api.dashboard import auth as auth_mod  # noqa: E402
from app.api.dashboard import security  # noqa: E402
from app.services import admin_auth  # noqa: E402


@pytest.fixture
def checked(monkeypatch):
    """Passwords that reached the (bcrypt) verification."""
    security.clear_memory_state()
    monkeypatch.setattr(security, "_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)
    monkeypatch.setattr(security, "client_ip", lambda request: "198.51.100.9")

    async def get_credentials():
        await asyncio.sleep(0.01)   # the real DB read: parallel requests interleave here
        return {"username": "boss", "password_hash": "h"}
    monkeypatch.setattr(admin_auth, "get_credentials", get_credentials)

    seen: list = []
    guard = threading.Lock()

    def verify(plain, hashed):      # runs in asyncio.to_thread, like bcrypt
        with guard:
            seen.append(plain)
        return plain == "correct-password"
    monkeypatch.setattr(admin_auth, "verify_password", verify)
    monkeypatch.setattr(admin_auth, "create_session", AsyncMock(return_value="tok"))
    yield seen
    security.clear_memory_state()


async def _login(password: str):
    body = auth_mod.LoginRequest(username="boss", password=password)
    try:
        return await auth_mod.auth_login(body, SimpleNamespace(), Response())
    except HTTPException as e:
        return e.status_code


async def test_parallel_wrong_passwords_reach_verification_at_most_the_limit(checked):
    results = await asyncio.gather(*[_login(f"wrong-pass-{i:03d}") for i in range(50)])

    limit = security.LOGIN_USER.max_failures
    assert len(checked) <= limit, f"{len(checked)} attempts reached bcrypt (limit {limit})"
    assert results.count(401) == len(checked)
    assert results.count(429) == 50 - len(checked)
    # locked now: even the right password waits for the lockout to end
    assert await _login("correct-password") == 429
    assert "correct-password" not in checked


async def test_sequential_semantics_unchanged(checked):
    limit = security.LOGIN_USER.max_failures
    for _ in range(limit - 1):
        assert await _login("wrong-password") == 401
    assert await _login("correct-password") == {"ok": True}      # success resets the counters
    for _ in range(limit):
        assert await _login("wrong-password") == 401             # the limit-th failure locks
    assert await _login("wrong-password") == 429
    assert len(checked) == (limit - 1) + 1 + limit
