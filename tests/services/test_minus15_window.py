"""−15 % around the end of a subscription (owner 2026-09-14, SCOPE.md «Срок скидки −15 %»;
docs/audit/08_payments_ux.md P1 #5, P2 #11):

  * ONE 72 h window per period, opened by whatever offers it first — the 3 h
    reminder or the expiry; a button never extends it, an old button after the
    window says it expired; a bigger personal discount stays;
  * the texts name the exact end in Moscow time (not «3 часа» / «7 дней»);
  * a paid subscription that ended tells the user — after the commit, once —
    also without a bypass entity (it used to end in silence).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import database.subscriptions as db_subs
from app.i18n import get_text
from app.services.notifications import special_offer as so

# reuse the one-subscription world of the N7 tests
from tests.services.test_special_offer_on_expiry import (  # noqa: F401 — `world` is a fixture
    ENDED, TG, _one_fast_expiry_pass, _patch_fast_expiry, world,
)

END = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
OFFER_END = datetime(2026, 9, 17, 11, 30, tzinfo=timezone.utc)
CYRILLIC = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def test_deadline_is_moscow_time():
    assert so.format_deadline("ru", OFFER_END) == "17.09.2026 14:30 МСК"
    assert so.format_deadline("en", OFFER_END) == "17.09.2026 14:30 Moscow time"


def test_window_is_72_hours_and_one_per_period():
    assert db_subs.SPECIAL_OFFER_DURATION == timedelta(hours=72)
    naive_end = END.replace(tzinfo=None)
    assert db_subs._offer_cutoff(END) == naive_end - timedelta(days=7)
    assert db_subs._offer_cutoff(naive_end) == naive_end - timedelta(days=7)


class _Pool:
    def __init__(self, tag):
        self.tag, self.args = tag, None

    def acquire(self):
        pool = self

        class _A:
            async def __aenter__(self_inner):
                class _C:
                    async def execute(self_c, sql, *args):
                        pool.args = args
                        return pool.tag
                return _C()

            async def __aexit__(self_inner, *exc):
                return False
        return _A()


@pytest.mark.parametrize("tag", ["UPDATE 1", "UPDATE 0"])
async def test_claim_opens_once_and_returns_the_active_window(monkeypatch, tag):
    pool = _Pool(tag)
    monkeypatch.setattr(db_subs._core, "DB_READY", True)
    monkeypatch.setattr(db_subs, "get_pool", AsyncMock(return_value=pool))
    active = {"expires_at": OFFER_END, "discount_percent": 15}
    monkeypatch.setattr(db_subs, "get_special_offer_info", AsyncMock(return_value=active))

    assert await db_subs.claim_special_offer(TG, END) is active
    assert pool.args[2] == END.replace(tzinfo=None) - timedelta(days=7)   # never re-opens this period


async def test_claim_without_db_or_period_end_is_none(monkeypatch):
    monkeypatch.setattr(db_subs._core, "DB_READY", True)
    assert await db_subs.claim_special_offer(TG, None) is None
    monkeypatch.setattr(db_subs._core, "DB_READY", False)
    assert await db_subs.claim_special_offer(TG, END) is None


# ── the −15 % buttons ──────────────────────────────────────────────────


async def _press(monkeypatch, *, offer, claim_returns=None, current=None, lang="ru", handler="callback_paid_discount_15"):
    from app.handlers.callbacks import navigation
    import app.handlers.common.screens as screens

    create = AsyncMock()
    claim = AsyncMock(return_value=claim_returns)
    monkeypatch.setattr(database, "create_user_discount", create)
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value=offer))
    monkeypatch.setattr(database, "get_subscription_any", AsyncMock(return_value={"expires_at": END}))
    monkeypatch.setattr(db_subs, "claim_special_offer", claim)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(
        return_value={"discount_percent": current} if current else None))
    monkeypatch.setattr(navigation, "resolve_user_language", AsyncMock(return_value=lang))
    monkeypatch.setattr(screens, "_open_buy_screen", AsyncMock())
    callback = MagicMock()
    callback.from_user.id = TG
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    await getattr(navigation, handler)(callback, MagicMock())
    return callback.message.answer.await_args.args[0], create, claim


@pytest.mark.parametrize("handler", ["callback_paid_discount_15", "callback_trial_discount_15"])
async def test_button_uses_the_open_window_and_names_its_end(monkeypatch, handler):
    text, create, claim = await _press(monkeypatch, offer={"expires_at": OFFER_END}, handler=handler)
    assert text == get_text("ru", "main.discount_applied_choose_tariff",
                            deadline=so.format_deadline("ru", OFFER_END))
    assert "7 дней" not in text and "17.09.2026 14:30" in text
    create.assert_not_awaited()     # no 7-day personal discount any more
    claim.assert_not_awaited()      # pressing again never extends the window


async def test_button_opens_the_window_if_nothing_offered_it_yet(monkeypatch):
    text, _, claim = await _press(monkeypatch, offer=None, claim_returns={"expires_at": OFFER_END})
    assert claim.await_args.args == (TG, END)
    assert "17.09.2026 14:30" in text


async def test_old_button_after_the_window_says_it_expired(monkeypatch):
    text, create, _ = await _press(monkeypatch, offer=None, claim_returns=None)
    assert text == get_text("ru", "errors.special_offer_expired")
    create.assert_not_awaited()


async def test_a_bigger_discount_stays(monkeypatch):
    text, _, _ = await _press(monkeypatch, offer={"expires_at": OFFER_END}, current=30, lang="en")
    assert text == get_text("en", "main.discount_bigger_kept", percent=30)


# ── the «subscription ended» notice ────────────────────────────────────


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_paid_expired_notice_with_the_open_window(monkeypatch, lang):
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value={"expires_at": OFFER_END}))
    text, kb = await so.expired_notice(lang, TG, has_bypass=False)
    assert text.startswith(get_text(lang, "subscription.expired_paid"))
    assert so.format_deadline(lang, OFFER_END) in text
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == ["special_offer_buy", "menu_buy_vpn"]
    if lang == "en":
        assert not (set(text.lower()) & CYRILLIC)


async def test_notice_without_a_window_has_no_offer(monkeypatch):
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value=None))
    text, kb = await so.expired_notice("ru", TG, has_bypass=False)
    assert text == get_text("ru", "subscription.expired_paid")
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == ["menu_buy_vpn"]


def _bypass(monkeypatch, info):
    from app.services.subscriptions import live_state
    read = AsyncMock(return_value=info)
    monkeypatch.setattr(live_state, "read_bypass", read)
    return read


async def test_bypass_notice_keeps_its_buttons_and_gets_the_offer(monkeypatch):
    from app.services.subscriptions.live_state import BypassInfo
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value={"expires_at": OFFER_END}))
    _bypass(monkeypatch, BypassInfo("present", used=3 * 1024 ** 3, limit=10 * 1024 ** 3, status="ACTIVE"))
    text, kb = await so.expired_notice("ru", TG, has_bypass=True)
    assert text.startswith(get_text("ru", "subscription.expired_gb_left", remaining="7 ГБ"))
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        "special_offer_buy", "buy_traffic", "menu_buy_vpn"]


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("info,key,gb_named", [
    (("present", 2 * 1024 ** 3, 10 * 1024 ** 3, "ACTIVE"), "subscription.expired_gb_left", "8"),
    (("present", 10 * 1024 ** 3, 10 * 1024 ** 3, "ACTIVE"), "subscription.expired_gb_spent", None),
    (("present", 12 * 1024 ** 3, 10 * 1024 ** 3, "LIMITED"), "subscription.expired_gb_spent", None),
    (("absent", 0, 0, ""), "subscription.expired_gb_spent", None),
    (("unavailable", 0, 0, ""), "subscription.expired_gb_works", None),
], ids=["gb_left", "gb_zero", "limited", "no_entity", "panel_down"])
async def test_ended_premium_notice_follows_the_gb_actually_left(monkeypatch, lang, info, key, gb_named):
    """#2: «обход работает» only when the panel shows GB left; at 0 GB — VPN off."""
    from app.services.subscriptions.live_state import BypassInfo
    state, used, limit, status = info
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value=None))
    _bypass(monkeypatch, BypassInfo(state, used=used, limit=limit, status=status))
    text, kb = await so.expired_notice(lang, TG, has_bypass=True)
    head = get_text(lang, key, remaining="X").split("\n", 1)[0]
    assert text.startswith(head), text
    if gb_named:
        assert f"{gb_named} {get_text(lang, 'common.unit_gb')}" in text
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    if key == "subscription.expired_gb_spent":
        assert callbacks == ["menu_buy_vpn", "buy_traffic"]
        assert "продолжает работать" not in text and "keeps working" not in text
    else:
        assert callbacks == ["buy_traffic", "menu_buy_vpn"]
    if lang == "en":
        assert not (set(text.lower()) & CYRILLIC)


# ── expiry paths: after the commit, once ───────────────────────────────


@pytest.mark.parametrize("bypass", [False, True], ids=["fully_expired", "to_bypass_only"])
async def test_fast_expiry_tells_a_paid_user_once(world, monkeypatch, bypass):
    store = world(source="payment", bypass=bypass)
    fec = _patch_fast_expiry(monkeypatch, store)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(so, "notify_expired", notify)

    await _one_fast_expiry_pass(fec)
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["has_bypass"] is bypass
    assert store.offer_writes == 1                 # the window exists when the notice is built

    store.row.update(status="active", uuid="u-1", source="payment")  # rerun of the same period
    await _one_fast_expiry_pass(fec)
    assert store.offer_writes == 1


async def test_gifted_subscription_end_is_told_with_the_offer(world, monkeypatch):
    """#19: a gifted subscription ended in silence and without −15 %."""
    store = world(source="gift", bypass=False)
    fec = _patch_fast_expiry(monkeypatch, store)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(so, "notify_expired", notify)
    await _one_fast_expiry_pass(fec)
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["has_bypass"] is False
    assert store.offer_writes == 1


@pytest.mark.parametrize("bypass, source, days, expected", [
    (True, "payment", None, (True, False)),
    (False, "payment", None, (False, False)),
    (False, "gift", None, (False, False)),          # #19
    (False, "admin", None, (False, False)),         # legacy admin row on a paid period
    (False, "admin", 7, (False, True)),             # #18: free days ended
    (True, "admin", 7, (True, False)),              # GB left decide the text
])
async def test_check_and_disable_notifies_after_its_commit(world, monkeypatch, bypass, source, days, expected):
    store = world(source=source, bypass=bypass)
    store.row["admin_grant_days"] = days
    schedule = MagicMock(return_value=True)
    monkeypatch.setattr(so, "schedule_expired_notice", schedule)
    assert await database.check_and_disable_expired_subscription(TG) is True
    has_bypass, free = expected
    schedule.assert_called_once_with(TG, has_bypass=has_bypass, free=free)


async def test_fast_expiry_tells_free_days_that_they_ended(world, monkeypatch):
    """#18: admin / promo-link days without a subscription ended in silence."""
    store = world(source="admin", bypass=False)
    store.row["admin_grant_days"] = 14
    fec = _patch_fast_expiry(monkeypatch, store)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(so, "notify_expired", notify)
    await _one_fast_expiry_pass(fec)
    notify.assert_awaited_once()
    assert notify.await_args.kwargs == {"has_bypass": False, "free": True}
    assert store.offer_writes == 0, "no −15 % for free days"


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_free_access_ended_text_uses_the_price_table(monkeypatch, lang):
    import config
    from app.services import pricing
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value=None))
    monkeypatch.setattr(pricing, "get_effective_price", AsyncMock(return_value=None))
    text, kb = await so.expired_notice(lang, TG, has_bypass=False, free=True)
    price = config.TARIFFS["basic"][30]["price"]
    assert text == get_text(lang, "subscription.expired_free", price=price)
    assert f"{price} ₽" in text
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == ["menu_buy_vpn"]


async def test_from_price_follows_the_dashboard_price(monkeypatch):
    from types import SimpleNamespace
    from app.services import pricing
    monkeypatch.setattr(pricing, "get_effective_price", AsyncMock(return_value=SimpleNamespace(effective=149)))
    assert await so.from_price_rub() == 149


async def test_check_and_disable_ends_a_trial_with_the_trial_notice_only(world, monkeypatch):
    """#1: a trial that ends on the screen-open path gets «пробный завершён», not
    «основная подписка закончилась −15 %»."""
    world(source="trial", bypass=True)
    schedule = MagicMock(return_value=True)
    trial_notice = MagicMock(return_value=True)
    monkeypatch.setattr(so, "schedule_expired_notice", schedule)
    monkeypatch.setattr(so, "schedule_trial_expired_notice", trial_notice)
    assert await database.check_and_disable_expired_subscription(TG) is True
    schedule.assert_not_called()
    trial_notice.assert_called_once_with(TG)


def test_grant_skips_a_window_the_reminder_already_opened():
    """The 3 h reminder opened the window at end − 3 h → the expiry must not re-open it."""
    naive_end = ENDED
    reminder_offer = naive_end - timedelta(hours=3)
    assert not reminder_offer < db_subs._offer_cutoff(naive_end)        # «already offered»
    assert (naive_end - timedelta(days=40)) < db_subs._offer_cutoff(naive_end)  # previous period
