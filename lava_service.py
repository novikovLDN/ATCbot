"""
Lava (Card) Integration

Handles card payment creation and webhook processing via Lava (p2p.lava.ru).
Uses the signature-based method (md5) which requires only wallet + secret key.

Signature: md5("wallet:amount:secretKey:orderId")
Payment URL: https://p2p.lava.ru/create?w=...&ao=...&o=...&s=...
"""
import config
import database
import hashlib
import logging
import time
from typing import Optional, Dict, Any
from urllib.parse import urlencode, quote
import httpx
from aiogram import Bot
from app.services.payments.confirmation import TransientPaymentError
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py
# .strip() protects against accidental whitespace/newlines from copy-paste
LAVA_WALLET_TO = (config.LAVA_WALLET_TO or "").strip()
LAVA_SECRET_KEY = (config.LAVA_JWT_TOKEN or "").strip()
LAVA_SHOP_ID = (config.LAVA_SHOP_ID or "").strip()
LAVA_API_URL = config.LAVA_API_URL
LAVA_P2P_URL = "https://p2p.lava.ru"


def is_enabled() -> bool:
    """Check if Lava is configured (wallet_to + secret_key)."""
    return bool(LAVA_WALLET_TO and LAVA_SECRET_KEY)


logger.info(
    "LAVA_CONFIG: wallet_to='%s' secret_len=%d shop_id='%s' enabled=%s",
    LAVA_WALLET_TO or "EMPTY",
    len(LAVA_SECRET_KEY),
    LAVA_SHOP_ID or "EMPTY",
    is_enabled(),
)


def _make_signature(wallet: str, amount: str, secret_key: str, order_id: str) -> str:
    """Generate md5 signature per Lava docs.

    Signature = md5("wallet:amount:secretKey:orderId")
    Example: md5("R10000138:100.00:b1DhCLv2IwgSdoJ5qWmfIb96xaBlUaM5:114533")
    """
    raw = f"{wallet}:{amount}:{secret_key}:{order_id}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


async def create_invoice(
    amount_rubles: float,
    purchase_id: str,
    comment: str = "",
    expire: int = 1440,
) -> Dict[str, Any]:
    """Create payment link via Lava p2p (signature-based method).

    Constructs a signed payment URL and verifies it's accessible.
    Returns payment_url for user redirect.
    """
    if not is_enabled():
        raise Exception("Lava not configured")

    amount_str = f"{amount_rubles:.2f}"

    signature = _make_signature(LAVA_WALLET_TO, amount_str, LAVA_SECRET_KEY, purchase_id)

    hook_url = ""
    if config.PUBLIC_BASE_URL:
        hook_url = f"{config.PUBLIC_BASE_URL.rstrip('/')}/webhooks/lava"

    params = {
        "w": LAVA_WALLET_TO,
        "ao": amount_str,
        "o": purchase_id,
        "s": signature,
        "exp": str(expire),
        "c": (comment[:500] if comment else "Atlas Secure VPN"),
    }
    if hook_url:
        params["hook_url"] = hook_url

    payment_url = f"{LAVA_P2P_URL}/create?{urlencode(params)}"

    # Verify the URL is accessible (HEAD request)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.head(payment_url)
            # Lava should return 200 or 302 (redirect to payment form)
            if response.status_code >= 400:
                logger.error(
                    "Lava payment URL check failed: status=%d url=%s",
                    response.status_code, payment_url[:100],
                )
    except Exception as e:
        logger.warning("Lava payment URL check error (non-fatal): %s", e)

    # Use purchase_id as invoice_id (no separate invoice in p2p method)
    invoice_id = f"lava_{purchase_id}_{int(time.time())}"

    logger.info(
        "Lava payment link created: invoice_id=%s amount=%s RUB purchase_id=%s",
        invoice_id, amount_str, purchase_id,
    )

    return {
        "invoice_id": invoice_id,
        "payment_url": payment_url,
    }


async def check_invoice_status(invoice_id: str) -> Optional[Dict[str, Any]]:
    """Check invoice status via Lava API (if available)."""
    # P2P method doesn't have a status API — rely on webhooks
    return None


async def process_webhook_data(headers: dict, body: dict, bot: Bot) -> dict:
    """Process Lava webhook data.

    Lava sends POST to hook_url when payment status changes.
    """
    if not database.DB_READY:
        logger.warning("Lava webhook: DB not ready — returning 500 for retry")
        raise TransientPaymentError("DB not ready")

    if not is_enabled():
        logger.error("Lava webhook: service not configured")
        return {"status": "disabled"}

    # Extract data from webhook body
    invoice_id = body.get("invoice_id") or body.get("id")
    order_id = body.get("order_id") or body.get("o")
    status = body.get("status")
    amount = body.get("amount") or body.get("sum") or body.get("ao")

    logger.info(
        "Lava webhook received: invoice_id=%s order_id=%s status=%s body_keys=%s",
        invoice_id, order_id, status, list(body.keys()),
    )

    # Lava sends status as integer (1 = success) or string
    if str(status) not in ("1", "success"):
        logger.info("Lava webhook: ignoring status=%s", status)
        return {"status": "ignored"}

    # Verify webhook signature if present
    webhook_sig = body.get("s") or body.get("sig") or body.get("signature")
    if webhook_sig and order_id and amount:
        expected_sig = _make_signature(
            LAVA_WALLET_TO, f"{float(amount):.2f}", LAVA_SECRET_KEY, order_id
        )
        if webhook_sig != expected_sig:
            logger.warning(
                "Lava webhook: signature mismatch. order_id=%s", order_id,
            )
            return {"status": "invalid_signature"}

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
        invoice_id=str(invoice_id or purchase_id),
        telegram_id=telegram_id,
        bot=bot,
    )
