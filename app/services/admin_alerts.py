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

# Last alert timestamp per category
_last_alert_at: dict[str, float] = {}


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
        True if alert was sent, False if rate-limited or failed
    """
    now = time.monotonic()
    cooldown = _ALERT_COOLDOWNS.get(category, 300)

    if not force:
        last = _last_alert_at.get(category, 0.0)
        if now - last < cooldown:
            return False

    try:
        header = _CATEGORY_HEADERS.get(category, f"[{category.upper()}]")
        full_message = f"{header}\n{message}"

        # Truncate to Telegram message limit
        if len(full_message) > 4000:
            full_message = full_message[:3997] + "..."

        await asyncio.wait_for(
            bot.send_message(config.ADMIN_TELEGRAM_ID, full_message),
            timeout=10.0,
        )
        _last_alert_at[category] = now
        logger.info(f"ADMIN_ALERT_SENT category={category}")
        return True
    except Exception as e:
        logger.warning(f"ADMIN_ALERT_FAILED category={category} error={e}")
        return False


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
) -> bool:
    """Alert admin about payment processing failure."""
    severity = "TRANSIENT" if is_transient else "PERMANENT"
    retry = "Provider will retry." if is_transient else "NEEDS MANUAL CHECK!"
    return await send_alert(
        bot,
        "payment",
        f"[{severity}]\n"
        f"Provider: {provider}\n"
        f"User: {telegram_id}\n"
        f"Purchase: {purchase_id}\n"
        f"Error: {type(error).__name__}: {str(error)[:200]}\n"
        f"{retry}",
        force=not is_transient,  # permanent failures always alert
    )


async def alert_subscription_failure(
    bot,
    telegram_id: int,
    action: str,
    error: Exception,
) -> bool:
    """Alert admin about subscription activation/renewal failure."""
    return await send_alert(
        bot,
        "subscription",
        f"Action: {action}\n"
        f"User: {telegram_id}\n"
        f"Error: {type(error).__name__}: {str(error)[:200]}",
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


async def alert_vpn_api_failure(
    bot,
    operation: str,
    telegram_id: int,
    error: Exception,
) -> bool:
    """Alert admin about VPN API failure affecting a user."""
    return await send_alert(
        bot,
        "vpn_api",
        f"Operation: {operation}\n"
        f"User: {telegram_id}\n"
        f"Error: {type(error).__name__}: {str(error)[:200]}",
    )


async def alert_security_event(
    bot,
    event: str,
    details: str,
) -> bool:
    """Alert admin about security event (always sends, no cooldown)."""
    return await send_alert(
        bot,
        "security",
        f"Event: {event}\n{details}",
        force=True,
    )
