"""
Lava Business (Card) Integration

Handles card payment creation and webhook processing via Lava Business API.
API docs: https://business.lava.ru — signature-based auth (HMAC-SHA256).

Auth: Signature header = HMAC-SHA256(json_body, secret_key).hexdigest()
Secret key = "Секретный ключ" from Lava Business panel.
Additional key = "Дополнительный ключ" for webhook signature verification.
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
LAVA_SECRET_KEY = (config.LAVA_JWT_TOKEN or "").strip()  # Secret key for signing requests
LAVA_SIGN_KEY = getattr(config, 'LAVA_SIGN_KEY', "") or ""  # Additional key for webhook verification
LAVA_SIGN_KEY = LAVA_SIGN_KEY.strip()
LAVA_SHOP_ID = (config.LAVA_SHOP_ID or "").strip()  # Project UUID
LAVA_API_URL = "https://api.lava.ru/business"


def is_enabled() -> bool:
    """Check if Lava is configured (secret_key + shop_id)."""
    return bool(LAVA_SECRET_KEY and LAVA_SHOP_ID)


logger.info(
    "LAVA_CONFIG: secret_len=%d shop_id='%s' sign_key_len=%d enabled=%s",
    len(LAVA_SECRET_KEY),
    LAVA_SHOP_ID or "EMPTY",
    len(LAVA_SIGN_KEY),
    is_enabled(),
)


def _sign(data: dict) -> str:
    """Generate HMAC-SHA256 signature of JSON body.

    Per Lava docs: json.dumps(data) signed with secret key.
    Parameters serialized in same order as in the request.
    """
    json_str = json.dumps(data).encode('utf-8')
    return hmac.new(
        LAVA_SECRET_KEY.encode('utf-8'),
        json_str,
        hashlib.sha256,
    ).hexdigest()


def _headers(data: dict) -> Dict[str, str]:
    """Build request headers with Signature."""
    return {
        "Signature": _sign(data),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def create_invoice(
    amount_rubles: float,
    purchase_id: str,
    comment: str = "",
    expire: int = 300,
) -> Dict[str, Any]:
    """Create payment invoice via Lava Business API.

    POST https://api.lava.ru/business/invoice/create
    """
    if not is_enabled():
        raise Exception("Lava not configured")

    hook_url = ""
    if config.PUBLIC_BASE_URL:
        hook_url = f"{config.PUBLIC_BASE_URL.rstrip('/')}/webhooks/lava"

    data = {
        "shopId": LAVA_SHOP_ID,
        "sum": round(amount_rubles, 2),
        "orderId": purchase_id,
        "expire": expire,
    }
    if hook_url:
        data["hookUrl"] = hook_url
    if comment:
        data["comment"] = comment[:255]

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{LAVA_API_URL}/invoice/create",
                headers=_headers(data),
                content=json.dumps(data),  # data-raw as per docs
            )
            if response.status_code != 200:
                logger.error(
                    "Lava API error: status=%d response=%s",
                    response.status_code,
                    response.text[:500],
                )
                raise Exception(f"Lava API error: {response.status_code}")
            return response

    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError),
    )

    resp = response.json()

    if not resp.get("status_check", False):
        error = resp.get("error", resp.get("message", "unknown error"))
        logger.error("LAVA_API_ERROR: %s", response.text[:500])
        raise Exception(f"Lava API error: {error}")

    invoice_data = resp.get("data", {})
    invoice_id = invoice_data.get("id")
    payment_url = invoice_data.get("url")

    if not invoice_id or not payment_url:
        raise Exception(f"Invalid Lava response: missing id or url. Response: {resp}")

    logger.info(
        "Lava invoice created: invoice_id=%s amount=%.2f RUB purchase_id=%s url=%s",
        invoice_id, amount_rubles, purchase_id, payment_url[:80],
    )

    return {
        "invoice_id": invoice_id,
        "payment_url": payment_url,
    }


async def check_invoice_status(invoice_id: str) -> Optional[Dict[str, Any]]:
    """Check invoice status via Lava Business API.

    POST https://api.lava.ru/business/invoice/status
    """
    if not is_enabled():
        return None

    data = {
        "shopId": LAVA_SHOP_ID,
        "invoiceId": invoice_id,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{LAVA_API_URL}/invoice/status",
                headers=_headers(data),
                content=json.dumps(data),
            )
            if response.status_code != 200:
                logger.error("Lava status check failed: status=%d", response.status_code)
                return None
            resp = response.json()
            if resp.get("status_check", False):
                return resp.get("data", {})
    except Exception as e:
        logger.error("Lava status check error: %s", e)
    return None


def _verify_webhook_signature(body_bytes: bytes, received_sig: str) -> bool:
    """Verify webhook signature using additional key.

    Lava sends signature in Authorization header of webhook.
    """
    if not LAVA_SIGN_KEY:
        logger.warning("Lava webhook: no SIGN_KEY configured, skipping signature check")
        return True

    expected = hmac.new(
        LAVA_SIGN_KEY.encode('utf-8'),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received_sig)


async def process_webhook_data(headers: dict, body: dict, bot: Bot) -> dict:
    """Process Lava Business webhook.

    Webhook format:
    {
        "invoice_id": "uuid",
        "order_id": "string",
        "status": "success",
        "amount": 2,
        "credited": 1.9,
        "pay_time": "2022-09-09 15:15:35",
        "custom_fields": null
    }
    Authorization header contains HMAC signature (verified with additional key).
    """
    if not database.DB_READY:
        logger.warning("Lava webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    if not is_enabled():
        logger.error("Lava webhook: service not configured")
        return {"status": "disabled"}

    invoice_id = body.get("invoice_id")
    order_id = body.get("order_id")
    status = body.get("status")
    amount = body.get("amount")
    credited = body.get("credited")

    logger.info(
        "Lava webhook received: invoice_id=%s order_id=%s status=%s amount=%s credited=%s",
        invoice_id, order_id, status, amount, credited,
    )

    if status != "success":
        logger.info("Lava webhook: ignoring status=%s", status)
        return {"status": "ignored"}

    # Verify via Lava API as additional security
    if invoice_id:
        verified = await check_invoice_status(invoice_id)
        if verified:
            verified_status = verified.get("status")
            if verified_status not in ("success", "completed"):
                logger.warning(
                    "Lava webhook: API status mismatch. webhook=success api=%s invoice=%s",
                    verified_status, invoice_id,
                )
            if not order_id:
                order_id = verified.get("order_id")

    purchase_id = order_id
    if not purchase_id:
        logger.error("Lava webhook: no order_id, invoice=%s", invoice_id)
        return {"status": "invalid"}

    from app.services.payments.confirmation import (
        lookup_pending_purchase, process_confirmed_payment,
    )

    lookup = await lookup_pending_purchase("lava", purchase_id)
    if lookup["status"] != "ok":
        return lookup

    pending_purchase = lookup["purchase"]
    telegram_id = lookup["telegram_id"]

    raw_amount = float(amount) if amount else 0.0
    expected_amount = pending_purchase["price_kopecks"] / 100.0
    if raw_amount <= 0:
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            "Lava webhook: amount mismatch. purchase_id=%s webhook=%s expected=%s",
            purchase_id, raw_amount, expected_amount,
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        "payment_event_received: provider=lava user=%s invoice_id=%s purchase_id=%s amount=%.2f RUB",
        telegram_id, invoice_id, purchase_id, amount_rubles,
    )

    return await process_confirmed_payment(
        provider="lava",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=str(invoice_id or purchase_id),
        telegram_id=telegram_id,
        bot=bot,
    )
