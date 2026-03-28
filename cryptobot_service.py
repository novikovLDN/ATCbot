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
from app.services.payments.confirmation import TransientPaymentError
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
        logger.warning("CryptoBot webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    # Verify signature
    signature = headers.get("crypto-pay-api-signature", "")
    if not signature:
        logger.warning("CryptoBot webhook: missing signature header")
        return {"status": "unauthorized"}
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

    # Delegate to shared confirmation logic
    from app.services.payments.confirmation import (
        extract_purchase_id, lookup_pending_purchase, process_confirmed_payment,
    )

    invoice_payload_raw = payload_obj.get("payload")
    purchase_id = extract_purchase_id(invoice_payload_raw)

    if not purchase_id:
        logger.error(f"CryptoBot webhook: could not extract purchase_id, payload={invoice_payload_raw}")
        return {"status": "invalid"}

    lookup = await lookup_pending_purchase("cryptobot", purchase_id)
    if lookup["status"] != "ok":
        return lookup

    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]

    # Get payment amount in RUB
    raw_amount = float(payload_obj.get("amount", 0))
    expected_amount = pending_purchase["price_kopecks"] / 100.0
    if raw_amount <= 0:
        logger.warning(
            f"CryptoBot webhook: amount missing or zero, using stored price. "
            f"purchase_id={purchase_id}, raw_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            f"CryptoBot webhook: amount mismatch. purchase_id={purchase_id}, "
            f"webhook_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        f"payment_event_received: provider=cryptobot, user={telegram_id}, "
        f"invoice_id={invoice_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB"
    )

    return await process_confirmed_payment(
        provider="cryptobot",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=str(invoice_id),
        telegram_id=telegram_id,
        bot=bot,
    )
