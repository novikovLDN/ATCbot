"""Daily-revenue milestone DMs never fired in production.

`_on_payment_approved` (5k/10k/… ₽ за день) hangs off the bus event
`payment:approved`, and the only publisher of that event lived inside
`approve_payment_atomic` — a function with zero call sites. Real money
settles in `finalize_purchase` / `mark_pending_purchase_paid`, which
published nothing, so no milestone could ever be announced.

Two more defects hid it even when the event was faked: delivery was
browser web-push only (silent when no browser is subscribed, while the
dashboard labels the section «Telegram DM»), and the revenue window was
«hours since MSK midnight» rounded to whole hours instead of midnight.

The tests drive the real handler, so they fail on the old code for the
defect itself, not for a missing helper.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import database
import database.subscriptions as db_subs
from app.events import bus
from app.services import admin_notifier, push_notifications

MSK = timezone(timedelta(hours=3))
ADMIN = 999


class _Pool:
    """Pool whose only connection reports a fixed UPDATE result."""

    def __init__(self, result: str):
        self.result = result

    def acquire(self):
        result = self.result

        class _Acquire:
            async def __aenter__(self):
                return SimpleNamespace(execute=AsyncMock(return_value=result))

            async def __aexit__(self, *exc):
                return False

        return _Acquire()


class _FakeRedis:
    """Enough of the Redis surface for the milestone bookkeeping."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


@pytest.fixture
def events():
    q = bus.subscribe()
    yield q
    bus.unsubscribe(q)


def approvals(q) -> list[dict]:
    out = []
    while not q.empty():
        e = q.get_nowait()
        if e.get("type") == "payment:approved":
            out.append(e)
    return out


@pytest.fixture
def notifier(monkeypatch):
    """A notifier wired to a fake bot, a fake Redis and a mute web push."""
    admin_notifier._state.fired_milestones = {}
    redis = _FakeRedis()
    monkeypatch.setattr(admin_notifier, "_redis", AsyncMock(return_value=redis), raising=False)
    monkeypatch.setattr(admin_notifier.config, "ADMIN_TELEGRAM_ID", ADMIN)
    monkeypatch.setattr(push_notifications, "send_to_all", AsyncMock(return_value={"sent": 0}))
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(bot=bot, redis=redis)


def set_revenue(monkeypatch, rubles: float, *, seen: dict | None = None):
    async def fake_revenue(hours, since=None, until=None):
        if seen is not None:
            seen["hours"] = hours
            seen["since"] = since
            seen["until"] = until
        return {"revenue_rubles": rubles, "payments_count": 1, "avg_check_rubles": rubles}

    monkeypatch.setattr(database, "get_revenue_for_period", fake_revenue)


async def test_paid_purchase_publishes_payment_approved(monkeypatch, events):
    """The settlement path must announce the payment — the milestone
    notifier and the dashboard both listen for this event."""
    monkeypatch.setattr(db_subs, "get_pool", AsyncMock(return_value=_Pool("UPDATE 1")))

    assert await db_subs.mark_pending_purchase_paid("p-1") is True

    published = approvals(events)
    assert len(published) == 1
    assert published[0]["purchase_id"] == "p-1"


async def test_already_paid_purchase_publishes_nothing(monkeypatch, events):
    """A retried webhook flips no row — and must announce no payment."""
    monkeypatch.setattr(db_subs, "get_pool", AsyncMock(return_value=_Pool("UPDATE 0")))

    assert await db_subs.mark_pending_purchase_paid("p-1") is False

    assert approvals(events) == []


async def test_milestone_reaches_telegram(monkeypatch, notifier):
    """Web push alone is silent when no browser is subscribed."""
    set_revenue(monkeypatch, 12_300.0)

    await admin_notifier._on_payment_approved(notifier.bot, {})

    assert notifier.bot.send_message.await_count == 1, "milestone must reach Telegram"
    chat_id, text = notifier.bot.send_message.await_args.args[:2]
    assert chat_id == ADMIN
    assert "10 000 ₽ за день" in text


async def test_milestone_fires_once_per_day(monkeypatch, notifier):
    set_revenue(monkeypatch, 12_300.0)

    await admin_notifier._on_payment_approved(notifier.bot, {})
    await admin_notifier._on_payment_approved(notifier.bot, {})

    assert notifier.bot.send_message.await_count == 1


async def test_milestone_survives_a_restart(monkeypatch, notifier):
    """A deploy in the middle of the day must not replay 5k/10k/…"""
    set_revenue(monkeypatch, 12_300.0)
    await admin_notifier._on_payment_approved(notifier.bot, {})
    notifier.bot.send_message.reset_mock()
    push_notifications.send_to_all.reset_mock()

    admin_notifier._state.fired_milestones = {}  # what a restart leaves behind
    await admin_notifier._on_payment_approved(notifier.bot, {})

    assert notifier.bot.send_message.await_count == 0
    assert push_notifications.send_to_all.await_count == 0


async def test_revenue_window_starts_at_msk_midnight(monkeypatch, notifier):
    """Rounding «hours since midnight» measured from 00:20 at 10:20 MSK."""
    seen: dict = {}
    set_revenue(monkeypatch, 100.0, seen=seen)

    await admin_notifier._on_payment_approved(notifier.bot, {})

    since = seen["since"]
    assert since is not None, "window must be bounded by midnight, not by whole hours"
    assert since.tzinfo is not None, "DB contract: aware UTC in"
    local = since.astimezone(MSK)
    assert (local.hour, local.minute, local.second) == (0, 0, 0)
    assert local.date() == datetime.now(MSK).date()


class TestTodayEndpoint:
    """/metrics/today shows the owner the very number the notifier
    compares against the ladder — so «почему не пришло» is answerable
    from the screen."""

    async def test_reports_progress_to_the_next_milestone(self, monkeypatch, notifier):
        from app.api.dashboard.routes import metrics as routes

        set_revenue(monkeypatch, 12_300.0)
        await admin_notifier.remember_milestones(admin_notifier.msk_day_key(), {5_000, 10_000})

        today = await routes._today()

        assert today["revenue_kopecks"] == 1_230_000
        assert today["milestones"]["fired"] == [5_000, 10_000]
        assert today["milestones"]["next"] == 15_000
        assert today["milestones"]["notifications_enabled"] is True

    async def test_no_next_milestone_above_the_ladder(self, monkeypatch, notifier):
        from app.api.dashboard.routes import metrics as routes

        set_revenue(monkeypatch, 99_000.0)

        assert (await routes._today())["milestones"]["next"] is None


class TestDailySummary:
    """«Итог дня» — one DM at 23:59 MSK, once per day even across a
    restart, and never before the day is over."""

    def test_body_compares_with_yesterday(self):
        title, body = admin_notifier.format_daily_summary(
            today={"revenue_rubles": 27_400.0, "payments_count": 48, "avg_check_rubles": 570.8},
            yesterday={"revenue_rubles": 21_920.0},
            new_users=132,
            fired=[10_000, 5_000, 25_000],
        )
        assert title == "📊 Итог дня: 27 400 ₽"
        assert "Вчера было 21 920 ₽ · +25 %" in body
        assert "Оплат 48 · средний чек 571 ₽" in body
        assert "Новых пользователей 132" in body
        assert "Планки: 5k · 10k · 25k" in body

    def test_yesterday_without_revenue_has_no_percentage(self):
        _, body = admin_notifier.format_daily_summary(
            today={"revenue_rubles": 500.0, "payments_count": 1, "avg_check_rubles": 500.0},
            yesterday={"revenue_rubles": 0.0},
            new_users=0,
            fired=[],
        )
        assert "Вчера было 0 ₽ · —" in body
        assert "Планки" not in body

    async def test_not_due_before_2359(self, monkeypatch, notifier):
        set_revenue(monkeypatch, 1_000.0)
        monkeypatch.setattr(admin_notifier, "_send", AsyncMock())
        monkeypatch.setattr(admin_notifier, "datetime", _FrozenMsk(hour=23, minute=58))

        assert await admin_notifier._send_daily_summary_if_due(notifier.bot) is False
        assert admin_notifier._send.await_count == 0

    async def test_sent_once_per_day(self, monkeypatch, notifier):
        set_revenue(monkeypatch, 1_000.0)
        monkeypatch.setattr(admin_notifier, "_send", AsyncMock())
        monkeypatch.setattr(admin_notifier, "datetime", _FrozenMsk(hour=23, minute=59))

        assert await admin_notifier._send_daily_summary_if_due(notifier.bot) is True
        admin_notifier._state.summary_sent_day = None  # a restart forgets it
        assert await admin_notifier._send_daily_summary_if_due(notifier.bot) is False
        assert admin_notifier._send.await_count == 1


class _FrozenMsk:
    """datetime stand-in whose now() sits at a chosen MSK time today."""

    def __init__(self, *, hour: int, minute: int):
        self.at = datetime.now(MSK).replace(hour=hour, minute=minute, second=0, microsecond=0)

    def now(self, tz=None):
        return self.at.astimezone(tz) if tz else self.at
