"""Bypass traffic notices (owner 2026-09-14): thresholds 50/30/15/10/5/3/1 GB of
the REMAINING bypass, only below the amount left at the last grant, one message
per check (the lowest crossed), >= 3 h apart, re-armed by more GB; 0 GB by
state. Background polling: the same per-user GET as before, for a subset of
rows, never the paged stream."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from app import i18n
from app.services import remnawave_api
from app.workers import traffic_monitor as tm

GB = 1024 ** 3
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(days=2)


# ── decide ─────────────────────────────────────────────────────────────

def test_first_check_after_a_grant_only_records_the_baseline():
    d = tm.decide(20 * GB, 30 * GB, None, None, NOW)
    assert d == tm.Decision(new_floor=20 * GB) and d.notice is None


def test_twenty_gb_left_never_gets_50_or_30():
    floor = 20 * GB                                  # baseline at the grant
    assert tm.decide(19 * GB, 30 * GB, floor, None, NOW) is None
    d = tm.decide(int(14.5 * GB), 30 * GB, floor, None, NOW)
    assert d.notice == 15 * GB and d.new_floor == 15 * GB


def test_a_sharp_drop_sends_one_message_for_the_lowest_threshold():
    """#13 / owner rule: 20 GB → 2 GB in one pass = ONE message («осталось 2 ГБ»,
    threshold 3), not 15, 10, 5, 3 one by one."""
    d = tm.decide(2 * GB, 30 * GB, 20 * GB, LONG_AGO, NOW)
    assert d.notice == 3 * GB and d.new_floor == 3 * GB
    # the higher ones are done: nothing more until 1 GB
    assert tm.decide(int(2.5 * GB), 30 * GB, 3 * GB, NOW, NOW + timedelta(hours=4)) is None
    assert tm.decide(int(0.9 * GB), 30 * GB, 3 * GB, NOW, NOW + timedelta(hours=4)).notice == GB


def test_three_hours_between_two_messages():
    assert tm.decide(4 * GB, 30 * GB, 10 * GB, NOW - timedelta(hours=2), NOW) is None
    d = tm.decide(4 * GB, 30 * GB, 10 * GB, NOW - timedelta(hours=3), NOW)
    assert d.notice == 5 * GB


def test_more_gb_re_arm_the_thresholds_below_the_new_amount():
    # told «3 GB» (floor 3 GB); a 10 GB pack → 12 GB left: new baseline, no message
    d = tm.decide(12 * GB, 40 * GB, 3 * GB, LONG_AGO, NOW)
    assert d == tm.Decision(new_floor=12 * GB)
    # and 10 / 5 / 3 / 1 can come again
    assert tm.decide(int(9.5 * GB), 40 * GB, 12 * GB, LONG_AGO, NOW).notice == 10 * GB


def test_zero_is_told_once_and_then_nothing():
    d = tm.decide(0, 10 * GB, GB, LONG_AGO, NOW)
    assert d.notice == 0 and d.new_floor == 0
    assert tm.decide(0, 10 * GB, 0, NOW, NOW + timedelta(days=1)) is None


def test_zero_at_the_baseline_told_by_the_old_worker_is_not_repeated():
    assert tm.decide(0, 10 * GB, None, None, NOW, legacy_zero_told=True) == tm.Decision(new_floor=0)
    assert tm.decide(0, 10 * GB, None, None, NOW).notice == 0


def test_unlimited_bypass_is_never_counted_down():
    assert tm.decide(0, 0, 5 * GB, None, NOW) is None


def test_trial_500_mb_gets_only_the_zero_notice():
    assert tm.decide(400 * 1024 ** 2, 500 * 1024 ** 2, 500 * 1024 ** 2, None, NOW) is None
    assert tm.decide(0, 500 * 1024 ** 2, 500 * 1024 ** 2, None, NOW).notice == 0


# ── texts ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("threshold,premium,key,buttons", [
    (30 * GB, True, "traffic.left_info", ["buy_traffic"]),
    (5 * GB, True, "traffic.left_warn", ["buy_traffic"]),
    (GB, False, "traffic.left_last", ["buy_traffic"]),
    (0, True, "traffic.zero_premium", ["buy_traffic"]),
    (0, False, "traffic.zero_no_premium", ["buy_traffic", "menu_buy_vpn"]),
])
async def test_notice_text_by_threshold_and_state(monkeypatch, lang, threshold, premium, key, buttons):
    """#7: «Atlas Fast работает без ограничений» was sent to users WITHOUT premium."""
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(tm, "safe_send_message", sent)
    monkeypatch.setattr(tm, "resolve_user_language", AsyncMock(return_value=lang))
    await tm._send_traffic_notification(MagicMock(), 1, 2 * GB, threshold, premium=premium)
    text = sent.await_args.args[2]
    assert text == i18n.get_text(lang, key, remaining=f"2 {i18n.get_text(lang, 'common.unit_gb')}")
    kb = sent.await_args.kwargs["reply_markup"]
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == buttons
    assert "Atlas Fast" not in text


# ── the background pass: a subset, per-user GETs, never the stream ─────

def _row(tg, floor=None, last_at=None, **kw):
    row = {"telegram_id": tg, "remnawave_uuid": f"uuid-{tg}", "remnawave_id": tg, "subscription_type": "basic",
           "is_bypass_only": False, "source": "payment", "expires_at": NOW + timedelta(days=10),
           "traffic_notice_floor_bytes": floor, "traffic_notice_last_at": last_at, "legacy_zero_told": False}
    row.update(kw)
    return row


@pytest.fixture
def world(monkeypatch):
    st = {"gets": [], "claims": [], "sent": [], "traffic": {}, "state": {}}

    async def get_user_traffic(ref):
        st["gets"].append(ref)
        used, limit = st["traffic"][int(ref)]
        return {"usedTrafficBytes": used, "trafficLimitBytes": limit}

    async def claim(tg, old_floor, old_last, new_floor, new_last):
        cur = st["state"].get(tg, (None, None))
        if cur != (old_floor, old_last):
            return False
        st["state"][tg] = (new_floor, new_last)
        st["claims"].append((tg, new_floor))
        return True

    async def send(bot, tg, remaining, threshold, *, premium):
        st["sent"].append((tg, threshold, premium))

    async def no_stream(*a, **k):
        raise AssertionError("the background pass must not stream the whole panel")

    monkeypatch.setattr(remnawave_api, "get_user_traffic", get_user_traffic)
    monkeypatch.setattr(remnawave_api, "get_all_users", no_stream, raising=False)
    monkeypatch.setattr(database, "claim_traffic_notice_state", claim, raising=False)
    monkeypatch.setattr(tm, "_send_traffic_notification", send)
    monkeypatch.setattr(tm.asyncio, "sleep", AsyncMock())
    return st


async def test_one_get_per_production_row_and_one_message_each(world, monkeypatch):
    rows = [_row(1), _row(2), _row(3, is_bypass_only=True, source="bypass_only"), _row(4)]
    world["traffic"] = {1: (18 * GB, 20 * GB), 2: (0, 30 * GB), 3: (10 * GB, 10 * GB), 4: (5 * GB, 0)}
    world["state"] = {1: (20 * GB, None), 2: (None, None), 3: (10 * GB, None), 4: (None, None)}

    async def state(tg):
        floor, last = world["state"][tg]
        return {"floor": floor, "last_at": last, "legacy_zero_told": False}
    monkeypatch.setattr(database, "get_active_remnawave_users", AsyncMock(return_value=rows))
    monkeypatch.setattr(database, "get_traffic_notice_state", state, raising=False)

    await tm.traffic_monitor_iteration(MagicMock())

    assert world["gets"] == [1, 2, 3, 4], "one panel GET per row — exactly production's polling"
    assert world["sent"] == [(1, 3 * GB, True), (3, 0, False)]
    assert world["state"][2] == (30 * GB, None), "baseline recorded, nothing sent"
    assert world["state"][4] == (None, None), "unlimited bypass: no state, no message"


async def test_two_passes_never_send_the_same_notice_twice(world, monkeypatch):
    world["state"] = {7: (20 * GB, None)}
    state = {"floor": 20 * GB, "last_at": None}
    first = await tm.apply_check(MagicMock(), 7, used=17 * GB, limit=20 * GB, premium=True, state=state, now=NOW)
    second = await tm.apply_check(MagicMock(), 7, used=17 * GB, limit=20 * GB, premium=True, state=state, now=NOW)
    assert (first, second) == (True, False) and len(world["sent"]) == 1


async def test_the_3h_gap_holds_the_next_notice(world, monkeypatch):
    recent = NOW - timedelta(hours=1)
    world["state"] = {9: (5 * GB, recent)}
    state = {"floor": 5 * GB, "last_at": recent}
    assert await tm.apply_check(MagicMock(), 9, used=9 * GB, limit=10 * GB, premium=False, state=state, now=NOW) is False
    later = NOW + timedelta(hours=2, minutes=1)
    assert await tm.apply_check(MagicMock(), 9, used=9 * GB, limit=10 * GB, premium=False, state=state,
                                now=later) is True
    assert world["sent"] == [(9, GB, False)], "one message: the lowest crossed (1 GB)"
