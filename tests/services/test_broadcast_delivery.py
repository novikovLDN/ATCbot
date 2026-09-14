"""HOW_IT_WORKS P2 (§9.4): broadcasts.

- get_users_by_segment did not filter users.is_reachable = FALSE, so every
  broadcast re-sent to users who blocked the bot (3 attempts each).
- broadcast_sender sent text with a bare bot.send_message (not
  safe_send_message) and never marked a blocked user unreachable.
- A scheduled broadcast could go out twice: the row lock of
  fetch_due_scheduled is released immediately (no transaction) and the run was
  marked only AFTER the send started, so a failed mark (or an overlapping
  instance) sent it again. The worker now CLAIMS the run first (one guarded
  UPDATE) and sends only if the claim succeeded.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import database


# ── segment: unreachable users are skipped ───────────────────────────


class _Conn:
    async def fetch(self, sql, *args):
        if "is_reachable" in sql:
            return [{"telegram_id": 2}]
        return [{"telegram_id": 1}, {"telegram_id": 2}, {"telegram_id": 3}]


class _Pool:
    @asynccontextmanager
    async def acquire(self):
        yield _Conn()


@pytest.mark.parametrize("segment", ["all_users", "no_subscription"])
async def test_segment_skips_unreachable_users(monkeypatch, segment):
    import database.admin as admin_db
    monkeypatch.setattr(admin_db, "get_pool", AsyncMock(return_value=_Pool()))
    assert await admin_db.get_users_by_segment(segment) == [1, 3]


# ── sender ──────────────────────────────────────────────────────────


async def test_text_broadcast_goes_through_safe_send_message(monkeypatch):
    from app.services import broadcast_sender as bs
    safe = AsyncMock(return_value=SimpleNamespace(message_id=9))
    monkeypatch.setattr(bs, "safe_send_message", safe)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    assert await bs._safe_send_with_buttons(bot, 1, "hi", asyncio.Semaphore(1)) == 9
    safe.assert_awaited_once()
    bot.send_message.assert_not_awaited()


async def test_blocked_user_media_is_marked_unreachable_and_not_retried(monkeypatch):
    from app.services import broadcast_sender as bs
    mark = AsyncMock()
    monkeypatch.setattr(database, "mark_user_unreachable", mark)
    bot = MagicMock()
    bot.send_photo = AsyncMock(side_effect=TelegramForbiddenError(method=MagicMock(), message="bot was blocked"))
    out = await bs._safe_send_with_buttons(bot, 5, "hi", asyncio.Semaphore(1), photo_file_id="ph")
    assert out is None
    assert bot.send_photo.await_count == 1
    mark.assert_awaited_once_with(5)


async def test_flood_wait_is_still_retried_for_text(monkeypatch):
    from app.services import broadcast_sender as bs
    monkeypatch.setattr(bs.asyncio, "sleep", AsyncMock())
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[
        TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=0),
        SimpleNamespace(message_id=11),
    ])
    assert await bs._safe_send_with_buttons(bot, 1, "hi", asyncio.Semaphore(1)) == 11
    assert bot.send_message.await_count == 2


async def test_safe_send_message_swallows_flood_wait_by_default():
    from app.utils.telegram_safe import safe_send_message
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=TelegramRetryAfter(method=MagicMock(), message="f", retry_after=1))
    assert await safe_send_message(bot, 1, "x") is None


# ── scheduled: claim before sending ──────────────────────────────────


def _sched():
    return {"id": 3, "segment": "all_users", "message": "m", "title": "t", "created_by": 1,
            "scheduled_at": datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc), "buttons": None}


@pytest.fixture
def worker(monkeypatch):
    from app.services import scheduled_broadcasts_worker as w
    import app.services.broadcast_sender as bs
    import app.api.dashboard.routes.broadcasts as routes
    monkeypatch.setattr(routes, "normalize_premium_emoji", lambda s: s)
    monkeypatch.setattr(routes, "_build_reply_markup", lambda *a, **k: None)
    send = AsyncMock(return_value={"sent": 1})
    monkeypatch.setattr(bs, "send_broadcast", send)
    monkeypatch.setattr(database, "get_users_by_segment", AsyncMock(return_value=[1]))
    monkeypatch.setattr(database, "create_broadcast", AsyncMock(return_value=77))
    monkeypatch.setattr(database, "record_scheduled_result", AsyncMock(), raising=False)
    return w, send


async def test_scheduled_run_not_claimed_is_not_sent(monkeypatch, worker):
    w, send = worker
    monkeypatch.setattr(database, "claim_scheduled_run", AsyncMock(return_value=False), raising=False)
    await w._dispatch_one(MagicMock(), _sched())
    await asyncio.sleep(0)
    database.create_broadcast.assert_not_awaited()
    send.assert_not_awaited()


async def test_scheduled_claim_error_is_not_sent(monkeypatch, worker):
    w, send = worker
    monkeypatch.setattr(database, "claim_scheduled_run", AsyncMock(side_effect=RuntimeError("db")), raising=False)
    await w._dispatch_one(MagicMock(), _sched())
    await asyncio.sleep(0)
    send.assert_not_awaited()


async def test_scheduled_run_is_claimed_before_it_is_sent(monkeypatch, worker):
    w, send = worker
    order = []
    claim = AsyncMock(side_effect=lambda *a, **k: order.append("claim") or True)
    monkeypatch.setattr(database, "claim_scheduled_run", claim, raising=False)
    database.create_broadcast.side_effect = lambda **k: order.append("create") or 77
    await w._dispatch_one(MagicMock(), _sched())
    await asyncio.sleep(0)
    assert order == ["claim", "create"]
    claim.assert_awaited_once_with(3, _sched()["scheduled_at"])
    send.assert_awaited_once()
    database.record_scheduled_result.assert_awaited_with(3, last_broadcast_id=77, error=None)
