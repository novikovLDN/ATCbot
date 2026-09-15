"""Trial end for a user whose (only) subscription row is already bypass-only.

Production: "Trial cleanup skipped: user has active paid subscription …
paid_expires_at=2036-09-…". get_active_paid_subscription counted the bypass-only
row (10-year placeholder expires_at) as paid. When the ended trial row had been
turned into bypass-only by a path that does not send "trial ended"
(check_and_disable_expired_subscription when the user opens the bot,
ensure_bypass_only_subscription when GB are bought right after the end), the
trial worker skipped the user for good: no "trial ended", no 30 % offer.

Now a bypass-only row is never "paid"; the trial worker sends the one
"trial ended" notice (same users.trial_completed_sent claim as everywhere) and
leaves the row and the bypass entity alone.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import trial_notifications as tn
from app.i18n import get_text

TG = 7_700_301
NOW = datetime.now(timezone.utc)
FALSE_CLAIMS = ("VPN перестанет", "VPN будет отключ", "вернётся к блокировкам")


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class Conn:
    """Answers like Postgres for the user's single subscriptions row."""

    def __init__(self, sub: dict, *, completed_sent=False):
        self.sub = sub
        self.completed_sent = completed_sent
        self.sub_writes: list = []

    def transaction(self):
        class _Tx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False
        return _Tx()

    async def fetchrow(self, sql, *args):
        if "SELECT expires_at FROM subscriptions" in sql:          # get_active_paid_subscription
            s = self.sub
            ok = (s["status"] == "active" and s["expires_at"] > _naive(args[1])
                  and s["source"] not in ("trial",))
            if "is_bypass_only" in sql:
                ok = ok and not s["is_bypass_only"]
            if "'bypass_only'" in sql:
                ok = ok and s["source"] != "bypass_only"
            return {"expires_at": s["expires_at"]} if ok else None
        if "AND source = 'trial'" in sql:                            # should_expire_trial
            s = self.sub
            return dict(s) if s["source"] == "trial" and s["status"] == "active" else None
        if "trial_completed_sent" in sql:                            # should_send_completion_notification
            return {"trial_used_at": _naive(NOW - timedelta(days=3)), "trial_completed_sent": self.completed_sent}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetchval(self, sql, *args):
        if "SELECT remnawave_uuid FROM subscriptions" in sql:
            return self.sub.get("remnawave_uuid")
        if "FROM subscriptions" in sql and "bypass_only" in sql:
            s = self.sub
            return True if s["status"] == "active" and (s["is_bypass_only"] or s["source"] == "bypass_only") else None
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql, *args):
        if "SET trial_completed_sent = TRUE" in sql:
            if self.completed_sent:
                return "UPDATE 0"
            self.completed_sent = True
            return "UPDATE 1"
        self.sub_writes.append(sql)
        return "UPDATE 1"


class Pool:
    def __init__(self, conn):
        self.conn = conn
        self.held = 0          # connections checked out right now

    def acquire(self):
        conn = self.conn
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.held += 1
                return conn

            async def __aexit__(self, *exc):
                pool.held -= 1
                return False
        return _Ctx()


BYPASS_ONLY = {"status": "active", "source": "bypass_only", "is_bypass_only": True,
               "expires_at": _naive(NOW + timedelta(days=3650)), "uuid": None}
PAID = {"status": "active", "source": "payment", "is_bypass_only": False,
        "expires_at": _naive(NOW + timedelta(days=20)), "uuid": "u-1"}


@pytest.fixture
def env(monkeypatch):
    sent = AsyncMock(return_value=MagicMock(message_id=1))
    disable = AsyncMock()
    discount = AsyncMock(return_value=True)
    monkeypatch.setattr(tn, "safe_send_message", sent)
    monkeypatch.setattr(tn, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "create_user_discount", discount, raising=False)
    from app.services import remnawave_premium
    monkeypatch.setattr(remnawave_premium, "disable_premium_user", disable)
    return {"sent": sent, "disable": disable, "discount": discount}


def _row():
    return {"telegram_id": TG, "uuid": None,
            "trial_used_at": _naive(NOW - timedelta(days=3, hours=1)),
            "trial_expires_at": _naive(NOW - timedelta(hours=1))}


async def test_bypass_only_row_gets_trial_end_once_and_keeps_its_bypass(env):
    conn = Conn(dict(BYPASS_ONLY))
    await tn._process_single_trial_expiration(MagicMock(), Pool(conn), _row(), NOW)

    texts = [c.args[2] for c in env["sent"].await_args_list]
    assert texts == [get_text("ru", "trial.expired")]
    assert not [c for c in FALSE_CLAIMS if c in texts[0]]
    env["discount"].assert_awaited_once()
    assert conn.sub_writes == [], "the bypass-only row must not be touched"
    env["disable"].assert_not_awaited()
    assert conn.sub == BYPASS_ONLY

    await tn._process_single_trial_expiration(MagicMock(), Pool(conn), _row(), NOW)
    assert env["sent"].await_count == 1, "trial ended is sent once"


async def test_trial_with_bypass_ends_with_one_message(env, monkeypatch):
    """#1 / #23: the trial worker used to send «основная подписка закончилась»
    (without checking its UPDATE) and then «пробный завершён»."""
    from app.services import remnawave_service
    monkeypatch.setattr(remnawave_service, "extend_remnawave_for_bypass_bg", lambda *_a, **_k: None)
    trial = {"status": "active", "source": "trial", "is_bypass_only": False,
             "expires_at": _naive(NOW - timedelta(hours=1)), "uuid": "u-1", "remnawave_uuid": "rw-1"}
    conn = Conn(dict(trial))
    await tn._process_single_trial_expiration(MagicMock(), Pool(conn), dict(_row(), uuid="u-1"), NOW)

    texts = [c.args[2] for c in env["sent"].await_args_list]
    assert texts == [get_text("ru", "trial.expired")]
    assert any("is_bypass_only = TRUE" in w for w in conn.sub_writes), "the GB keep working (bypass-only)"


@pytest.mark.parametrize("bypass", [True, False])
async def test_trial_expiry_holds_no_connection_during_the_panel_call_or_the_send(env, monkeypatch, bypass):
    """#17: the pool connection stayed checked out during disable_premium_user
    (HTTP to the panel) and the Telegram sends."""
    from app.services import remnawave_premium, remnawave_service
    monkeypatch.setattr(remnawave_service, "extend_remnawave_for_bypass_bg", lambda *_a, **_k: None)
    trial = {"status": "active", "source": "trial", "is_bypass_only": False,
             "expires_at": _naive(NOW - timedelta(hours=1)), "uuid": "u-1",
             "remnawave_uuid": "rw-1" if bypass else None}
    pool = Pool(Conn(dict(trial)))
    held_during = {}

    async def disable(tg):
        held_during["panel"] = pool.held

    async def send(bot, tg, text, **kw):
        held_during["send"] = pool.held
        return MagicMock(message_id=1)
    monkeypatch.setattr(remnawave_premium, "disable_premium_user", disable)
    monkeypatch.setattr(tn, "safe_send_message", send)

    await tn._process_single_trial_expiration(MagicMock(), pool, dict(_row(), uuid="u-1"), NOW)

    assert held_during == {"panel": 0, "send": 0}


async def test_bypass_only_row_already_told_gets_nothing(env):
    conn = Conn(dict(BYPASS_ONLY), completed_sent=True)
    await tn._process_single_trial_expiration(MagicMock(), Pool(conn), _row(), NOW)
    env["sent"].assert_not_awaited()
    assert conn.sub_writes == []


async def test_real_paid_subscription_is_skipped_and_the_trial_marked_completed(env):
    """Nothing expired, no notice — and the trial is marked completed so the
    worker does not re-select this user on every pass (production 2026-09-15)."""
    conn = Conn(dict(PAID))
    await tn._process_single_trial_expiration(MagicMock(), Pool(conn), _row(), NOW)
    env["sent"].assert_not_awaited()
    env["disable"].assert_not_awaited()
    assert conn.sub_writes == [] and conn.completed_sent is True

    await tn._process_single_trial_expiration(MagicMock(), Pool(conn), _row(), NOW)
    env["sent"].assert_not_awaited()
    assert conn.sub_writes == [], "the paid row is never touched"


@pytest.mark.parametrize("sub,paid", [
    (BYPASS_ONLY, False),
    (dict(BYPASS_ONLY, is_bypass_only=False), False),     # source='bypass_only' alone
    (dict(BYPASS_ONLY, source="payment"), False),          # is_bypass_only alone (placeholder)
    (PAID, True),
])
async def test_get_active_paid_subscription_ignores_bypass_only(sub, paid):
    row = await database.get_active_paid_subscription(Conn(dict(sub)), TG, NOW)
    assert (row is not None) is paid
