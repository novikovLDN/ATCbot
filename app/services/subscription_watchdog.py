"""Subscription over-issuance watchdog.

Called by `database.subscriptions.grant_access` after EVERY write to
`subscriptions.expires_at`. If the new value lands more than 8 years in the
future for a PREMIUM subscription (i.e. not bypass-only), we:

  1. Insert a row into `subscription_over_issuance_log` with the full call
     context (source, tariff, admin ids, python stack snippet) so the admin
     dashboard can retrace the write.

  2. Send a Telegram alert to ADMIN_TELEGRAM_ID via `app.services.admin_alerts`
     (category "security" — no cooldown, always fires).

Bypass-only subscriptions intentionally sit at NOW + 10 years — those
writes are filtered out at the call-site (grant_access passes
`is_bypass_only=True`).

The hook is FIRE-AND-FORGET: it schedules a background task and returns
immediately, so it can never block or fail a grant/purchase.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
import database

logger = logging.getLogger(__name__)


# Threshold — anything above this from NOW is an anomaly.
_EIGHT_YEARS = timedelta(days=365 * 8)


def notify_expires_at_write(
    telegram_id: int,
    *,
    old_expires_at: Optional[datetime],
    new_expires_at: datetime,
    grant_action: str,
    source: Optional[str] = None,
    tariff: Optional[str] = None,
    admin_telegram_id: Optional[int] = None,
    admin_grant_days: Optional[int] = None,
    is_bypass_only: bool = False,
    extra_context: Optional[str] = None,
) -> None:
    """Fire-and-forget hook invoked from grant_access after every expires_at
    write. Non-blocking; never raises to the caller.

    Args:
        telegram_id: user whose subscription was just written
        old_expires_at: the previous expires_at value (None on new issuance)
        new_expires_at: the freshly-written value
        grant_action: 'renewal' | 'new_issuance' | 'upgrade' | 'admin_grant' | ...
        source: grant_access source parameter (payment/admin/trial/…)
        tariff: incoming tariff string, if known
        admin_telegram_id, admin_grant_days: for source='admin' writes
        is_bypass_only: skip anomaly detection for bypass-only rows
        extra_context: extra free-form context to append after the stack trace
    """
    if is_bypass_only:
        return
    if not new_expires_at:
        return

    now = datetime.now(timezone.utc)
    threshold = now + _EIGHT_YEARS
    if new_expires_at <= threshold:
        return  # Within normal range — no action needed.

    # Capture the python stack up to (but not including) grant_access so we
    # can see WHERE the excessive duration came from. Truncated for storage.
    stack = "".join(traceback.format_stack(limit=12)[:-1])
    context_parts = [f"stack:\n{stack}"]
    if extra_context:
        context_parts.append(f"extra: {extra_context}")
    caller_context = "\n".join(context_parts)

    # Schedule the DB write + alert. We don't await — grant_access must not
    # be delayed by DB/Telegram RTT on the hot path.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (called from sync context somehow) — skip silently.
        logger.warning(
            "SUBSCRIPTION_WATCHDOG: no running loop, skipping alert user=%s",
            telegram_id,
        )
        return

    loop.create_task(_dispatch(
        telegram_id=telegram_id,
        old_expires_at=old_expires_at,
        new_expires_at=new_expires_at,
        grant_action=grant_action,
        source=source,
        tariff=tariff,
        admin_telegram_id=admin_telegram_id,
        admin_grant_days=admin_grant_days,
        caller_context=caller_context,
    ))


async def _dispatch(
    telegram_id: int,
    *,
    old_expires_at: Optional[datetime],
    new_expires_at: datetime,
    grant_action: str,
    source: Optional[str],
    tariff: Optional[str],
    admin_telegram_id: Optional[int],
    admin_grant_days: Optional[int],
    caller_context: str,
) -> None:
    """Log + alert. Never raises."""
    # 1) Persist to DB so the dashboard can retrace even after bot restart.
    try:
        log_id = await database.record_over_issuance(
            telegram_id,
            old_expires_at=old_expires_at,
            new_expires_at=new_expires_at,
            grant_action=grant_action,
            source=source,
            tariff=tariff,
            admin_telegram_id=admin_telegram_id,
            admin_grant_days=admin_grant_days,
            caller_context=caller_context,
        )
    except Exception as e:
        logger.warning("SUBSCRIPTION_WATCHDOG: DB log failed user=%s: %s", telegram_id, e)
        log_id = None

    now = datetime.now(timezone.utc)
    days_delta = (new_expires_at - now).days
    years_delta = round(days_delta / 365.0, 2)
    added = None
    if old_expires_at:
        added_days = (new_expires_at - old_expires_at).days
        added_years = round(added_days / 365.0, 2)
        added = f"+{added_days}d (+{added_years}y)"

    logger.error(
        "OVER_ISSUANCE_DETECTED user=%s log_id=%s new_expires_at=%s "
        "grant_action=%s source=%s tariff=%s admin_id=%s admin_grant_days=%s "
        "days_from_now=%s added=%s",
        telegram_id, log_id, new_expires_at.isoformat(),
        grant_action, source, tariff, admin_telegram_id, admin_grant_days,
        days_delta, added,
    )

    # 2) Send admin alert via the shared service (category=security → force).
    bot = _resolve_bot()
    if bot is None:
        return

    try:
        from app.services import admin_alerts
    except Exception as imp_err:
        logger.warning("SUBSCRIPTION_WATCHDOG: admin_alerts import failed: %s", imp_err)
        return

    lines = [
        "⚠️ Обнаружена подозрительная выдача подписки",
        f"👤 user_id: <code>{telegram_id}</code>",
        f"📆 новый expires_at: {new_expires_at.isoformat()}",
        f"⏳ до истечения: {days_delta} дней (~{years_delta} лет)",
    ]
    if old_expires_at:
        lines.append(f"↳ было: {old_expires_at.isoformat()}")
    if added:
        lines.append(f"↳ добавлено: {added}")
    lines.append(f"🎬 grant_action: {grant_action}")
    if source:
        lines.append(f"📎 source: {source}")
    if tariff:
        lines.append(f"🏷 tariff: {tariff}")
    if admin_telegram_id:
        lines.append(f"👨‍💼 admin_id: {admin_telegram_id}")
    if admin_grant_days is not None:
        lines.append(f"➕ admin_grant_days: {admin_grant_days}")
    if log_id:
        lines.append(f"🧾 log_id: {log_id} (см. дашборд «Сверка»)")

    try:
        await admin_alerts.send_alert(
            bot,
            "security",
            "\n".join(lines),
            force=True,
        )
    except Exception as alert_err:
        logger.warning(
            "SUBSCRIPTION_WATCHDOG: send_alert failed user=%s: %s",
            telegram_id, alert_err,
        )


def _resolve_bot():
    """Fetch the live aiogram Bot from the webhook module — same trick as
    app.api.dashboard.routes.broadcasts._get_bot. Returns None if the bot
    isn't ready yet."""
    try:
        from app.api import telegram_webhook
        return getattr(telegram_webhook, "_bot", None)
    except Exception:
        return None


__all__ = ["notify_expires_at_write"]
