"""
CryptoBot (Crypto Pay) Integration

Handles cryptocurrency payment creation and webhook processing via @CryptoBot API.
Configuration: CRYPTOBOT_API_TOKEN / CRYPTOBOT_API_URL resolved via config.py only.

API docs: https://help.crypt.bot/crypto-pay-api
"""
import config
import database
import hashlib
import hmac
import json
import logging
from typing import Dict, Any

import httpx
from aiogram import Bot
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

CRYPTOBOT_API_TOKEN = config.CRYPTOBOT_API_TOKEN
CRYPTOBOT_API_URL = config.CRYPTOBOT_API_URL


def is_enabled() -> bool:
    """Check if CryptoBot is configured (API token present)."""
    return bool(CRYPTOBOT_API_TOKEN)


def _get_headers() -> Dict[str, str]:
    """Get authentication headers for Crypto Pay API."""
    return {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def create_invoice(
    amount_rubles: float,
    description: str,
    purchase_id: str,
    expires_in: int = 1800,
) -> Dict[str, Any]:
    """
    Create a cryptocurrency payment invoice via CryptoBot API.

    Uses currency_type=fiat with fiat=RUB so the user pays the equivalent
    in any supported crypto (USDT, TON, BTC, etc.).

    Args:
        amount_rubles: Payment amount in RUB
        description: Invoice description
        purchase_id: Internal purchase ID (stored in payload)
        expires_in: Invoice expiration in seconds (default 30 min)

    Returns:
        {"invoice_id": int, "mini_app_invoice_url": str, "web_app_invoice_url": str}
    """
    if not is_enabled():
        raise Exception("CryptoBot not configured")

    request_body = {
        "currency_type": "fiat",
        "fiat": "RUB",
        "accepted_assets": "USDT,TON,BTC,ETH,LTC,TRX",
        "amount": str(round(amount_rubles, 2)),
        "description": description[:1024] if description else "Atlas Secure VPN",
        "payload": json.dumps({"purchase_id": purchase_id}),
        "expires_in": expires_in,
    }

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CRYPTOBOT_API_URL}/createInvoice",
                headers=_get_headers(),
                json=request_body,
            )
            if response.status_code != 200:
                logger.error(
                    f"CryptoBot API error: status={response.status_code}, "
                    f"response={response.text[:300]}"
                )
                raise Exception(f"CryptoBot API error: {response.status_code}")
            return response

    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError),
    )

    data = response.json()
    if not data.get("ok"):
        error = data.get("error", {})
        raise Exception(f"CryptoBot API error: {error}")

    result = data["result"]
    invoice_id = result["invoice_id"]
    mini_app_url = result.get("mini_app_invoice_url", "")
    web_app_url = result.get("web_app_invoice_url", "")
    pay_url = mini_app_url or web_app_url

    if not pay_url:
        raise Exception(f"CryptoBot API: missing payment URL in response: {result}")

    logger.info(
        f"CryptoBot invoice created: invoice_id={invoice_id}, "
        f"amount={amount_rubles} RUB, purchase_id={purchase_id}"
    )

    return {
        "invoice_id": invoice_id,
        "mini_app_invoice_url": mini_app_url,
        "web_app_invoice_url": web_app_url,
        "pay_url": pay_url,
    }


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Verify CryptoBot webhook signature.

    The signature is HMAC-SHA-256 of the raw body,
    with the secret key being SHA-256 hash of the API token.
    """
    if not CRYPTOBOT_API_TOKEN:
        return False
    secret = hashlib.sha256(CRYPTOBOT_API_TOKEN.encode("utf-8")).digest()
    computed = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


async def process_webhook_data(headers: dict, raw_body: bytes, body: dict, bot: Bot) -> dict:
    """
    Process CryptoBot webhook data (framework-agnostic).

    Args:
        headers: Request headers dict
        raw_body: Raw request body bytes (for signature verification)
        body: Parsed JSON body
        bot: Bot instance for sending messages

    Returns:
        Response dict with "status" key
    """
    if not database.DB_READY:
        logger.warning("CryptoBot webhook: DB not ready")
        return {"status": "degraded"}

    # Verify signature
    signature = headers.get("crypto-pay-api-signature", "")
    if not verify_webhook_signature(raw_body, signature):
        logger.warning("CryptoBot webhook: signature verification failed")
        return {"status": "unauthorized"}

    update_type = body.get("update_type")
    if update_type != "invoice_paid":
        logger.info(f"CryptoBot webhook: ignoring update_type={update_type}")
        return {"status": "ignored"}

    payload_obj = body.get("payload", {})
    invoice_id = payload_obj.get("invoice_id")
    status = payload_obj.get("status")

    logger.info(
        f"CryptoBot webhook received: invoice_id={invoice_id}, status={status}"
    )

    if status != "paid":
        logger.info(f"CryptoBot webhook: ignoring status={status}")
        return {"status": "ignored"}

    # Extract purchase_id from invoice payload
    invoice_payload_raw = payload_obj.get("payload")
    purchase_id = None

    if invoice_payload_raw:
        try:
            if isinstance(invoice_payload_raw, str):
                payload_data = json.loads(invoice_payload_raw)
            else:
                payload_data = invoice_payload_raw
            purchase_id = payload_data.get("purchase_id")
        except (json.JSONDecodeError, TypeError):
            pass

    if not purchase_id:
        logger.error(f"CryptoBot webhook: could not extract purchase_id, payload={invoice_payload_raw}")
        return {"status": "invalid"}

    # Look up pending purchase
    pending_purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)

    if not pending_purchase:
        logger.warning(f"CryptoBot webhook: purchase not found: purchase_id={purchase_id}")
        return {"status": "not_found"}

    telegram_id = pending_purchase["telegram_id"]
    purchase_status = pending_purchase.get("status")

    if purchase_status != "pending":
        logger.info(
            f"CryptoBot webhook: purchase already processed: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return {"status": "already_processed"}

    # Get payment amount in RUB
    amount_rubles = float(payload_obj.get("amount", 0))
    if amount_rubles <= 0:
        amount_rubles = pending_purchase["price_kopecks"] / 100.0

    logger.info(
        f"payment_event_received: provider=cryptobot, user={telegram_id}, "
        f"invoice_id={invoice_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB"
    )

    # Finalize purchase
    try:
        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider="cryptobot",
            amount_rubles=amount_rubles,
            invoice_id=str(invoice_id),
        )

        if not result or not result.get("success"):
            logger.error(f"CryptoBot webhook: finalize_purchase failed: {result}")
            raise Exception(f"finalize_purchase returned invalid result: {result}")

        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        is_balance_topup = result.get("is_balance_topup", False)

        from app.services.language_service import resolve_user_language
        from app.i18n import get_text as i18n_get_text

        language = await resolve_user_language(telegram_id)

        if is_balance_topup:
            topup_amount = result.get("amount", amount_rubles)
            text = i18n_get_text(language, "main.balance_topup_success", amount=topup_amount)
            try:
                await bot.send_message(telegram_id, text, parse_mode="HTML")
            except Exception as send_err:
                logger.warning(f"CryptoBot: failed to send topup confirmation to user={telegram_id}: {send_err}")
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
                language, "payment.crypto_success",
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
                logger.warning(f"CryptoBot: failed to send subscription confirmation to user={telegram_id}: {send_err}")

        logger.info(
            f"CryptoBot payment processed: user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}"
        )

    except ValueError as e:
        logger.info(
            f"CryptoBot webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "already_processed"}
    except Exception as e:
        logger.exception(
            f"CryptoBot webhook: finalize_purchase failed: user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "error"}

    return {"status": "ok"}
