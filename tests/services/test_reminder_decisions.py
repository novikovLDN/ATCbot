"""Paid / free-access reminder decisions (docs/notifications/matrix.md #3, #18).

should_send_reminder is pure: these pin which reminder a subscription row gets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app import i18n
from app.services.notifications import service as ns

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _row(**kw):
    row = {"telegram_id": 1, "source": "payment", "subscription_type": "basic",
           "admin_grant_days": None, "last_action_type": "renewal",
           "reminder_7d_sent": False, "reminder_3d_sent": False, "reminder_1d_sent": False,
           "reminder_3h_sent": False, "reminder_6h_sent": False, "reminder_24h_sent": False}
    row.update(kw)
    return row


@pytest.mark.parametrize("left,kind", [
    (timedelta(days=7), "reminder_7d"), (timedelta(days=3), "reminder_3d"),
    (timedelta(hours=24), "reminder_1d"), (timedelta(hours=3), "reminder_3h"),
])
def test_days_added_on_top_of_a_paid_subscription_keep_every_paid_reminder(left, kind):
    """#3: the last history row 'admin_grant' (days added by an admin / promo link
    on top of a paid subscription) made the row a «free grant» with neither 1 nor
    7 days → not one paid reminder."""
    d = ns.should_send_reminder(_row(source="admin", last_action_type="admin_grant",
                                     expires_at=NOW + left), NOW)
    assert d.should_send and d.reminder_type.value == kind


@pytest.mark.parametrize("left", [timedelta(hours=2, minutes=5), timedelta(hours=3), timedelta(hours=3, minutes=55)])
def test_paid_3h_window_is_wider_than_the_pass_interval(left):
    """#14: a 1 h window with a pass every 45 min — one missed pass lost the 3 h
    reminder. Now ±1 h with a pass every 15 min."""
    import reminders
    d = ns.should_send_reminder(_row(expires_at=NOW + left), NOW)
    assert d.should_send and d.reminder_type.value == "reminder_3h"
    assert reminders.REMINDERS_INTERVAL_SECONDS <= 15 * 60
    # the window survives at least three passes
    assert timedelta(hours=2) >= 3 * timedelta(seconds=reminders.REMINDERS_INTERVAL_SECONDS)


@pytest.mark.parametrize("days", [3, 7, 14, 30])
def test_free_days_get_the_24h_reminder_whatever_their_number(days):
    """#18: only exactly 7 days got a reminder (3 / 14 / 30 — nothing)."""
    d = ns.should_send_reminder(_row(source="admin", admin_grant_days=days,
                                     expires_at=NOW + timedelta(hours=24)), NOW)
    assert d.should_send and d.reminder_type == ns.ReminderType.ADMIN_7DAYS_24H
    # and no paid reminder at 7 / 3 days for free access
    for left in (timedelta(days=7), timedelta(days=3), timedelta(hours=3)):
        assert not ns.should_send_reminder(_row(source="admin", admin_grant_days=days,
                                                expires_at=NOW + left), NOW).should_send


def test_one_free_day_keeps_its_6h_reminder():
    d = ns.should_send_reminder(_row(source="admin", admin_grant_days=1,
                                     expires_at=NOW + timedelta(hours=6)), NOW)
    assert d.should_send and d.reminder_type == ns.ReminderType.ADMIN_1DAY_6H


@pytest.fixture
def paid_pass(monkeypatch):
    """One paid subscription 7 days before its end; records claim / send / release."""
    import reminders
    import database.subscriptions as db_subs
    from app.services import automated_notifications as an
    st = {"order": [], "claim": True, "results": [], "blocked": False}
    end = datetime.now(timezone.utc) + timedelta(days=7) - timedelta(minutes=10)

    async def claim(tg, rtype, expires_at):
        st["order"].append(("claim", rtype.value, expires_at))
        return st["claim"]

    async def release(tg, rtype, expires_at):
        st["order"].append(("release", rtype.value, expires_at))

    async def send(bot, tg, text, **kw):
        st["order"].append(("send", text[:15]))
        return st["results"].pop(0) if st["results"] else MagicMock()

    monkeypatch.setattr(reminders, "_claim_reminder", claim)
    monkeypatch.setattr(reminders, "_release_reminder", release)
    monkeypatch.setattr(reminders, "safe_send_message", send)
    monkeypatch.setattr(reminders, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(db_subs, "is_user_blocked", AsyncMock(side_effect=lambda tg: st["blocked"]))
    monkeypatch.setattr(an, "is_notification_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(an, "get_notification_text", AsyncMock(return_value=None))
    monkeypatch.setattr(an, "log_notification_send", AsyncMock())
    monkeypatch.setattr(an, "get_trigger_config", AsyncMock(return_value={}))
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", AsyncMock(), raising=False)
    monkeypatch.setattr(database, "get_subscriptions_for_reminders",
                        AsyncMock(return_value=[_row(expires_at=end)]))
    st["end"] = end
    return reminders, st


async def test_paid_reminder_is_claimed_for_the_period_before_the_send(paid_pass):
    reminders, st = paid_pass
    await reminders.send_smart_reminders(MagicMock())
    assert [o[0] for o in st["order"]] == ["claim", "send"]
    assert st["order"][0][2] == st["end"], "claimed for the period this pass saw"


async def test_renewed_inside_the_pass_no_old_date_reminder(paid_pass):
    """#16: the claim is bound to the snapshot's expires_at — a renewal since then
    (new date) leaves nothing to claim, so no reminder with the old date."""
    reminders, st = paid_pass
    st["claim"] = False
    await reminders.send_smart_reminders(MagicMock())
    assert [o[0] for o in st["order"]] == ["claim"]


@pytest.mark.parametrize("blocked,released", [(False, True), (True, False)])
async def test_failed_send_is_released_unless_blocked(paid_pass, blocked, released):
    reminders, st = paid_pass
    st["results"] = [None]
    st["blocked"] = blocked
    await reminders.send_smart_reminders(MagicMock())
    assert ("release" in [o[0] for o in st["order"]]) is released


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_free_access_24h_text_has_the_price_from_the_table(monkeypatch, lang):
    import reminders
    from app.services import automated_notifications as an
    from app.services import pricing
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(reminders, "safe_send_message", sent)
    monkeypatch.setattr(reminders, "resolve_user_language", AsyncMock(return_value=lang))
    monkeypatch.setattr(ns, "mark_reminder_sent", AsyncMock())
    monkeypatch.setattr(an, "log_notification_send", AsyncMock())
    monkeypatch.setattr(an, "get_trigger_config", AsyncMock(return_value={}))
    monkeypatch.setattr(pricing, "get_effective_price", AsyncMock(return_value=None))
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", AsyncMock(), raising=False)
    sub = _row(source="admin", admin_grant_days=14,
               expires_at=datetime.now(timezone.utc) + timedelta(hours=24) - timedelta(minutes=5))
    monkeypatch.setattr(database, "get_subscriptions_for_reminders", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(reminders, "_claim_reminder", AsyncMock(return_value=True), raising=False)

    await reminders.send_smart_reminders(MagicMock())

    text = sent.await_args.args[2]
    price = config.TARIFFS["basic"][30]["price"]
    assert text == i18n.get_text(lang, "reminder.admin_7days_24h", price=price)
    assert "{price}" not in text
