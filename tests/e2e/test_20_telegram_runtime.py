"""Scenario 20 — Telegram runtime: what Telegram and users do to a production bot.

docs/audit/11_telegram_runtime.md. The real dispatcher + middleware chain on a
real PostgreSQL; the fake Bot API injects Telegram errors (429, 403, "query is
too old", "message to edit not found", "can't parse entities"), re-delivers
updates, and sends the update kinds a VPN shop bot does not expect (groups,
channels, stickers in text states, refunds, my_chat_member).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from app.handlers.common.states import (
    AdminChat, PromoCodeInput, SpotifyPurchaseState, SteamPurchaseState,
    TelegramPremiumState, TelegramStarsState, TopUpStates,
)
from tests.e2e import flows
from tests.e2e.world import ADMIN, new_user
from tests.fakes import providers_http as prov
from tests.fakes import telegram as tgf

UNHANDLED = "UNHANDLED_HANDLER_EXCEPTION"


def unhandled(caplog) -> list:
    return [r for r in caplog.records if r.getMessage().startswith(UNHANDLED)]


def reset_rate_limit(e2e) -> None:
    for rl in e2e.dp._e2e_rate_limiters:
        rl._user_requests.clear()
        rl._banned_users.clear()


async def fsm_set(e2e, user, state) -> None:
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=e2e.bot.id, chat_id=user.id, user_id=user.id)
    await e2e.dp.fsm.storage.set_state(key, state)


# ── 3. payments via Telegram ─────────────────────────────────────────────

async def test_flood_banned_user_still_gets_the_paid_purchase(e2e):
    """TG-RT-1: GlobalRateLimit banned the user (60+ messages a minute) — the
    successful_payment that arrives during the 5-minute ban must still be
    finalized: Telegram already took the money and never resends it."""
    u = new_user()
    await e2e.register(u)
    p = await flows.buy(e2e, u, "basic", 30, "card")
    for _ in range(61):
        await e2e._post_update(tgf.message(u, "hi"), settle=False)
    await e2e.settle()
    assert u.id in e2e.dp._e2e_rate_limiters[0]._banned_users, "precondition: the flood ban is on"

    before = await flows.snapshot(e2e, u.id)
    assert await flows.pay(e2e, u, p, "card") == 200
    await flows.check_purchase(e2e, u.id, before, "basic", 30)


async def test_redelivered_successful_payment_is_silent(e2e):
    """TG-RT-2: Telegram re-delivers an update when the webhook did not answer
    2xx (process killed by a redeploy mid-request). The purchase is already
    paid: no second grant, and — the bug — no PERMANENT "money taken, nothing
    granted" alert and no error screen for the user who already got the key."""
    u = new_user()
    await e2e.register(u)
    p = await flows.buy(e2e, u, "basic", 30, "card")
    before = await flows.snapshot(e2e, u.id)
    payload = f"purchase:{p['purchase_id']}"
    paid = tgf.successful_payment(u, payload, p["price_kopecks"], "RUB", charge_id="tg-charge-dup-1")
    await e2e._post_update(tgf.pre_checkout(u, payload, p["price_kopecks"]))
    assert await e2e._post_update(paid) == 200
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    sub = await e2e.sub(u.id)

    mark = e2e.tg.mark()
    assert await e2e._post_update(paid) == 200          # the very same update again
    assert (await e2e.sub(u.id))["expires_at"] == sub["expires_at"]
    assert len(await e2e.payments(u.id)) == before.payments + 1
    assert e2e.admin_texts(mark) == []
    assert e2e.user_texts(u.id, mark) == []
    assert not [e for e in await e2e.payment_errors() if e["stage"] == "telegram_purchase_not_found"]


@pytest.mark.parametrize("state", [
    TopUpStates.waiting_for_amount, PromoCodeInput.waiting_for_promo, AdminChat.waiting_for_user_id,
    TelegramPremiumState.waiting_for_username, SteamPurchaseState.waiting_for_login,
    SpotifyPurchaseState.waiting_for_email,
], ids=lambda s: s.state)
async def test_successful_payment_while_in_a_text_input_state_is_finalized(e2e, state):
    """The user left a text-input screen open (top-up amount, promo, shop
    login…) and pays an invoice sent earlier: a state-only message handler
    must not swallow the successful_payment."""
    u = new_user()
    await e2e.register(u)
    p = await flows.buy(e2e, u, "basic", 30, "card")
    await fsm_set(e2e, u, state)
    before = await flows.snapshot(e2e, u.id)
    await flows.pay(e2e, u, p, "card")
    await flows.check_purchase(e2e, u.id, before, "basic", 30)


async def test_second_charge_for_a_paid_purchase_still_alerts(e2e):
    """The dedup above is per Telegram charge id: a DIFFERENT charge for an
    already-paid purchase is real money taken twice — the forced alert stays."""
    u = new_user()
    await e2e.register(u)
    p = await flows.buy(e2e, u, "basic", 30, "card")
    await flows.pay(e2e, u, p, "card", tx_id="tg-charge-first")
    mark = e2e.tg.mark()
    await e2e._post_update(tgf.successful_payment(
        u, f"purchase:{p['purchase_id']}", p["price_kopecks"], "RUB", charge_id="tg-charge-second"))
    assert e2e.admin_texts(mark), "a second real charge must reach the admin"
    assert [e for e in await e2e.payment_errors() if e["stage"] == "telegram_purchase_not_found"]


async def test_stars_refund_alerts_admin_and_keeps_access(e2e):
    """TG-RT-3: a Stars refund arrives as a `refunded_payment` service message.
    Owner rule (SCOPE.md): refunds → log + forced admin alert, access is NOT
    revoked automatically."""
    u = new_user()
    await e2e.register(u)
    p = await flows.buy(e2e, u, "basic", 30, "stars")
    await flows.pay(e2e, u, p, "stars", tx_id="stars-charge-refund-1")
    sub = await e2e.sub(u.id)
    stars = next(i for i in reversed(e2e.tg.invoices) if i.currency == "XTR").prices[0].amount

    mark = e2e.tg.mark()
    upd = tgf.refunded_payment(u, f"purchase:{p['purchase_id']}", stars, "XTR", "stars-charge-refund-1")
    assert await e2e._post_update(upd) == 200
    texts = e2e.admin_texts(mark)
    assert any("stars-charge-refund-1" in t for t in texts), texts
    assert (await e2e.sub(u.id))["expires_at"] == sub["expires_at"]
    assert [e for e in await e2e.payment_errors() if e["stage"] == "telegram_refund"]


async def test_pre_checkout_rejects_foreign_expired_and_paid_purchases(e2e):
    a, b = new_user(), new_user()
    await e2e.register(a)
    await e2e.register(b)
    p = await e2e.create_purchase(a, "basic", 30, provider="telegram")
    payload, price = f"purchase:{p['purchase_id']}", p["price_kopecks"]

    await e2e._post_update(tgf.pre_checkout(b, payload, price))            # someone else's invoice
    assert e2e.tg.pre_checkout_answers[-1].ok is False
    await e2e._post_update(tgf.pre_checkout(a, payload, price))
    assert e2e.tg.pre_checkout_answers[-1].ok is True
    await e2e.pool.execute("UPDATE pending_purchases SET expires_at = NOW() - interval '1 minute' "
                           "WHERE purchase_id=$1", p["purchase_id"])
    await e2e._post_update(tgf.pre_checkout(a, payload, price))            # expired
    assert e2e.tg.pre_checkout_answers[-1].ok is False
    await e2e.pool.execute("UPDATE pending_purchases SET expires_at = NOW() + interval '1 hour', "
                           "status='paid' WHERE purchase_id=$1", p["purchase_id"])
    await e2e._post_update(tgf.pre_checkout(a, payload, price))            # already paid
    assert e2e.tg.pre_checkout_answers[-1].ok is False


async def test_pre_checkout_is_answered_in_time_when_the_db_hangs(e2e, monkeypatch):
    """TG-RT-4: Telegram gives the bot 10 s to answer pre_checkout_query, then
    the payment fails for the user. A DB that hangs (pool exhausted) must not
    eat that budget: the lookup is bounded and the answer still goes out."""
    import asyncio

    import database
    from app.handlers.payments import payments_messages as pm
    u = new_user()
    await e2e.register(u)
    p = await e2e.create_purchase(u, "basic", 30, provider="telegram")
    never = asyncio.Event()

    async def hanging(*a, **kw):
        await never.wait()
    monkeypatch.setattr(database, "get_pending_purchase", hanging)
    monkeypatch.setattr(pm, "PRE_CHECKOUT_DB_TIMEOUT_S", 0.05, raising=False)
    upd = tgf.pre_checkout(u, f"purchase:{p['purchase_id']}", p["price_kopecks"])
    # settle=False: the webhook call itself must return fast (the hung lookup is
    # cancelled by the bound); settle() would wait for unrelated tasks.
    assert await asyncio.wait_for(e2e._post_update(upd, settle=False), timeout=3) == 200
    assert e2e.tg.pre_checkout_answers and e2e.tg.pre_checkout_answers[-1].ok is True


# ── 5. chat types / sources ──────────────────────────────────────────────

async def test_non_private_and_foreign_update_kinds_are_ignored(e2e, caplog):
    u = new_user()
    await e2e.register(u)
    mark = e2e.tg.mark()
    updates = [
        tgf.group_message(u, "/start"),
        tgf.group_message(u, "/buy", chat_type="supergroup"),
        tgf.channel_post("/start"),
        tgf.edited_message(u, "/start"),
        tgf.inline_query(u, "vpn"),
        tgf.chat_join_request(u),
    ]
    for upd in updates:
        assert await e2e._post_update(upd) == 200
    assert e2e.tg.since(mark) == []
    assert unhandled(caplog) == []


async def test_blocking_and_unblocking_the_bot_updates_reachability(e2e):
    """TG-RT-5: my_chat_member (kicked / member) is the only signal Telegram
    sends when a user blocks the bot; workers and broadcasts filter by
    users.is_reachable."""
    u = new_user()
    await e2e.register(u)
    assert "my_chat_member" in e2e.dp.resolve_used_update_types()
    assert await e2e._post_update(tgf.my_chat_member(u, "kicked", "member")) == 200
    assert await e2e.val("SELECT is_reachable FROM users WHERE telegram_id=$1", u.id) is False
    assert await e2e._post_update(tgf.my_chat_member(u, "member", "kicked")) == 200
    assert await e2e.val("SELECT is_reachable FROM users WHERE telegram_id=$1", u.id) is True


TEXT_STATES = [
    TopUpStates.waiting_for_amount, PromoCodeInput.waiting_for_promo, AdminChat.waiting_for_user_id,
    TelegramPremiumState.waiting_for_username, TelegramStarsState.waiting_for_username,   # 🔒 shop
    SteamPurchaseState.waiting_for_login, SpotifyPurchaseState.waiting_for_email,          # 🔒 shop
]


@pytest.mark.parametrize("state", TEXT_STATES, ids=lambda s: s.state)
async def test_non_text_input_in_a_text_state_does_not_crash(e2e, caplog, state):
    u = new_user()
    await e2e.register(u)
    for upd in (tgf.message_with(u, sticker=tgf.STICKER), tgf.message_with(u, photo=tgf.PHOTO),
                tgf.message_with(u, caption="x", photo=tgf.PHOTO), tgf.message(u, "я" * 5000),
                tgf.message(u, "​‏"), tgf.message(u, "<b>1</b> & <script>")):
        await fsm_set(e2e, u, state)
        assert await e2e._post_update(upd) == 200
    assert [r.getMessage() for r in unhandled(caplog)] == []


async def test_user_with_html_in_name_and_no_username_or_language(e2e, caplog):
    """Names are user-controlled: a name like "<Admin> & Co" inside an HTML
    template makes Telegram answer "can't parse entities" and the screen is lost."""
    e2e.tg.strict_html = True
    u = tgf.TgUser(id=new_user().id, first_name="<Admin> & Co </b>", username=None,
                   language_code=None, last_name="<i>")
    await e2e.start_user(u)
    for data in ("menu_main", "menu_profile", "menu_referral", "menu_buy_vpn"):
        await e2e.tap(u, data)
    rtl = tgf.TgUser(id=new_user().id, first_name="‮محمد‬ 🔥", username=None, language_code="pt-br")
    await e2e.start_user(rtl)
    await e2e.tap(rtl, "menu_profile")
    assert e2e.tg.html_errors == []
    assert unhandled(caplog) == []


# ── 1. Telegram API errors on send / edit / answer ───────────────────────

NAV = ("menu_main", "menu_profile", "menu_buy_vpn", "topup_balance", "menu_referral")


async def test_late_callback_answer_does_not_abort_the_screen(e2e):
    """TG-RT-6: under load the bot answers a callback after Telegram's ~15 s
    window: answerCallbackQuery → 400 "query is too old". That is cosmetic —
    the screen the user asked for must still be shown."""
    u = new_user()
    await e2e.register(u)
    baseline = {}
    for data in NAV:
        mark = e2e.tg.mark()
        await e2e.tap(u, data)
        baseline[data] = len(e2e.tg.since(mark, u.id))
    e2e.tg.inject("AnswerCallbackQuery", tgf.bad_request(tgf.QUERY_TOO_OLD))
    silent = []
    for data in NAV:
        mark = e2e.tg.mark()
        await e2e.tap(u, data)
        if baseline[data] and not e2e.tg.since(mark, u.id):
            silent.append(data)
    assert silent == []


async def test_late_callback_answer_does_not_abort_a_balance_purchase(e2e):
    u = new_user()
    await e2e.register(u, balance_rub=1000)
    await flows.open_payment_screen(e2e, u, "basic", 30)
    before = await flows.snapshot(e2e, u.id)
    e2e.tg.inject("AnswerCallbackQuery", tgf.bad_request(tgf.QUERY_TOO_OLD))
    await e2e.tap(u, "pay:balance")
    await flows.check_purchase(e2e, u.id, before, "basic", 30)


@pytest.mark.parametrize("error", [tgf.EDIT_NOT_FOUND, tgf.CANT_BE_EDITED])
async def test_uneditable_old_message_still_gets_a_screen(e2e, error):
    """The message a button belongs to was deleted / is older than 48 h: edit
    fails, a new message must be sent instead of nothing."""
    u = new_user()
    await e2e.register(u)
    for m in ("EditMessageText", "EditMessageCaption", "EditMessageMedia", "EditMessageReplyMarkup"):
        e2e.tg.inject(m, tgf.bad_request(error))
    silent = []
    for data in NAV:
        mark = e2e.tg.mark()
        await e2e.tap(u, data)
        if not [s for s in e2e.tg.since(mark, u.id) if not s.method.startswith("Edit")]:
            silent.append(data)
    assert silent == []


async def test_blocked_user_still_gets_access_for_a_provider_payment(e2e):
    """403 on every send to the user: money/access never depend on a send."""
    u = new_user()
    await e2e.register(u)
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    e2e.tg.fail_chats.add(u.id)
    before = await flows.snapshot(e2e, u.id)
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.val("SELECT is_reachable FROM users WHERE telegram_id=$1", u.id) is False


async def test_flood_wait_on_the_payment_confirmation_does_not_lose_it(e2e):
    """TG-RT-7: a 429 (retry after 1-2 s) on the "payment received" message
    used to drop it (safe_send_message swallowed TelegramRetryAfter). A short
    flood wait is slept out once and the message is delivered."""
    control, u = new_user(), new_user()
    for x in (control, u):
        await e2e.register(x)
    pc = await e2e.create_purchase(control, "basic", 30, provider="platega")
    mark = e2e.tg.mark()
    await e2e.webhook(prov.platega_webhook(pc["purchase_id"], pc["price_kopecks"] / 100))
    expected = len(e2e.user_texts(control.id, mark))
    assert expected >= 1

    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    e2e.tg.inject("SendMessage", tgf.retry_after(2), chat_id=u.id, times=1)
    e2e.tg.inject("SendPhoto", tgf.retry_after(2), chat_id=u.id, times=1)
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100))
    assert res.body["status"] == "ok"
    assert len(e2e.user_texts(u.id, mark)) == expected


async def test_admin_chat_unreachable_does_not_break_payments(e2e, caplog):
    """8. The admin blocked the bot / ADMIN_TELEGRAM_ID is wrong: a forced alert
    fails (logged), the user's flow is not affected."""
    e2e.tg.fail_chats.add(ADMIN)
    u = new_user()
    await e2e.register(u)
    p = await flows.buy(e2e, u, "basic", 30, "card")
    await flows.pay(e2e, u, p, "card", tx_id="c-1")
    await e2e._post_update(tgf.successful_payment(
        u, f"purchase:{p['purchase_id']}", p["price_kopecks"], "RUB", charge_id="c-2"))
    assert any("ADMIN_ALERT_FAILED" in r.getMessage() for r in caplog.records)
    assert (await e2e.sub(u.id))["status"] == "active"
    assert unhandled(caplog) == []


# ── 2 / 7. duplicate, out-of-order and stale updates ─────────────────────

def _callback_literals() -> tuple[list[str], list[str]]:
    root = Path(__file__).resolve().parents[2] / "app" / "handlers"
    exact, prefixes = set(), set()
    for f in root.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        exact.update(re.findall(r'F\.data\s*==\s*"([^"]+)"', src))
        prefixes.update(re.findall(r'F\.data\.startswith\(\s*"([^"]+)"\s*\)', src))
    return sorted(exact), sorted(prefixes)


EXACT, PREFIXES = _callback_literals()

# Malformed / forged callback data that still crashes the parser (the error
# boundary answers with the generic error). Report-only (11_telegram_runtime.md
# TG-RT-R4): apple_* is the 🔒 shop, farm_* is the game; a real button never
# carries such data. A NEW prefix here is a regression.
KNOWN_MALFORMED_CRASH_PREFIXES = ("apple_", "farm_")


async def _tap_all(e2e, caplog, user, datas) -> dict:
    crashed = {}
    for data in datas:
        if len(data.encode()) > 64:
            continue
        reset_rate_limit(e2e)
        caplog.clear()
        await e2e.tap(user, data)
        bad = unhandled(caplog)
        if bad:
            exc = bad[0].exc_info[1] if bad[0].exc_info else None
            crashed[data] = f"{type(exc).__name__}: {exc}"[:120]
    return crashed


async def test_every_button_of_an_old_message_after_a_restart(e2e, caplog):
    """A restart / Redis flush empties the FSM; the user presses any button of
    an old message. No button may crash its handler."""
    caplog.set_level(logging.ERROR)
    u = new_user()
    await e2e.register(u)
    crashed = await _tap_all(e2e, caplog, u, EXACT)
    assert crashed == {}, "\n".join(f"{k!r}: {v}" for k, v in crashed.items())


async def test_prefix_buttons_with_stale_or_forged_suffix(e2e, caplog):
    """Prefix buttons with a stale / forged suffix (an old keyboard after a
    callback_data format change, or a crafted callback)."""
    caplog.set_level(logging.ERROR)
    u = new_user()
    await e2e.register(u)
    datas = [p + "x" for p in PREFIXES] + [p + "0:0:0" for p in PREFIXES]
    crashed = await _tap_all(e2e, caplog, u, datas)
    new = {k: v for k, v in crashed.items() if not k.startswith(KNOWN_MALFORMED_CRASH_PREFIXES)}
    assert new == {}, "\n".join(f"{k!r}: {v}" for k, v in new.items())
