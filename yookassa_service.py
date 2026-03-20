"""
YooKassa Direct API Integration for Recurring Payments

Handles:
- Creating payments with save_payment_method (conditional saving)
- Executing autopayments using saved payment method IDs
- Processing YooKassa webhook notifications
- Managing saved payment methods

Configuration: YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY via config.py.
"""
import asyncio
import json
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any
from uuid import uuid4

import httpx
import config
import database

logger = logging.getLogger(__name__)

# YooKassa API base URL
YOOKASSA_API_URL = "https://api.yookassa.ru/v3"


def is_enabled() -> bool:
    """Check if YooKassa direct API is configured."""
    return config.YOOKASSA_ENABLED


def _get_auth() -> tuple:
    """Get HTTP Basic auth tuple for YooKassa API."""
    return (config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET_KEY)


async def create_payment(
    amount_rubles: float,
    description: str,
    purchase_id: str,
    telegram_id: int,
    return_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a YooKassa payment.

    Args:
        amount_rubles: Payment amount in rubles
        description: Payment description
        purchase_id: Internal purchase ID (stored in metadata)
        telegram_id: User's Telegram ID
        return_url: URL to redirect user after payment

    Returns:
        {"payment_id": str, "confirmation_url": str, "status": str}

    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise RuntimeError("YooKassa direct API is not configured")

    idempotence_key = str(uuid4())

    body = {
        "amount": {
            "value": f"{amount_rubles:.2f}",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": return_url or config.YOOKASSA_RETURN_URL or "https://t.me/atlassecure_bot",
        },
        "capture": True,
        "description": description[:128] if description else "Atlas Secure VPN",
        "metadata": {
            "purchase_id": purchase_id,
            "telegram_id": str(telegram_id),
        },
        "merchant_customer_id": str(telegram_id),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{YOOKASSA_API_URL}/payments",
            json=body,
            auth=_get_auth(),
            headers={
                "Idempotence-Key": idempotence_key,
                "Content-Type": "application/json",
            },
        )

        if response.status_code not in (200, 201):
            logger.error(
                f"YooKassa create payment failed: status={response.status_code}, "
                f"response={response.text[:500]}"
            )
            raise RuntimeError(f"YooKassa API error: {response.status_code}")

        data = response.json()

    payment_id = data.get("id")
    confirmation = data.get("confirmation", {})
    confirmation_url = confirmation.get("confirmation_url")
    status = data.get("status")

    logger.info(
        f"YooKassa payment created: payment_id={payment_id}, status={status}, "
        f"purchase_id={purchase_id}, telegram_id={telegram_id}"
    )

    return {
        "payment_id": payment_id,
        "confirmation_url": confirmation_url,
        "status": status,
    }


async def get_payment_info(payment_id: str) -> Dict[str, Any]:
    """
    Get payment details from YooKassa API.

    Args:
        payment_id: YooKassa payment ID

    Returns:
        Full payment object from YooKassa API
    """
    if not is_enabled():
        raise RuntimeError("YooKassa direct API is not configured")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{YOOKASSA_API_URL}/payments/{payment_id}",
            auth=_get_auth(),
        )

        if response.status_code != 200:
            raise RuntimeError(f"YooKassa get payment error: {response.status_code}")

        return response.json()


import ipaddress

# YooKassa webhook IP allowlist (as of 2025)
_YOOKASSA_IP_NETWORKS = [
    ipaddress.ip_network("185.71.76.0/27"),
    ipaddress.ip_network("185.71.77.0/27"),
    ipaddress.ip_network("77.75.153.0/25"),
    ipaddress.ip_network("77.75.156.11/32"),
    ipaddress.ip_network("77.75.156.35/32"),
    ipaddress.ip_network("77.75.154.128/25"),
    ipaddress.ip_network("2a02:5180::/32"),
]


def verify_webhook_ip(client_ip: str) -> bool:
    """
    Verify that webhook request comes from YooKassa IP ranges.

    Returns True if the IP is in the YooKassa allowlist.
    Falls back to True if IP cannot be parsed (e.g. behind proxy without X-Forwarded-For),
    since we also verify payments by re-fetching from YooKassa API.
    """
    if not client_ip:
        logger.warning("YooKassa webhook: no client IP provided, allowing (API re-fetch protects)")
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
        for net in _YOOKASSA_IP_NETWORKS:
            if addr in net:
                return True
        logger.warning("YooKassa webhook: IP %s not in allowlist", client_ip)
        return False
    except ValueError:
        logger.warning("YooKassa webhook: invalid IP format: %s", client_ip)
        return True  # Allow — API re-fetch is the primary verification


async def process_webhook(body: dict, bot) -> dict:
    """
    Process YooKassa webhook notification.

    YooKassa sends notifications about payment status changes.
    We verify by fetching the payment from API (not trusting the body directly).

    Args:
        body: Parsed JSON body from webhook
        bot: Bot instance for sending messages

    Returns:
        Response dict with "status" key
    """
    if not database.DB_READY:
        logger.warning("YooKassa webhook: DB not ready")
        return {"status": "degraded"}

    event_type = body.get("event")
    payment_object = body.get("object", {})
    payment_id = payment_object.get("id")

    logger.info(
        f"YooKassa webhook received: event={event_type}, payment_id={payment_id}"
    )

    if event_type not in ("payment.succeeded", "payment.waiting_for_capture"):
        logger.info(f"YooKassa webhook: ignoring event={event_type}")
        return {"status": "ignored"}

    if not payment_id:
        logger.warning("YooKassa webhook: missing payment_id")
        return {"status": "invalid"}

    # Verify payment by fetching from API (don't trust webhook body)
    try:
        verified_payment = await get_payment_info(payment_id)
    except Exception as e:
        logger.error(f"YooKassa webhook: failed to verify payment {payment_id}: {e}")
        return {"status": "error"}

    status = verified_payment.get("status")
    if status != "succeeded":
        logger.info(f"YooKassa webhook: payment {payment_id} status={status}, not succeeded")
        return {"status": "ignored"}

    # Extract metadata
    metadata = verified_payment.get("metadata", {})
    purchase_id = metadata.get("purchase_id")
    telegram_id_str = metadata.get("telegram_id")

    if not purchase_id or not telegram_id_str:
        # This might be an autopayment (no purchase_id in metadata)
        # Autopayments are handled synchronously in auto_renewal, not via webhook
        logger.info(
            f"YooKassa webhook: no purchase_id/telegram_id in metadata, "
            f"payment_id={payment_id} (likely autopayment)"
        )
        return {"status": "ok"}

    try:
        telegram_id = int(telegram_id_str)
    except (ValueError, TypeError):
        logger.error(f"YooKassa webhook: invalid telegram_id={telegram_id_str}")
        return {"status": "invalid"}

    # Process payment through shared confirmation logic
    amount_obj = verified_payment.get("amount", {})
    amount_rubles = float(amount_obj.get("value", 0))

    from app.services.payments.confirmation import (
        lookup_pending_purchase,
        process_confirmed_payment,
    )

    lookup = await lookup_pending_purchase("yookassa", purchase_id)
    if lookup["status"] != "ok":
        return lookup

    return await process_confirmed_payment(
        provider="yookassa",
        purchase_id=purchase_id,
        amount_rubles=amount_rubles,
        invoice_id=payment_id,
        telegram_id=telegram_id,
        bot=bot,
    )


