"""Unit tests for healthcheck admin-alert PII safety + cooldown."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

import healthcheck


@pytest.mark.asyncio
async def test_message_truncated_to_safe_length(monkeypatch):
    """Long messages (which could leak stack traces) are truncated."""
    # Force the cooldown window to be in the deep past so the next send fires.
    monkeypatch.setattr(healthcheck, "_last_alert_at", -1e9)
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    long_msg = "X" * (healthcheck._ADMIN_ALERT_MAX_LEN * 3)
    await healthcheck._send_admin_alert(bot, long_msg)
    sent_text = bot.send_message.await_args.args[1]
    assert len(sent_text) <= healthcheck._ADMIN_ALERT_MAX_LEN + 1  # +1 for ellipsis
    assert sent_text.endswith("…")


@pytest.mark.asyncio
async def test_short_message_passes_through(monkeypatch):
    # Force the cooldown window to be in the deep past so the next send fires.
    monkeypatch.setattr(healthcheck, "_last_alert_at", -1e9)
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    await healthcheck._send_admin_alert(bot, "DB down: TimeoutError")
    assert bot.send_message.await_args.args[1] == "DB down: TimeoutError"


@pytest.mark.asyncio
async def test_cooldown_prevents_spam(monkeypatch):
    monkeypatch.setattr(healthcheck, "_last_alert_at", time.monotonic())  # just sent
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    await healthcheck._send_admin_alert(bot, "DB down")
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_failure_swallowed(monkeypatch):
    """Alert delivery failure must not raise (would crash the worker loop)."""
    # Force the cooldown window to be in the deep past so the next send fires.
    monkeypatch.setattr(healthcheck, "_last_alert_at", -1e9)
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    # Must not raise.
    await healthcheck._send_admin_alert(bot, "DB down")
