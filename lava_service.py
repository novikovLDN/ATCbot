"""
Lava (Card) Integration

Handles card payment creation and webhook processing via Lava API (api.lava.ru).
Configuration resolved via config.py (env-prefixed variables).

Auth: Lava API requires HS256 JWT in Authorization header.
JWT payload: {"apikey": <secret_key>, "tid": <shop_id>}
signed with the secret key from Lava Business panel.

Request format: form-encoded POST (per Lava PHP example using CURLOPT_POSTFIELDS).
"""
import base64
import config
import database
import hashlib
import hmac
import json
import logging
import time
from typing import Optional, Dict, Any
import httpx
from aiogram import Bot
from app.services.payments.confirmation import TransientPaymentError
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py
LAVA_WALLET_TO = config.LAVA_WALLET_TO
LAVA_SECRET_KEY = config.LAVA_JWT_TOKEN  # Secret key for HS256 JWT signing
LAVA_SHOP_ID = config.LAVA_SHOP_ID  # Project/shop ID
LAVA_API_URL = config.LAVA_API_URL


def is_enabled() -> bool:
    """Check if Lava is configured (wallet_to + secret_key + shop_id)."""
    return bool(LAVA_WALLET_TO and LAVA_SECRET_KEY and LAVA_SHOP_ID)


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding (JWT standard)."""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _generate_jwt() -> str:
    """Generate HS256 JWT token for Lava API authorization.

    JWT structure (matching Lava server expectations):
      Header:  {"alg":"HS256","typ":"JWT"}
      Payload: {"apikey": <secret_key>, "tid": <shop_id>}
      Signed with secret_key using HMAC-SHA256.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "apikey": LAVA_SECRET_KEY,
        "uid": LAVA_SHOP_ID,
        "tid": LAVA_SHOP_ID,
    }

    h = _b64url_encode(json.dumps(header, separators=(',', ':')).encode())
    p = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
    signing_input = f"{h}.{p}".encode('utf-8')
    sig = hmac.new(
        LAVA_SECRET_KEY.encode('utf-8'),
        signing_input,
        hashlib.sha256,
    ).digest()
    s = _b64url_encode(sig)
    return f"{h}.{p}.{s}"


def _get_auth_headers() -> Dict[str, str]:
    """Build headers with JWT Authorization (no Content-Type — httpx sets it)."""
    return {
        "Authorization": _generate_jwt(),
        "Accept": "application/json",
    }


async def create_invoice(
    amount_rubles: float,
    purchase_id: str,
    comment: str = "",
    expire: int = 1440,
) -> Dict[str, Any]:
    """Create payment invoice via Lava API.

    Sends form-encoded POST per Lava PHP example (CURLOPT_POSTFIELDS with array).
    """
    if not is_enabled():
        raise Exception("Lava not configured")

    hook_url = ""
    if config.PUBLIC_BASE_URL:
        hook_url = f"{config.PUBLIC_BASE_URL.rstrip('/')}/webhooks/lava"

    form_data = {
        "wallet_to": LAVA_WALLET_TO,
        "sum": str(round(amount_rubles, 2)),
        "order_id": purchase_id,
        "expire": str(expire),
        "comment": (comment[:500] if comment else "Atlas Secure VPN"),
    }
    if hook_url:
        form_data["hook_url"] = hook_url

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{LAVA_API_URL}/invoice/create",
                headers=_get_auth_headers(),
                data=form_data,  # form-encoded, not JSON
            )
            if response.status_code == 200:
                resp_data = response.json()
                if resp_data.get("status") != "success":
                    logger.error(
                        "LAVA_API_ERROR: status=%d body=%s",
                        response.status_code,
                        response.text[:500],
                    )
            if 400 <= response.status_code < 500:
                logger.error(
                    "Lava API client error: status=%d response=%s",
                    response.status_code,
                    response.text[:300],
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

    # Lava docs response: {"status":"success","id":"...","url":"...","expire":...,"sum":"..."}
    # Flat format — no nested "data" key.
    invoice_data = data.get("data", data)
    invoice_id = invoice_data.get("id")
    payment_url = invoice_data.get("url")

    if not invoice_id or not payment_url:
        raise Exception(f"Invalid Lava response: missing id or url. Response: {data}")

    logger.info(
        "Lava invoice created: invoice_id=%s amount=%.2f RUB purchase_id=%s",
        invoice_id, amount_rubles, purchase_id,
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{LAVA_API_URL}/invoice/status",
                headers=_get_auth_headers(),
                data={"id": invoice_id},  # form-encoded
            )
            if response.status_code != 200:
                logger.error("Lava status check failed: status=%d", response.status_code)
                return None
            data = response.json()
            if data.get("status") == "success":
                return data.get("data", data)
    except Exception as e:
        logger.error("Lava status check error: %s", e)
    return None


async def process_webhook_data(headers: dict, body: dict, bot: Bot) -> dict:
    """Process Lava webhook data.

    Lava sends POST to hook_url when payment status changes.
    We verify the payment by checking invoice status via Lava API.
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
        "Lava webhook received: invoice_id=%s order_id=%s status=%s",
        invoice_id, order_id, status,
    )

    # Lava sends status as integer (1 = success) or string
    if str(status) not in ("1", "success"):
        logger.info("Lava webhook: ignoring status=%s", status)
        return {"status": "ignored"}

    # SECURITY: Verify payment by checking invoice status via Lava API
    if invoice_id:
        verified = await check_invoice_status(invoice_id)
        if not verified:
            logger.warning("Lava webhook: could not verify invoice %s", invoice_id)
            return {"status": "unverified"}
        if not order_id:
            order_id = verified.get("order_id")
    else:
        logger.error("Lava webhook: no invoice_id in webhook body")
        return {"status": "invalid"}

    purchase_id = order_id
    if not purchase_id:
        logger.error("Lava webhook: no order_id/purchase_id, invoice=%s", invoice_id)
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
            "Lava webhook: amount missing or zero, using stored price. "
            "purchase_id=%s raw_amount=%s expected=%s",
            purchase_id, raw_amount, expected_amount,
        )
        amount_rubles = expected_amount
    elif abs(raw_amount - expected_amount) > 1.0:
        logger.warning(
            "Lava webhook: amount mismatch. purchase_id=%s "
            "webhook_amount=%s expected=%s",
            purchase_id, raw_amount, expected_amount,
        )
        amount_rubles = raw_amount
    else:
        amount_rubles = raw_amount

    logger.info(
        "payment_event_received: provider=lava user=%s "
        "invoice_id=%s purchase_id=%s amount=%.2f RUB",
        telegram_id, invoice_id, purchase_id, amount_rubles,
    )

    return await process_confirmed_payment(
        provider="lava",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=str(invoice_id),
        telegram_id=telegram_id,
        bot=bot,
    )
