"""Referral cashback notification — for EVERY accrual, exactly once (owner, 2026-09-14).

The one place cashback is accrued is database.users.process_referral_reward
(every purchase kind and payment method goes through it: provider webhooks,
Telegram card / Stars, balance, auto-renewal, gifts, GB packs, shop). It runs
inside the caller's billing transaction, so it cannot send: a rolled-back
accrual must not notify, and no HTTP call may run inside a transaction.

So on success it calls schedule(): a background task waits until the
referral_rewards row of (buyer_id, purchase_id) is visible — i.e. the caller
committed — and then sends once. (buyer_id, purchase_id) is the table's
unique key: at most one committed accrual per key, and an in-process claim on
the key makes a rolled-back-then-retried accrual send one message, not two.
A row that never becomes visible (rolled back) is never announced. Farm /
game purchases accrue no cashback, so they never get here.

Best effort: the accrual is the money; a lost notification (restart while
waiting, Telegram error) is only logged. Never raises.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Waits between visibility checks (~12 s in total): the caller commits right
# after process_referral_reward returns, normally within milliseconds.
_POLL_DELAYS = (0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0)

_claimed: set = set()      # (buyer_id, purchase_id) already announced in this process
_tasks: set = set()        # strong refs to running notifier tasks


def _bot():
    from app.services import purchase_flow
    return purchase_flow._alert_bot()


def schedule(
    *,
    buyer_id: int,
    referrer_id: int,
    purchase_id: str,
    purchase_amount: float,
    reward_amount: float,
    percent: int,
    paid_referrals_count: int,
    referrals_needed: int,
) -> bool:
    """Start the post-commit notifier. False when there is no bot / event loop
    (tests, scripts) — nothing is scheduled then. Never raises."""
    try:
        bot = _bot()
        if bot is None or not purchase_id:
            return False
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001 — no loop / no bot: nothing to send with
        return False
    task = loop.create_task(_notify_after_commit(
        bot,
        buyer_id=int(buyer_id), referrer_id=int(referrer_id), purchase_id=str(purchase_id),
        purchase_amount=float(purchase_amount or 0), reward_amount=float(reward_amount or 0),
        percent=int(percent or 0), paid_referrals_count=int(paid_referrals_count or 0),
        referrals_needed=int(referrals_needed or 0),
    ))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def _row_committed(buyer_id: int, purchase_id: str) -> Optional[bool]:
    """True when the accrual is committed (visible), None when the DB is unreachable."""
    from database.core import get_pool
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM referral_rewards WHERE buyer_id = $1 AND purchase_id = $2",
            buyer_id, purchase_id,
        ))


async def _purchase_context(purchase_id: str) -> tuple[str, Optional[int]]:
    """(action_type, period_days) of the purchase the cashback is for."""
    if purchase_id.startswith("autorenew_"):
        return "renewal", None
    if purchase_id.startswith("balance_purchase_"):
        return "purchase", None
    try:
        import database
        row = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False) or {}
    except Exception:  # noqa: BLE001 — context is decoration only
        row = {}
    if (row.get("purchase_type") or "subscription") in ("subscription", "gift") and row.get("period_days"):
        return "purchase", int(row["period_days"])
    return "purchase", None


async def _send(bot, *, buyer_id: int, referrer_id: int, purchase_id: str, purchase_amount: float,
                reward_amount: float, percent: int, paid_referrals_count: int,
                referrals_needed: int) -> bool:
    from app.handlers.notifications import send_referral_cashback_notification
    from app.services.language_service import resolve_user_language
    from app.services.payments.success_message import period_display

    action_type, period_days = await _purchase_context(purchase_id)
    period = None
    if period_days:
        try:
            language = await resolve_user_language(referrer_id)
        except Exception:  # noqa: BLE001
            language = "ru"
        period = period_display(language, period_days)   # referrer's language (was always RU)
    return await send_referral_cashback_notification(
        bot=bot,
        referrer_id=referrer_id,
        referred_id=buyer_id,
        purchase_amount=purchase_amount,
        cashback_amount=reward_amount,
        cashback_percent=percent,
        paid_referrals_count=paid_referrals_count,
        referrals_needed=referrals_needed,
        action_type=action_type,
        subscription_period=period,
    )


async def _notify_after_commit(bot, **kw) -> bool:
    """Wait for the accrual to be committed, then announce it once. Returns True if sent."""
    key = (kw["buyer_id"], kw["purchase_id"])
    for delay in _POLL_DELAYS:
        await asyncio.sleep(delay)
        try:
            committed = await _row_committed(kw["buyer_id"], kw["purchase_id"])
        except Exception as e:  # noqa: BLE001
            logger.debug("REFERRAL_CASHBACK_NOTIFY_CHECK_FAILED purchase_id=%s: %s", kw["purchase_id"], e)
            committed = None
        if not committed:
            continue
        if key in _claimed:
            return False
        _claimed.add(key)
        try:
            sent = await _send(bot, **kw)
        except Exception as e:  # noqa: BLE001
            logger.warning("REFERRAL_CASHBACK_NOTIFY_FAILED purchase_id=%s: %s", kw["purchase_id"], type(e).__name__)
            return False
        logger.info(
            "REFERRAL_CASHBACK_NOTIFIED referrer=%s buyer=%s purchase_id=%s sent=%s",
            kw["referrer_id"], kw["buyer_id"], kw["purchase_id"], sent,
        )
        return bool(sent)
    logger.info(
        "REFERRAL_CASHBACK_NOTIFY_SKIPPED purchase_id=%s — accrual not committed (rolled back) or DB unreachable",
        kw["purchase_id"],
    )
    return False
