"""/white (show_traffic_info_message) auto-provision: trial gets TRIAL_BYPASS_MB, not 5 GB.

Owner rule (docs/audit/SCOPE.md): trial = 500 MB bypass. The callback screen
(show_traffic_info) already used TRIAL_BYPASS_MB; the /white message variant
still created the bypass entity with 5 GB for trial users.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
from app.handlers import traffic


def _message(tg_id: int = 777):
    msg = MagicMock()
    msg.from_user.id = tg_id
    msg.answer = AsyncMock()
    return msg


@pytest.fixture
def captured(monkeypatch):
    calls = []

    async def fake_create(telegram_id, sub_type, expires_at, traffic_limit_override=None):
        calls.append({"sub_type": sub_type, "override": traffic_limit_override})

    def fake_fire_and_forget(coro):
        # capture the coroutine; the test awaits it explicitly
        calls.append({"_coro": coro})

    monkeypatch.setattr(traffic.remnawave_service, "create_remnawave_user", fake_create)
    monkeypatch.setattr(traffic.remnawave_service, "_fire_and_forget", fake_fire_and_forget)
    monkeypatch.setattr(traffic, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(traffic.database, "get_remnawave_uuid", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    return calls


async def _run(monkeypatch, captured, sub_type: str) -> int:
    expires = datetime.now(timezone.utc) + timedelta(days=3)
    monkeypatch.setattr(
        traffic.database, "get_subscription",
        AsyncMock(return_value={"subscription_type": sub_type, "expires_at": expires}),
        raising=False,
    )
    await traffic.show_traffic_info_message(_message())
    coros = [c["_coro"] for c in captured if "_coro" in c]
    assert len(coros) == 1
    await coros[0]
    overrides = [c["override"] for c in captured if "override" in c]
    assert len(overrides) == 1
    return overrides[0]


async def test_trial_user_gets_trial_bypass_mb_not_5gb(monkeypatch, captured):
    monkeypatch.setattr(config, "TRIAL_BYPASS_MB", 500)
    assert await _run(monkeypatch, captured, "trial") == 500 * 1024 ** 2


async def test_paid_user_still_gets_10gb(monkeypatch, captured):
    assert await _run(monkeypatch, captured, "basic") == 10 * 1024 ** 3
