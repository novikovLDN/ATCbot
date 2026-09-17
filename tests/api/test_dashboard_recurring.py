"""Dashboard user card: «Выключить автопродление».

Отмена рекуррентной подписки — только по запросу в поддержку, поэтому
кнопка живёт в админ-карточке пользователя, а не у него в боте. Здесь
проверяется то, что делает эта кнопка: останавливает списания у Platega,
пишет аудит-лог и не врёт про результат, если провайдер отказал.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import platega_service
from app.api.dashboard.routes import users
from database import platega_subscriptions as psub_db

ADMIN = {"sub": "777"}
TG = 42


def sub(sub_id: str, status: str = "Active") -> dict:
    return {
        "subscription_id": sub_id,
        "status": status,
        "amount_kopecks": 19900,
        "interval_days": 30,
        "tariff_type": "basic",
        "next_charge_at": None,
        "last_charge_at": None,
        "charges_success": 2,
        "charges_failed": 0,
        "created_at": None,
    }


@pytest.fixture
def env(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", audit, raising=False)
    monkeypatch.setattr(users.bus, "publish", MagicMock())
    cancel = AsyncMock(return_value=True)
    monkeypatch.setattr(platega_service, "cancel_subscription", cancel)
    status = AsyncMock()
    monkeypatch.setattr(psub_db, "update_subscription_status", status)
    return MagicMock(audit=audit, cancel=cancel, status=status, monkeypatch=monkeypatch)


def with_subs(env, rows):
    env.monkeypatch.setattr(psub_db, "get_user_live_subscriptions", AsyncMock(return_value=rows))


async def test_card_shows_the_live_subscriptions(env):
    with_subs(env, [sub("s-1"), sub("s-2", "PastDue")])

    out = await users.user_recurring(telegram_id=TG)

    assert [s["subscription_id"] for s in out["subscriptions"]] == ["s-1", "s-2"]
    assert out["subscriptions"][1]["status"] == "PastDue"


async def test_cancel_stops_the_charges_and_audits(env):
    with_subs(env, [sub("s-1")])

    out = await users.user_recurring_cancel(
        body=users.RecurringCancel(), telegram_id=TG, admin=ADMIN,
    )

    assert out == {"ok": True, "cancelled": ["s-1"], "failed": []}
    env.cancel.assert_awaited_once_with("s-1")
    action, admin_id = env.audit.await_args.args
    assert (action, admin_id, env.audit.await_args.kwargs["target_user"]) == (
        "admin_recurring_cancel", 777, TG,
    )
    assert json.loads(env.audit.await_args.kwargs["details"])["cancelled"] == ["s-1"]


async def test_cancel_one_of_several_leaves_the_others_alone(env):
    with_subs(env, [sub("s-1"), sub("s-2")])

    out = await users.user_recurring_cancel(
        body=users.RecurringCancel(subscription_id="s-2"), telegram_id=TG, admin=ADMIN,
    )

    assert out["cancelled"] == ["s-2"]
    env.cancel.assert_awaited_once_with("s-2")


async def test_a_refused_cancel_is_reported_as_failure(env):
    """Provider said no — the card must not claim the charges stopped."""
    with_subs(env, [sub("s-1")])
    env.cancel.return_value = False

    out = await users.user_recurring_cancel(
        body=users.RecurringCancel(), telegram_id=TG, admin=ADMIN,
    )

    assert out == {"ok": False, "cancelled": [], "failed": ["s-1"]}
    env.status.assert_not_awaited()  # local status must not say Cancelled


async def test_local_status_is_written_only_after_the_provider_confirms(env):
    with_subs(env, [sub("s-1")])

    await users.user_recurring_cancel(
        body=users.RecurringCancel(), telegram_id=TG, admin=ADMIN,
    )

    env.status.assert_awaited_once_with("s-1", "Cancelled")


async def test_nothing_to_cancel_is_not_an_error(env):
    with_subs(env, [])

    out = await users.user_recurring_cancel(
        body=users.RecurringCancel(), telegram_id=TG, admin=ADMIN,
    )

    assert out["ok"] is True and out["note"] == "nothing_to_cancel"
    env.cancel.assert_not_awaited()
    env.audit.assert_not_awaited()


async def test_live_subscriptions_query_includes_past_due(monkeypatch):
    """Настоящая функция, без мока: PastDue должен попадать в выборку —
    именно её админ и приходит выключать."""
    captured = {}

    class _Conn:
        async def fetch(self, sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return [sub("s-1", "PastDue")]

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()

                async def __aexit__(self, *a):
                    return False

            return _Ctx()

    monkeypatch.setattr(psub_db._core, "DB_READY", True)
    monkeypatch.setattr(psub_db, "get_pool", AsyncMock(return_value=_Pool()))

    rows = await psub_db.get_user_live_subscriptions(TG)

    assert [r["status"] for r in rows] == ["PastDue"]
    assert captured["args"] == (TG, ["Active", "PendingAgreement", "PastDue"])
