"""R6 (docs/audit/11_telegram_runtime.md §5): the farm and traffic workers sent
with a raw bot.send_message.

- A 403 did not mark the user unreachable; a short 429 was not retried.
- The farm set its notified_* flag even when the send failed, so a 429 or a
  network error lost the "ripe" / "12 h left" notification for good.

Now both go through safe_send_message. The farm sets a notified_* flag only
after a successful send (the next 30-min pass retries otherwise). Game-state
transitions (status ready / dead) are still applied unconditionally: harvest
checks the status, so a dead plot must not stay "ready" because a message
failed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter

import database
from app.workers import farm_notifications as fn
from app.workers import traffic_monitor as tm

TG = 5151
NOW = datetime.now(timezone.utc)


def _plot(ready_delta_h, dead_delta_h):
    return {
        "plot_id": 0, "status": "growing", "plant_type": "tomato",
        "planted_at": (NOW - timedelta(days=4)).isoformat(),
        "ready_at": (NOW + timedelta(hours=ready_delta_h)).isoformat(),
        "dead_at": (NOW + timedelta(hours=dead_delta_h)).isoformat(),
        "notified_ready": False, "notified_12h": False, "notified_dead": False,
    }


RIPE = _plot(-1, 48)          # ripe, far from rotting: only the "ready" message
DEAD = dict(_plot(-30, -1), notified_ready=True, notified_12h=True)  # only the "dead" one


def _ok():
    return MagicMock(message_id=1)


def _flood(seconds):
    return TelegramRetryAfter(method=MagicMock(), message=f"retry after {seconds}", retry_after=seconds)


def _net():
    return TelegramNetworkError(method=MagicMock(), message="connection reset")


def _blocked():
    return TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was blocked by the user")


@pytest.fixture
def farm(monkeypatch):
    state = {"plots": [], "applied": []}

    async def get_users_with_active_farm():
        return [{"telegram_id": TG, "farm_plots": [dict(p) for p in state["plots"]]}]

    async def apply_farm_notification_flags(telegram_id, updates):
        state["applied"].extend(updates)
        return len(updates)

    monkeypatch.setattr(database, "get_users_with_active_farm", get_users_with_active_farm)
    monkeypatch.setattr(database, "apply_farm_notification_flags", apply_farm_notification_flags)
    state["unreachable"] = AsyncMock()
    monkeypatch.setattr(database, "mark_user_unreachable", state["unreachable"])
    return state


def _set_fields(state):
    out = {}
    for upd in state["applied"]:
        out.update(upd["set"])
    return out


async def test_failed_ready_notification_keeps_the_flag_unset(farm):
    farm["plots"] = [RIPE]
    bot = MagicMock(send_message=AsyncMock(side_effect=_net()))
    await fn.farm_notifications_iteration(bot)
    fields = _set_fields(farm)
    assert fields.get("status") == "ready"
    assert "notified_ready" not in fields, "a lost message must be retried on the next pass"


async def test_long_flood_wait_keeps_the_flag_unset(farm):
    farm["plots"] = [RIPE]
    bot = MagicMock(send_message=AsyncMock(side_effect=_flood(60)))
    await fn.farm_notifications_iteration(bot)
    assert "notified_ready" not in _set_fields(farm)


async def test_delivered_ready_notification_sets_the_flag_once(farm):
    farm["plots"] = [RIPE]
    bot = MagicMock(send_message=AsyncMock(return_value=_ok()))
    await fn.farm_notifications_iteration(bot)
    assert _set_fields(farm) == {"status": "ready", "notified_ready": True}
    assert bot.send_message.await_count == 1


async def test_short_flood_wait_is_retried_and_flagged(farm):
    farm["plots"] = [RIPE]
    bot = MagicMock(send_message=AsyncMock(side_effect=[_flood(0), _ok()]))
    await fn.farm_notifications_iteration(bot)
    assert bot.send_message.await_count == 2
    assert _set_fields(farm).get("notified_ready") is True


async def test_dead_status_is_applied_even_if_the_message_fails(farm):
    farm["plots"] = [DEAD]
    bot = MagicMock(send_message=AsyncMock(side_effect=_net()))
    await fn.farm_notifications_iteration(bot)
    fields = _set_fields(farm)
    assert fields.get("status") == "dead"
    assert "notified_dead" not in fields


async def test_blocked_farm_user_is_marked_unreachable(farm):
    farm["plots"] = [RIPE]
    bot = MagicMock(send_message=AsyncMock(side_effect=_blocked()))
    await fn.farm_notifications_iteration(bot)
    farm["unreachable"].assert_awaited_once_with(TG)


async def test_storm_announce_retries_a_short_flood_wait(farm):
    bot = MagicMock(send_message=AsyncMock(side_effect=[_flood(0), _ok()]))
    sent = await fn.broadcast_storm_announce(bot, [{"telegram_id": TG}], NOW + timedelta(hours=6))
    assert sent == 1 and bot.send_message.await_count == 2


async def test_storm_announce_counts_only_delivered(farm):
    bot = MagicMock(send_message=AsyncMock(side_effect=_blocked()))
    sent = await fn.broadcast_storm_announce(bot, [{"telegram_id": TG}], NOW + timedelta(hours=6))
    assert sent == 0
    farm["unreachable"].assert_awaited_once_with(TG)


@pytest.fixture
def traffic(monkeypatch):
    async def get_user(telegram_id):
        return {"telegram_id": telegram_id, "language": "ru"}
    monkeypatch.setattr(database, "get_user", get_user)
    unreachable = AsyncMock()
    monkeypatch.setattr(database, "mark_user_unreachable", unreachable)
    return unreachable


async def test_traffic_notification_retries_a_short_flood_wait(traffic):
    bot = MagicMock(send_message=AsyncMock(side_effect=[_flood(0), _ok()]))
    await tm._send_traffic_notification(bot, TG, 400 * 1024**2, 1024**3, premium=True)
    assert bot.send_message.await_count == 2


async def test_blocked_traffic_user_is_marked_unreachable(traffic):
    bot = MagicMock(send_message=AsyncMock(side_effect=_blocked()))
    await tm._send_traffic_notification(bot, TG, 0, 0, premium=False)
    traffic.assert_awaited_once_with(TG)
