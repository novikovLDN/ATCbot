"""POST /auth/login must not block the event loop while bcrypt runs.

bcrypt cost 12 takes ~0.25 s per check. The bot, payment webhooks and workers
share one process/event loop with the dashboard API, and /auth/login needs no
auth, so a synchronous check would stall everything for every attempt.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")

from fastapi import Response  # noqa: E402

import config  # noqa: E402
from app.api.dashboard import auth as auth_mod  # noqa: E402
from app.api.dashboard import security  # noqa: E402
from app.services import admin_auth  # noqa: E402

SLOW_S = 0.3


def _slow_verify(plain, hashed):
    time.sleep(SLOW_S)  # stands in for bcrypt.checkpw (CPU-bound, synchronous)
    return plain == "correct-password"


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)
    monkeypatch.setattr(security, "client_ip", lambda request: "203.0.113.7")
    monkeypatch.setattr(security, "ensure_not_locked", AsyncMock())
    monkeypatch.setattr(security, "fail", AsyncMock())
    monkeypatch.setattr(security, "reset", AsyncMock())
    monkeypatch.setattr(
        admin_auth, "get_credentials",
        AsyncMock(return_value={"username": "boss", "password_hash": "h"}),
    )
    monkeypatch.setattr(admin_auth, "verify_password", _slow_verify)
    monkeypatch.setattr(admin_auth, "create_session", AsyncMock(return_value="tok"))


async def test_login_password_check_does_not_block_the_event_loop(patched):
    ticks = 0
    # The window is fixed BEFORE login starts: a blocking check freezes the loop
    # past `end`, so the ticker never gets a turn inside the window.
    end = time.monotonic() + SLOW_S * 0.8

    async def ticker():
        nonlocal ticks
        while time.monotonic() < end:
            await asyncio.sleep(0.01)
            if time.monotonic() < end:
                ticks += 1

    body = auth_mod.LoginRequest(username="boss", password="correct-password")
    result, _ = await asyncio.gather(
        auth_mod.auth_login(body, SimpleNamespace(), Response()),
        ticker(),
    )
    assert result == {"ok": True}
    # Blocking check: the ticker is frozen for the whole 0.3 s → ~0-1 ticks.
    assert ticks >= 5
