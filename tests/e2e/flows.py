"""User journeys through the real bot UI + provider callbacks, and the owner-rule checks.

buy(): the screens a user taps — «Купить» → tariff → period → payment method —
ending in a pending purchase (and, for providers, an invoice at the fake API).
pay(): what the provider / Telegram sends back once the user paid.
check_purchase(): owner rules (docs/audit/SCOPE.md) on DB + panel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import config
from tests.fakes import providers_http as prov
from tests.e2e.world import GIB, MB, World, aware, combo_gb, tariff_price, utcnow

# payment method (button) → pending_purchases.payment_provider / webhook kind
METHOD_PROVIDER = {
    "sbp": "platega",
    "wata": "wata",
    "crypto": "cryptobot",
    "card": "telegram",
    "stars": "telegram_stars",
    "balance": "balance",
}


def base_of(tariff: str) -> str:
    return tariff.replace("combo_", "")


# Owner decision (merged 439828ec): paid catalog periods are CALENDAR months
# (30/90/180/365/730 d = 1/3/6/12/24 months, end-of-month clamp); any other
# period (day grants, trial) stays in days. Written here independently of
# app.services.tariffs.extend_expiry, so the test checks the code, not itself.
PERIOD_MONTHS = {30: 1, 90: 3, 180: 6, 365: 12, 730: 24}


def add_months(dt: datetime, months: int) -> datetime:
    import calendar
    y, m = divmod(dt.month - 1 + months, 12)
    year, month = dt.year + y, m + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def add_period(dt: datetime, period_days: int) -> datetime:
    months = PERIOD_MONTHS.get(period_days)
    return add_months(dt, months) if months else dt + timedelta(days=period_days)


def expected_bypass_gain(tariff: str, period: int) -> int:
    """Owner rule: Basic/Plus = 10 GB once; Combo = combo GB only (30 d → 75 GB)."""
    if tariff.startswith("combo_"):
        return combo_gb(tariff, period) * GIB
    return 10 * GIB


async def open_payment_screen(w: World, user, tariff: str, period: int) -> None:
    if tariff.startswith("combo_"):
        await w.tap(user, "buy_combo")
        await w.tap(user, f"combo_tariff:{tariff}")
        await w.tap(user, f"combo_period:{tariff}:{period}")
    else:
        await w.tap(user, "menu_buy_vpn")
        await w.tap(user, f"tariff:{tariff}")
        mark = w.tg.mark()
        await w.tap(user, f"period:{tariff}:{period}")
        # Plus → Basic asks to confirm the downgrade before the payment screen
        if any("downgrade_confirm_basic" in s.callback_data() for s in w.tg.since(mark, user.id)):
            await w.tap(user, "downgrade_confirm_basic")


async def latest_pending(w: World, tg: int) -> Optional[Dict[str, Any]]:
    return await w.row(
        "SELECT * FROM pending_purchases WHERE telegram_id=$1 ORDER BY id DESC LIMIT 1", tg)


async def buy(w: World, user, tariff: str, period: int, method: str) -> Optional[Dict[str, Any]]:
    """Walk the purchase screens and press the payment button. Returns the
    pending purchase the button created (None for balance — it pays at once)."""
    await open_payment_screen(w, user, tariff, period)
    shown = w.tg.with_button(user.id, f"pay:{method}") if method not in ("card",) else True
    assert shown, f"payment screen has no pay:{method} button"
    last_id = await w.val("SELECT COALESCE(max(id), 0) FROM pending_purchases WHERE telegram_id=$1", user.id)
    await w.tap(user, f"pay:{method}")
    if method == "balance":
        return None
    row = await latest_pending(w, user.id)
    # never hand back an older purchase: the button must have created this one
    if row is None or row["id"] <= last_id or row["status"] != "pending":
        raise AssertionError(f"pay:{method} created no purchase for {user.id}" + await screen_evidence(w, user))
    return row


async def screen_evidence(w: World, user) -> str:
    """What the user saw last + the FSM state — for a failed UI step."""
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=w.bot.id, chat_id=user.id, user_id=user.id)
    state = await w.dp.fsm.storage.get_state(key)
    data = await w.dp.fsm.storage.get_data(key)
    screens = [(s.method, (s.text or "")[:80], s.callback_data()[:6]) for s in w.tg.to(user.id)[-3:]]
    return f"\n  fsm_state={state} fsm_data={data}\n  last_screens={screens}"


def stars_price(tariff: str, period: int) -> int:
    return int(config.TARIFFS_STARS[base_of(tariff)][period]["price"])


async def pay(w: World, user, pending: Dict[str, Any], method: str, *, amount_rub: Optional[float] = None,
              tx_id: Optional[str] = None):
    """Deliver the provider's "paid" notification for `pending`."""
    price = pending["price_kopecks"] / 100.0
    amount = price if amount_rub is None else amount_rub
    pid = pending["purchase_id"]
    if method == "sbp":
        return await w.webhook(prov.platega_webhook(pid, amount, tx_id=tx_id))
    if method == "wata":
        return await w.webhook(prov.wata_webhook(pid, amount, tx_id=tx_id))
    if method == "crypto":
        return await w.webhook(prov.cryptobot_webhook(pid, amount))
    if method == "card":
        return await w.pay_telegram(user, f"purchase:{pid}", int(round(amount * 100)), "RUB", charge_id=tx_id)
    if method == "stars":
        # Pay exactly what the bot's invoice asked for (merged fix 031380e3: the
        # row keeps the RUB list price, the invoice carries the Stars amount).
        inv = next((i for i in reversed(w.tg.invoices)
                    if i.currency == "XTR" and i.payload == f"purchase:{pid}"), None)
        assert inv is not None, "no Stars invoice was sent for this purchase"
        stars = inv.prices[0].amount if amount_rub is None else int(amount_rub)
        return await w.pay_telegram(user, f"purchase:{pid}", stars, "XTR", charge_id=tx_id)
    raise ValueError(method)


@dataclass
class Before:
    expires_at: Optional[datetime]
    active: bool
    bypass: int
    payments: int
    at: datetime


async def snapshot(w: World, tg: int) -> Before:
    sub = await w.sub(tg)
    exp = sub["expires_at"] if sub else None
    active = bool(sub and sub["status"] == "active" and exp and exp > utcnow()
                  and not sub.get("is_bypass_only"))
    return Before(exp, active, w.panel.bypass_limit(tg) or 0, len(await w.payments(tg)), utcnow())


async def check_purchase(w: World, tg: int, before: Before, tariff: str, period: int, *,
                         paid: bool = True, bypass_gain: Optional[int] = None) -> Dict[str, Any]:
    """Owner rules for a paid purchase of `tariff`/`period`:
    * premium end in DB = max(now, current end) + period (renewal adds to the end)
    * panel premium expireAt == DB end (to the second)
    * bypass limit = previous + 10 GB (basic/plus) or + combo GB (combo)
    * exactly one new payments row
    """
    sub = await w.sub(tg)
    assert sub is not None, "no subscription row after a paid purchase"
    start_lo = before.expires_at if before.active else before.at
    start_hi = before.expires_at if before.active else utcnow()
    exp = sub["expires_at"]
    lo, hi = add_period(start_lo, period), add_period(start_hi, period)
    assert lo - timedelta(seconds=5) <= exp <= hi + timedelta(seconds=5), (
        f"DB expires_at {exp} != start({start_lo}) + period {period} (calendar months) = {lo}")
    assert sub["status"] == "active"
    assert (sub["subscription_type"] or "basic") == base_of(tariff), sub["subscription_type"]
    assert bool(sub.get("is_bypass_only")) is False
    panel_exp = w.panel.premium_expire(tg)
    assert panel_exp is not None, "no premium entity in the panel"
    assert abs((panel_exp - exp).total_seconds()) <= 1.5, f"panel {panel_exp} != DB {exp}"
    gain = expected_bypass_gain(tariff, period) if bypass_gain is None else bypass_gain
    assert (w.panel.bypass_limit(tg) or 0) == before.bypass + gain, (
        f"bypass {(w.panel.bypass_limit(tg) or 0) / GIB:.2f} GB != {before.bypass / GIB:.2f} + {gain / GIB:.2f} GB")
    if paid:
        assert len(await w.payments(tg)) == before.payments + 1
    return sub


async def seed_active(w: World, user, tariff: str, until: datetime, *, period: int = 30,
                      flag: Optional[str] = None) -> None:
    """Real purchase (Platega webhook), then move the end date to `until` in
    DB + panel: "an active subscriber until <until>". `flag` = provisioning
    mode of the SEED purchase only (the outbox writes the Remnawave cache
    columns; the legacy first purchase does not — E2E-CACHE)."""
    if not await w.val("SELECT 1 FROM users WHERE telegram_id=$1", user.id):
        await w.register(user)
    if flag:
        w.provisioning(flag)
    pending = await w.create_purchase(user, tariff, period, provider="platega")
    res = await pay(w, user, pending, "sbp")
    assert res.status == 200 and res.body.get("status") == "ok", res
    if flag:
        await w.provisioning_tick()
        w.provisioning("off")
    # the seed purchase happened long ago (outside the 60 s balance double-tap window)
    await w.pool.execute(
        "UPDATE payments SET created_at = created_at - interval '40 days', "
        "paid_at = paid_at - interval '40 days' WHERE telegram_id=$1", user.id)
    await w.set_expiry(user.id, until)


async def seed_expired(w: World, user, tariff: str = "basic") -> None:
    """A subscriber whose premium ended 5 days ago (status still active, the
    cleanup worker did not run yet — the state the webhook sees)."""
    await seed_active(w, user, tariff, utcnow() + timedelta(days=3))
    past = utcnow() - timedelta(days=5)
    await w.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1",
                         user.id, past.replace(tzinfo=None))
    ent = w.panel.premium(user.id)
    if ent is not None:
        ent["expireAt"] = past
        ent["status"] = "EXPIRED"


__all__ = [
    "METHOD_PROVIDER", "Before", "base_of", "buy", "check_purchase", "expected_bypass_gain",
    "latest_pending", "open_payment_screen", "pay", "seed_active", "seed_expired", "snapshot",
    "stars_price", "tariff_price", "aware", "MB",
]
