"""Trial pre-expiry reminders (24 h / 3 h / last hour): claimed before the send,
a temporary Telegram failure is retried, a block is final
(docs/notifications/matrix.md #15, #24). Texts are not touched here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import trial_notifications as tn
from app.services import automated_notifications as an

TG = 7_700_777
FLAGS = ("trial_notif_24h_sent", "trial_notif_3h_sent", "trial_notif_71h_sent")


class FlagConn:
    def __init__(self, st):
        self.st = st

    async def execute(self, sql, *args):
        s = " ".join(sql.split())
        for flag in FLAGS:
            if s.startswith(f"UPDATE subscriptions SET {flag} = TRUE") and "COALESCE" in s:
                if self.st[flag]:
                    return "UPDATE 0"
                self.st[flag] = True
                self.st["order"].append(("claim", flag))
                return "UPDATE 1"
            if s.startswith(f"UPDATE subscriptions SET {flag} = FALSE"):
                self.st[flag] = False
                self.st["order"].append(("release", flag))
                return "UPDATE 1"
        return "UPDATE 1"

    async def fetchval(self, sql, *args):
        if "is_reachable" in sql:
            return self.st["reachable"]
        return False             # _PURCHASED_GB_SQL: no bought GB

    async def fetchrow(self, sql, *args):
        return None              # no active paid subscription


class Pool:
    def __init__(self, st):
        self.st = st

    def acquire(self):
        conn = FlagConn(self.st)

        class _A:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _A()


@pytest.fixture
def world(monkeypatch):
    st = {f: False for f in FLAGS}
    st.update(order=[], reachable=True, results=[])

    async def send(bot, tg, text, **kw):
        st["order"].append(("send", text[:20]))
        return st["results"].pop(0) if st["results"] else MagicMock(message_id=1)

    monkeypatch.setattr(tn, "safe_send_message", send)
    monkeypatch.setattr(tn, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(an, "is_notification_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(an, "get_notification_text", AsyncMock(return_value=None))
    monkeypatch.setattr(an, "log_notification_send", AsyncMock())
    monkeypatch.setattr(an, "get_trigger_config", AsyncMock(return_value={}))
    import database.subscriptions as db_subs
    monkeypatch.setattr(db_subs, "claim_special_offer", AsyncMock(return_value=None))
    monkeypatch.setattr(tn.asyncio, "sleep", AsyncMock())
    return st


def _row(st, hours_left: float) -> dict:
    now = datetime.now(timezone.utc)
    end = (now + timedelta(hours=hours_left)).replace(tzinfo=None)
    return {"telegram_id": TG, "trial_expires_at": end, "subscription_expires_at": end,
            "paid_subscription_expires_at": None, "trial_notif_bypass_activated_sent": True,
            **{f: st[f] for f in FLAGS}}


WINDOWS = [(24.0, "trial_notif_24h_sent"), (3.0, "trial_notif_3h_sent"), (0.75, "trial_notif_71h_sent")]


async def _pass(st, hours_left):
    await tn._process_single_trial_notification(MagicMock(), Pool(st), _row(st, hours_left),
                                                datetime.now(timezone.utc))


@pytest.mark.parametrize("hours_left,flag", WINDOWS)
async def test_claimed_before_the_send_and_sent_once(world, hours_left, flag):
    await _pass(world, hours_left)
    kinds = [k for k, _ in world["order"]]
    assert kinds == ["claim", "send"], world["order"]          # a restart after the send: no duplicate
    assert world[flag] is True
    await _pass(world, hours_left)
    assert [k for k, _ in world["order"]].count("send") == 1


@pytest.mark.parametrize("hours_left,flag", WINDOWS)
async def test_a_temporary_failure_is_retried_on_the_next_pass(world, hours_left, flag):
    """#15: a flood wait / network error used to count as «blocked» — lost."""
    world["results"] = [None]            # safe_send_message gave up, the user is still reachable
    await _pass(world, hours_left)
    assert world[flag] is False, "released for a retry"
    await _pass(world, hours_left)
    assert [k for k, _ in world["order"]].count("send") == 2 and world[flag] is True


@pytest.mark.parametrize("hours_left,flag", WINDOWS)
async def test_a_blocked_user_is_not_retried(world, hours_left, flag):
    world["results"] = [None]
    world["reachable"] = False           # safe_send_message marked the block
    await _pass(world, hours_left)
    assert world[flag] is True
    await _pass(world, hours_left)
    assert [k for k, _ in world["order"]].count("send") == 1


@pytest.mark.parametrize("hours_left,flag", WINDOWS)
async def test_a_claim_lost_to_another_pass_sends_nothing(world, hours_left, flag):
    row = _row(world, hours_left)        # the snapshot says «not sent» …
    world[flag] = True                   # … but another pass claimed it meanwhile
    await tn._process_single_trial_notification(MagicMock(), Pool(world), row, datetime.now(timezone.utc))
    assert "send" not in [k for k, _ in world["order"]]
