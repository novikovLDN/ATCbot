"""
Shared payment confirmation logic for all webhook providers.

Eliminates duplicate code between platega_service and cryptobot_service.
Each provider handles auth/signature verification, then delegates here.
"""
import json
import logging
from typing import Optional, Dict, Any

import config
import database
from aiogram import Bot

logger = logging.getLogger(__name__)


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

    except ValueError as e:
        logger.info(
            f"{provider} webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "already_processed"}
    except Exception as e:
        logger.exception(
            f"{provider} webhook: finalize_purchase failed: user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
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
