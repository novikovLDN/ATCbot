"""
Platega.io (SBP) Integration

Handles SBP payment creation and webhook processing.
Configuration: merchant_id/secret/API URL resolved via config.py only.
"""
import config
import database
import hmac
import json
import logging
import math
from typing import Optional, Dict, Any
from uuid import uuid4
import httpx
from aiogram import Bot
from app.services.payments.confirmation import TransientPaymentError
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py
PLATEGA_MERCHANT_ID = config.PLATEGA_MERCHANT_ID
PLATEGA_SECRET = config.PLATEGA_SECRET
PLATEGA_API_URL = config.PLATEGA_API_URL


def is_enabled() -> bool:
    """Check if Platega is configured (merchant_id + secret)."""
    return bool(PLATEGA_MERCHANT_ID and PLATEGA_SECRET)


def _get_headers() -> Dict[str, str]:
    """Get authentication headers for Platega API."""
    return {
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_SECRET,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def apply_sbp_markup(price_kopecks: int) -> int:
    """Apply SBP markup (+11%) to price in kopecks. Returns new price rounded up."""
    markup = config.SBP_MARKUP_PERCENT / 100.0
    return math.ceil(price_kopecks * (1 + markup))


# Payment method constants (from Platega API docs)
PAYMENT_METHOD_SBP = 2
PAYMENT_METHOD_CARD_RU = 11
PAYMENT_METHOD_INTERNATIONAL = 12
PAYMENT_METHOD_CRYPTO = 13


async def create_transaction(
    amount_rubles: float,
    description: str,
    purchase_id: str,
    payment_method: int = PAYMENT_METHOD_SBP,
    return_url: Optional[str] = None,
    failed_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create payment transaction via Platega API.

    Args:
        amount_rubles: Payment amount in rubles (already with markup applied)
        description: Payment description
        purchase_id: Internal purchase ID (stored in payload)
        payment_method: Platega payment method (2=SBP, 11=Card RU, 12=International, 13=Crypto)
        return_url: Redirect URL after successful payment
        failed_url: Redirect URL after failed payment

    Returns:
        {"transaction_id": str, "redirect_url": str}

    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("Platega not configured")

    request_body = {
        "paymentMethod": payment_method,
        "paymentDetails": {
            "amount": round(amount_rubles, 2),
            "currency": "RUB",
        },
        "description": description[:250] if description else "Atlas Secure VPN",
        "payload": json.dumps({"purchase_id": purchase_id}),
    }
    if return_url:
        request_body["return"] = return_url
    if failed_url:
        request_body["failedUrl"] = failed_url

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PLATEGA_API_URL}/transaction/process",
                headers=_get_headers(),
                json=request_body,
            )
            if 400 <= response.status_code < 500:
                logger.error(
                    f"Platega API client error: status={response.status_code}, "
                    f"response={response.text[:300]}"
                )
                raise Exception(f"Platega API error: {response.status_code}")
            if response.status_code != 200:
                response.raise_for_status()
            return response

    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError),
    )

    data = response.json()
    transaction_id = data.get("transactionId")
    redirect_url = data.get("redirect")

    if not transaction_id or not redirect_url:
        raise Exception(f"Invalid Platega response: missing transactionId or redirect. Response: {data}")

    logger.info(
        f"Platega transaction created: transaction_id={transaction_id}, "
        f"amount={amount_rubles} RUB, purchase_id={purchase_id}"
    )

    return {
        "transaction_id": transaction_id,
        "redirect_url": redirect_url,
    }


async def process_webhook_data(headers: dict, body: dict, bot: Bot) -> dict:
    """
    Process Platega webhook data (framework-agnostic).

    Args:
        headers: Request headers dict
        body: Parsed JSON body
        bot: Bot instance for sending messages

    Returns:
        Response dict with "status" key
    """
    if not database.DB_READY:
        logger.warning("Platega webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    # Verify authentication headers (case-insensitive lookup)
    merchant_id = headers.get("x-merchantid", "") or headers.get("X-MerchantId", "")
    secret = headers.get("x-secret", "") or headers.get("X-Secret", "")

    # SECURITY: Reject if server-side credentials are not configured (prevents empty-string bypass)
    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        logger.error("Platega webhook: server credentials not configured")
        return {"status": "unauthorized"}

    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or not hmac.compare_digest(str(secret), str(PLATEGA_SECRET)):
        logger.warning("Platega webhook: auth failed")
        return {"status": "unauthorized"}

    transaction_id = body.get("id") or body.get("transactionId")
    status = (body.get("status") or "").lower()

    logger.info(
        f"Platega webhook received: transaction_id={transaction_id}, status={status}"
    )

    # Only process confirmed/completed payments
    if status not in ("confirmed", "completed", "paid"):
        logger.info(f"Platega webhook: ignoring status={status}")
        return {"status": "ignored"}

    # Delegate to shared confirmation logic
    from app.services.payments.confirmation import (
        extract_purchase_id, lookup_pending_purchase, process_confirmed_payment,
    )

    payload_raw = body.get("payload")
    purchase_id = extract_purchase_id(payload_raw)

    if not purchase_id:
        logger.error(f"Platega webhook: could not extract purchase_id, payload={payload_raw}")
        return {"status": "invalid"}

    lookup = await lookup_pending_purchase("platega", purchase_id)
    if lookup["status"] != "ok":
        return lookup

    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]

    # Get payment amount
    payment_details = body.get("paymentDetails", {})
    raw_amount = float(payment_details.get("amount", 0))
    expected_amount = pending_purchase["price_kopecks"] / 100.0
    if raw_amount <= 0:
        logger.warning(
            f"Platega webhook: amount missing or zero, using stored price. "
            f"purchase_id={purchase_id}, raw_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            f"Platega webhook: amount mismatch. purchase_id={purchase_id}, "
            f"webhook_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        f"payment_event_received: provider=platega, user={telegram_id}, "
        f"transaction_id={transaction_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB"
    )

    return await process_confirmed_payment(
        provider="platega",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=str(transaction_id),
        telegram_id=telegram_id,
        bot=bot,
    )


