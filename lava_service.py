"""
Lava (Card) Integration

Handles card payment creation and webhook processing via Lava API (api.lava.ru).
Configuration: wallet_to/jwt_token/API URL resolved via config.py only.
"""
import config
import database
import hashlib
import hmac
import json
import logging
from typing import Optional, Dict, Any
import httpx
from aiogram import Bot
from app.services.payments.confirmation import TransientPaymentError
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py
LAVA_WALLET_TO = config.LAVA_WALLET_TO
LAVA_SECRET_KEY = config.LAVA_JWT_TOKEN  # Secret key for HMAC-SHA256 signing
LAVA_API_URL = config.LAVA_API_URL


def is_enabled() -> bool:
    """Check if Lava is configured (wallet_to + secret_key)."""
    return bool(LAVA_WALLET_TO and LAVA_SECRET_KEY)


def _sign_request(body: dict) -> str:
    """Generate HMAC-SHA256 signature of the JSON request body."""
    body_json = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hmac.new(
        LAVA_SECRET_KEY.encode('utf-8'),
        body_json.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _get_headers(body: dict) -> Dict[str, str]:
    """Get authentication headers for Lava API with HMAC-SHA256 signature."""
    signature = _sign_request(body)
    return {
        "Signature": signature,
        "Authorization": signature,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def create_invoice(
    amount_rubles: float,
    purchase_id: str,
    comment: str = "",
    expire: int = 1440,
) -> Dict[str, Any]:
    """
    Create payment invoice via Lava API.

    Args:
        amount_rubles: Payment amount in rubles
        purchase_id: Internal purchase ID (stored as order_id)
        comment: Payment comment
        expire: Invoice expiration in minutes (default 1440 = 24h)

    Returns:
        {"invoice_id": str, "payment_url": str}

    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("Lava not configured")

    hook_url = ""
    if config.PUBLIC_BASE_URL:
        hook_url = f"{config.PUBLIC_BASE_URL.rstrip('/')}/webhooks/lava"

    request_body = {
        "wallet_to": LAVA_WALLET_TO,
        "sum": round(amount_rubles, 2),
        "order_id": purchase_id,
        "expire": expire,
        "comment": comment[:500] if comment else "Atlas Secure VPN",
    }
    if hook_url:
        request_body["hook_url"] = hook_url

    async def _make_request():
        headers = _get_headers(request_body)
        logger.info(
            "LAVA_DEBUG: url=%s, auth_header_len=%d, sig_header_len=%d, "
            "wallet_to=%s, secret_key_prefix=%s, body_keys=%s",
            f"{LAVA_API_URL}/invoice/create",
            len(headers.get("Authorization", "")),
            len(headers.get("Signature", "")),
            LAVA_WALLET_TO[:10] if LAVA_WALLET_TO else "EMPTY",
            LAVA_SECRET_KEY[:8] + "..." if LAVA_SECRET_KEY else "EMPTY",
            list(request_body.keys()),
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{LAVA_API_URL}/invoice/create",
                headers=headers,
                json=request_body,
            )
            logger.info("LAVA_DEBUG_RESPONSE: status=%d, body=%s", response.status_code, response.text[:500])
            if 400 <= response.status_code < 500:
                logger.error(
                    f"Lava API client error: status={response.status_code}, "
                    f"response={response.text[:300]}"
                )
                raise Exception(f"Lava API error: {response.status_code}")
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

    if data.get("status") != "success":
        raise Exception(
            f"Lava API error: {data.get('message', 'unknown error')}, code={data.get('code')}"
        )

    # Handle both flat and nested response formats
    invoice_data = data.get("data", data)
    invoice_id = invoice_data.get("id")
    payment_url = invoice_data.get("url")

    if not invoice_id or not payment_url:
        raise Exception(f"Invalid Lava response: missing id or url. Response: {data}")

    logger.info(
        f"Lava invoice created: invoice_id={invoice_id}, "
        f"amount={amount_rubles} RUB, purchase_id={purchase_id}"
    )

    return {
        "invoice_id": invoice_id,
        "payment_url": payment_url,
    }


async def check_invoice_status(invoice_id: str) -> Optional[Dict[str, Any]]:
    """Check invoice status via Lava API for payment verification."""
    if not is_enabled():
        return None

    try:
        status_body = {"id": invoice_id}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{LAVA_API_URL}/invoice/status",
                headers=_get_headers(status_body),
                json=status_body,
            )
            if response.status_code != 200:
                logger.error(f"Lava status check failed: status={response.status_code}")
                return None
            data = response.json()
            if data.get("status") == "success":
                return data.get("data", data)
    except Exception as e:
        logger.error(f"Lava status check error: {e}")
    return None


async def process_webhook_data(headers: dict, body: dict, bot: Bot) -> dict:
    """
    Process Lava webhook data (framework-agnostic).

    Lava sends POST to hook_url when payment status changes.
    We verify the payment by checking invoice status via Lava API.

    Args:
        headers: Request headers dict
        body: Parsed JSON body
        bot: Bot instance for sending messages

    Returns:
        Response dict with "status" key
    """
    if not database.DB_READY:
        logger.warning("Lava webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    if not is_enabled():
        logger.error("Lava webhook: service not configured")
        return {"status": "disabled"}

    # Extract data from webhook body
    invoice_id = body.get("invoice_id") or body.get("id")
    order_id = body.get("order_id")
    status = body.get("status")
    amount = body.get("amount") or body.get("sum")

    logger.info(
        f"Lava webhook received: invoice_id={invoice_id}, "
        f"order_id={order_id}, status={status}"
    )

    # Lava sends status as integer (1 = success) or string
    if str(status) not in ("1", "success"):
        logger.info(f"Lava webhook: ignoring status={status}")
        return {"status": "ignored"}

    # SECURITY: Verify payment by checking invoice status via Lava API
    if invoice_id:
        verified = await check_invoice_status(invoice_id)
        if not verified:
            logger.warning(f"Lava webhook: could not verify invoice {invoice_id}")
            return {"status": "unverified"}
        # Use verified order_id if not present in webhook body
        if not order_id:
            order_id = verified.get("order_id")
    else:
        logger.error("Lava webhook: no invoice_id in webhook body")
        return {"status": "invalid"}

    purchase_id = order_id
    if not purchase_id:
        logger.error(
            f"Lava webhook: no order_id/purchase_id, invoice={invoice_id}"
        )
        return {"status": "invalid"}

    # Delegate to shared confirmation logic
    from app.services.payments.confirmation import (
        lookup_pending_purchase, process_confirmed_payment,
    )

    lookup = await lookup_pending_purchase("lava", purchase_id)
    if lookup["status"] != "ok":
        return lookup

    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]

    # Get payment amount
    raw_amount = float(amount) if amount else 0.0
    expected_amount = pending_purchase["price_kopecks"] / 100.0
    if raw_amount <= 0:
        logger.warning(
            f"Lava webhook: amount missing or zero, using stored price. "
            f"purchase_id={purchase_id}, raw_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            f"Lava webhook: amount mismatch. purchase_id={purchase_id}, "
            f"webhook_amount={raw_amount}, expected={expected_amount}"
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        f"payment_event_received: provider=lava, user={telegram_id}, "
        f"invoice_id={invoice_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB"
    )

    return await process_confirmed_payment(
        provider="lava",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=str(invoice_id),
        telegram_id=telegram_id,
        bot=bot,
    )
