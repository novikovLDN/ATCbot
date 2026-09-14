"""TG-RT-7 (port of refactor 92028c35): safe_send_message must not drop a
message on a short 429 flood wait — sleep it out and send ONCE more; long
waits are still dropped; no retry storm."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramRetryAfter


def _flood(seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=MagicMock(), message=f"Too Many Requests: retry after {seconds}",
                              retry_after=seconds)


async def test_short_flood_wait_is_slept_out_and_sent_once_more(monkeypatch):
    from app.utils import telegram_safe
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[_flood(2), "sent"])
    assert await telegram_safe.safe_send_message(bot, 1, "x") == "sent"
    sleep.assert_awaited_once_with(2)
    assert bot.send_message.await_count == 2


async def test_flood_wait_retries_only_once_and_long_waits_are_dropped(monkeypatch):
    from app.utils import telegram_safe
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[_flood(1), _flood(1), "never"])
    assert await telegram_safe.safe_send_message(bot, 1, "x") is None     # no retry storm
    assert bot.send_message.await_count == 2

    bot.send_message = AsyncMock(side_effect=[_flood(60), "never"])
    sleep.reset_mock()
    assert await telegram_safe.safe_send_message(bot, 1, "x") is None
    assert bot.send_message.await_count == 1 and sleep.await_count == 0
