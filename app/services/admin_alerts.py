"""
Critical admin alert service.

Sends alerts to ADMIN_TELEGRAM_ID for events that require immediate attention:
- Payment processing failures
- Subscription activation failures
- Worker crashes / prolonged failures
- Database connectivity issues
- VPN API failures affecting users

Rate-limited per category to prevent alert storms.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Per-category cooldowns to prevent alert spam (seconds)
_ALERT_COOLDOWNS = {
    "payment": 60,        # 1 min — every payment failure is important
    "subscription": 120,  # 2 min
    "worker": 300,        # 5 min
    "database": 600,      # 10 min
    "vpn_api": 300,       # 5 min
    "security": 0,        # no cooldown — always alert
}

# Last alert timestamp per category (started by NON-forced alerts only)
_last_alert_at: dict[str, float] = {}

_clock = time.monotonic          # tests patch it
_RETRY_DELAY = 2.0               # one retry for transient Telegram errors
_DIGEST_MIN_DELAY = 5.0          # earliest digest attempt after a failed send
# TG-RT-8: the admin chat itself is unreachable (blocked bot / wrong
# ADMIN_TELEGRAM_ID) — every failed digest doubles the next retry delay
# (5 s, 10 s, 20 s … capped here) instead of retrying every 5 s forever.
_DIGEST_MAX_DELAY = 3600.0
_digest_failures: dict[str, int] = {}
_DIGEST_KEEP = 20                # alert texts kept per category (count stays exact)
_DIGEST_ITEM_CHARS = 300

# HOW_IT_WORKS P2: an alert held back by the cooldown or whose send failed is
# NOT dropped — it waits here and goes out as ONE digest per category when the
# cooldown ends (process-local; payment_errors / logs stay the full record).
_held: dict[str, list[str]] = {}
_held_count: dict[str, int] = {}
_flush_tasks: dict[str, asyncio.Task] = {}


def reset_state() -> None:
    """Forget cooldowns and held alerts (tests)."""
    for task in _flush_tasks.values():
        if not task.done():
            task.cancel()
    _flush_tasks.clear()
    _held.clear()
    _held_count.clear()
    _last_alert_at.clear()
    _digest_failures.clear()


def pending_digest_counts() -> dict[str, int]:
    """Held alerts per category, not yet delivered in a digest."""
    return {k: n for k, n in _held_count.items() if n}


def _format(category: str, message: str) -> str:
    header = _CATEGORY_HEADERS.get(category, f"[{category.upper()}]")
    full_message = f"{header}\n{message}"
    # Truncate to Telegram message limit
    if len(full_message) > 4000:
        full_message = full_message[:3997] + "..."
    return full_message


async def _deliver(bot, category: str, full_message: str) -> bool:
    """Send with one retry. No cooldown logic. True = delivered."""
    try:
        await asyncio.wait_for(bot.send_message(config.ADMIN_TELEGRAM_ID, full_message), timeout=10.0)
        logger.info(f"ADMIN_ALERT_SENT category={category}")
        return True
    except Exception as e:
        logger.error(f"ADMIN_ALERT_FAILED category={category} error={e}")
    try:
        await asyncio.sleep(_RETRY_DELAY)
        await asyncio.wait_for(bot.send_message(config.ADMIN_TELEGRAM_ID, full_message), timeout=10.0)
        logger.info(f"ADMIN_ALERT_SENT_RETRY category={category}")
        return True
    except Exception as retry_err:
        logger.error(f"ADMIN_ALERT_RETRY_FAILED category={category} error={retry_err}")
        return False


def _hold(bot, category: str, message: str, *, count: int = 1, items=None) -> None:
    """Keep an undelivered alert for the category digest and make sure a flush is scheduled."""
    _held_count[category] = _held_count.get(category, 0) + count
    kept = _held.setdefault(category, [])
    for text in (items if items is not None else [message]):
        if len(kept) < _DIGEST_KEEP:
            kept.append(str(text)[:_DIGEST_ITEM_CHARS])
    logger.warning("ADMIN_ALERT_HELD category=%s held=%s", category, _held_count[category])
    task = _flush_tasks.get(category)
    if task is not None and not task.done():
        return
    cooldown = _ALERT_COOLDOWNS.get(category, 300)
    remaining = cooldown - (_clock() - _last_alert_at.get(category, float("-inf")))
    backoff = min(_DIGEST_MIN_DELAY * (2 ** _digest_failures.get(category, 0)), _DIGEST_MAX_DELAY)
    delay = max(remaining, backoff)
    try:
        _flush_tasks[category] = asyncio.get_running_loop().create_task(_flush_later(bot, category, delay))
    except RuntimeError:  # no running loop — the next alert of the category schedules it
        pass


async def _flush_later(bot, category: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    finally:
        if _flush_tasks.get(category) is asyncio.current_task():
            del _flush_tasks[category]
    await flush_digest(bot, category)


async def flush_digest(bot, category: str) -> bool:
    """Send the held alerts of `category` as one message. A failed send keeps
    them (and schedules another attempt). Returns True if a digest was sent."""
    count = _held_count.pop(category, 0)
    items = _held.pop(category, [])
    if not count:
        return False
    lines = [f"DIGEST: {count} alert(s) held back by the cooldown or not delivered:"]
    for text in items:
        lines.append("—— " + text)
    if count > len(items):
        lines.append(f"… and {count - len(items)} more (see logs / payment_errors)")
    if await _deliver(bot, category, _format(category, "\n".join(lines))):
        _digest_failures.pop(category, None)
        _count_alert(category)
        return True
    _digest_failures[category] = min(_digest_failures.get(category, 0) + 1, 20)
    _hold(bot, category, "", count=count, items=items)
    return False


async def flush_all_digests(bot) -> int:
    """Send every held digest now (shutdown). Returns the number sent."""
    sent = 0
    for category in list(_held_count):
        task = _flush_tasks.pop(category, None)
        if task is not None and not task.done():
            task.cancel()
        try:
            if await flush_digest(bot, category):
                sent += 1
        except Exception as e:  # noqa: BLE001
            logger.error("ADMIN_ALERT_DIGEST_FLUSH_FAILED category=%s: %s", category, e)
    return sent


async def send_alert(
    bot,
    category: str,
    message: str,
    *,
    force: bool = False,
) -> bool:
    """Send critical alert to admin.

    Args:
        bot: aiogram Bot instance
        category: Alert category (payment, subscription, worker, database, vpn_api, security)
        message: Alert text (will be prefixed with category header)
        force: Bypass cooldown (for truly critical one-off events)

    Returns:
        True if the alert was sent now. False: inside the cooldown or the send
        failed — the alert is then held and delivered in the category digest
        when the cooldown ends (never dropped).
    """
    now = _clock()
    cooldown = _ALERT_COOLDOWNS.get(category, 300)

    if not force:
        last = _last_alert_at.get(category, float("-inf"))
        if now - last < cooldown:
            _hold(bot, category, message)
            return False

    if await _deliver(bot, category, _format(category, message)):
        _digest_failures.pop(category, None)      # the admin chat works again
        if not force:
            # Forced alerts never start the cooldown: they must not silence the
            # next ordinary alert of the category.
            _last_alert_at[category] = now
        _count_alert(category)
        return True
    _hold(bot, category, message)
    return False


def _count_alert(category: str) -> None:
    """Admin alert volume for the dashboard (in-memory, never raises)."""
    try:
        from app.core import runtime_health
        runtime_health.record_alert(category)
    except Exception:
        pass


_CATEGORY_HEADERS = {
    "payment": "PAYMENT ALERT",
    "subscription": "SUBSCRIPTION ALERT",
    "worker": "WORKER ALERT",
    "database": "DATABASE ALERT",
    "vpn_api": "VPN API ALERT",
    "security": "SECURITY ALERT",
}


# Convenience functions for common alert scenarios

async def alert_payment_failure(
    bot,
    provider: str,
    telegram_id: int,
    purchase_id: str,
    error: Exception,
    is_transient: bool = False,
    *,
    amount_rubles: Optional[float] = None,
    tariff: Optional[str] = None,
    period_days: Optional[int] = None,
) -> bool:
    """Alert admin about payment processing failure."""
    severity = "TRANSIENT" if is_transient else "PERMANENT"
    retry = "Provider will retry." if is_transient else "NEEDS MANUAL CHECK!"
    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    details = (
        f"[{severity}]\n"
        f"Date: {now_str}\n"
        f"Provider: {provider}\n"
        f"User TG ID: {telegram_id}\n"
        f"Purchase: {purchase_id}\n"
    )
    if amount_rubles is not None:
        details += f"Amount: {amount_rubles} RUB\n"
    if tariff:
        details += f"Tariff: {tariff}\n"
    if period_days is not None:
        details += f"Period: {period_days} days\n"
    details += (
        f"Error: {type(error).__name__}: {str(error)[:200]}\n"
        f"{retry}"
    )
    return await send_alert(
        bot,
        "payment",
        details,
        force=not is_transient,  # permanent failures always alert
    )


async def alert_subscription_failure(
    bot,
    telegram_id: int,
    action: str,
    error: Exception,
    *,
    amount_rubles: Optional[float] = None,
    tariff: Optional[str] = None,
    period_days: Optional[int] = None,
    subscription_id: Optional[int] = None,
) -> bool:
    """Alert admin about subscription activation/renewal failure."""
    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    details = (
        f"Action: {action}\n"
        f"Date: {now_str}\n"
        f"User TG ID: {telegram_id}\n"
    )
    if subscription_id is not None:
        details += f"Subscription ID: {subscription_id}\n"
    if amount_rubles is not None:
        details += f"Amount: {amount_rubles} RUB\n"
    if tariff:
        details += f"Tariff: {tariff}\n"
    if period_days is not None:
        details += f"Period: {period_days} days\n"
    details += f"Error: {type(error).__name__}: {str(error)[:200]}"
    return await send_alert(
        bot,
        "subscription",
        details,
    )


async def alert_worker_failure(
    bot,
    worker_name: str,
    error: Exception,
    iteration: Optional[int] = None,
) -> bool:
    """Alert admin about background worker failure."""
    iter_str = f"\nIteration: {iteration}" if iteration is not None else ""
    return await send_alert(
        bot,
        "worker",
        f"Worker: {worker_name}{iter_str}\n"
        f"Error: {type(error).__name__}: {str(error)[:200]}",
    )


