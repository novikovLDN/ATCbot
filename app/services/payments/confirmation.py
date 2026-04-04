"""
Shared payment confirmation logic for all webhook providers.

Eliminates duplicate code between platega_service and cryptobot_service.
Each provider handles auth/signature verification, then delegates here.
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any

import asyncpg
import config
import database
from aiogram import Bot

logger = logging.getLogger(__name__)


class TransientPaymentError(Exception):
    """Transient error during payment processing (DB timeout, connection error).

    Webhook handler should return HTTP 500 so the payment provider retries.
    """
    pass


async def process_confirmed_payment(
    provider: str,
    purchase_id: str,
    amount_rubles: float,
    invoice_id: str,
    telegram_id: int,
    bot: Bot,
) -> dict:
    """
    Shared logic for processing a confirmed payment webhook.

    Called after provider-specific auth verification and payload extraction.

    Args:
        provider: Payment provider name ("platega", "cryptobot")
        purchase_id: Internal purchase ID
        amount_rubles: Payment amount in RUB
        invoice_id: Provider's transaction/invoice ID
        telegram_id: Buyer's Telegram ID
        bot: Bot instance for sending confirmation messages

    Returns:
        Response dict with "status" key ("ok", "already_processed", "error")
    """
    try:
        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=provider,
            amount_rubles=amount_rubles,
            invoice_id=str(invoice_id),
        )

        if not result or not result.get("success"):
            logger.error(f"{provider} webhook: finalize_purchase failed: {result}")
            raise Exception(f"finalize_purchase returned invalid result: {result}")

        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        is_balance_topup = result.get("is_balance_topup", False)

        # Sync with website (fire-and-forget, must not fail payment)
        try:
            from app.handlers.user.site_link import notify_site_after_payment
            purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
            if purchase and not is_balance_topup:
                tariff = purchase.get("tariff", "basic")
                period_days = purchase.get("period_days", 30)
                await notify_site_after_payment(telegram_id, period_days, tariff)
        except Exception as site_err:
            logger.warning(
                "SITE_SYNC_AFTER_PAYMENT_FAILED: user=%s, purchase_id=%s, error=%s",
                telegram_id, purchase_id, site_err,
            )

        # Remnawave: renew/create user on Yandex node (fire-and-forget)
        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            if not is_balance_topup and expires_at:
                purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
                tariff = purchase.get("tariff", "basic") if purchase else "basic"
                renew_remnawave_user_bg(telegram_id, expires_at, tariff)
        except Exception as rmn_err:
            logger.warning(
                "REMNAWAVE_AFTER_PAYMENT_FAILED: user=%s, error=%s",
                telegram_id, rmn_err,
            )

        # Notification failure must NOT fail the payment — DB is already committed
        try:
            await _send_confirmation(
                provider=provider,
                bot=bot,
                telegram_id=telegram_id,
                payment_id=payment_id,
                purchase_id=purchase_id,
                is_balance_topup=is_balance_topup,
                amount_rubles=amount_rubles,
                result=result,
                expires_at=expires_at,
            )
        except Exception as notif_err:
            logger.error(
                f"PAYMENT_NOTIFICATION_FAILED: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, payment_id={payment_id}, "
                f"error={type(notif_err).__name__}: {notif_err} — payment was successful"
            )

    except ValueError as e:
        logger.info(
            f"{provider} webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "already_processed"}
    except (asyncpg.PostgresError, asyncio.TimeoutError, OSError) as e:
        # Transient infrastructure error — provider MUST retry
        logger.error(
            f"PAYMENT_TRANSIENT_ERROR: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, error={type(e).__name__}: {e}"
        )
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=True,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
        raise TransientPaymentError(
            f"Transient DB error during payment: {type(e).__name__}"
        ) from e
    except Exception as e:
        logger.exception(
            f"PAYMENT_PERMANENT_ERROR: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
        )
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=False,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
        return {"status": "error"}

    return {"status": "ok"}


def extract_purchase_id(payload_raw: Any) -> Optional[str]:
    """Extract purchase_id from webhook payload (JSON string or dict)."""
    if not payload_raw:
        return None
    try:
        if isinstance(payload_raw, str):
            payload_data = json.loads(payload_raw)
        else:
            payload_data = payload_raw
        return payload_data.get("purchase_id")
    except (json.JSONDecodeError, TypeError):
        return None


async def lookup_pending_purchase(
    provider: str,
    purchase_id: str,
) -> dict:
    """
    Look up pending purchase and validate status.

    Returns:
        {"status": "ok", "purchase": dict, "telegram_id": int} on success
        {"status": "not_found"|"already_processed"} on failure
    """
    pending_purchase = await database.get_pending_purchase_by_id(
        purchase_id, check_expiry=False
    )

    if not pending_purchase:
        logger.warning(f"{provider} webhook: purchase not found: purchase_id={purchase_id}")
        return {"status": "not_found"}

    telegram_id = pending_purchase["telegram_id"]
    purchase_status = pending_purchase.get("status")

    if purchase_status != "pending":
        logger.info(
            f"{provider} webhook: purchase already processed: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return {"status": "already_processed"}

    return {
        "status": "ok",
        "purchase": pending_purchase,
        "telegram_id": telegram_id,
    }


async def _lookup_purchase_tariff(purchase_id: str) -> tuple:
    """Look up tariff and period_days from pending_purchases for alert context.

    Returns (tariff, period_days) or (None, None) on any failure.
    """
    try:
        row = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
        if row:
            return row.get("tariff"), row.get("period_days")
    except Exception:
        pass
    return None, None


async def _send_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    payment_id: int,
    purchase_id: str,
    is_balance_topup: bool,
    amount_rubles: float,
    result: dict,
    expires_at: Any,
) -> None:
    """Send payment confirmation message to user."""
    from app.services.language_service import resolve_user_language
    from app.i18n import get_text as i18n_get_text

    language = await resolve_user_language(telegram_id)

    if is_balance_topup:
        topup_amount = result.get("amount", amount_rubles)
        text = i18n_get_text(language, "main.balance_topup_success", amount=topup_amount)
        try:
            await bot.send_message(telegram_id, text, parse_mode="HTML")
        except Exception as send_err:
            logger.warning(
                f"{provider}: failed to send topup confirmation to user={telegram_id}: {send_err}"
            )
        logger.info(
            f"{provider} payment processed (balance topup): user={telegram_id}, "
            f"payment_id={payment_id}, amount={topup_amount} RUB"
        )
    else:
        expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
        subscription_type = (result.get("subscription_type") or "basic").strip().lower()
        if subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
            subscription_type = "basic"

        if config.is_biz_tariff(subscription_type):
            _label, _emoji = "Business", "🏢"
        elif subscription_type == "plus":
            _label, _emoji = "Plus", "⭐️"
        else:
            _label, _emoji = "Basic", "📦"

        text = i18n_get_text(
            language,
            "payment.success",
            f"🎉 Оплата получена!\n{_emoji} Тариф: {_label}\n📅 До: {expires_str}",
            tariff_icon=_emoji,
            tariff=_label,
            date=expires_str,
        )

        from app.handlers.common.keyboards import get_connect_keyboard

        try:
            await bot.send_message(
                telegram_id, text, reply_markup=get_connect_keyboard(), parse_mode="HTML"
            )
        except Exception as send_err:
            logger.warning(
                f"{provider}: failed to send subscription confirmation to user={telegram_id}: {send_err}"
            )

        logger.info(
            f"{provider} payment processed: user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, subscription_activated=True"
        )


