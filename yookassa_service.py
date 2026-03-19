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


async def create_payment_with_save(
    amount_rubles: float,
    description: str,
    purchase_id: str,
    telegram_id: int,
    return_url: Optional[str] = None,
    save_payment_method: bool = True,
) -> Dict[str, Any]:
    """
    Create a YooKassa payment with optional card saving for recurring charges.

    Args:
        amount_rubles: Payment amount in rubles
        description: Payment description
        purchase_id: Internal purchase ID (stored in metadata)
        telegram_id: User's Telegram ID
        return_url: URL to redirect user after payment
        save_payment_method: Whether to save the payment method

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
        "save_payment_method": save_payment_method,
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
        f"purchase_id={purchase_id}, telegram_id={telegram_id}, "
        f"save_payment_method={save_payment_method}"
    )

    return {
        "payment_id": payment_id,
        "confirmation_url": confirmation_url,
        "status": status,
    }


async def create_autopayment(
    amount_rubles: float,
    payment_method_id: str,
    description: str,
    telegram_id: int,
    metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Create an autopayment using a saved payment method (no user confirmation needed).

    Args:
        amount_rubles: Payment amount in rubles
        payment_method_id: Saved YooKassa payment method ID
        description: Payment description
        telegram_id: User's Telegram ID
        metadata: Optional metadata dict

    Returns:
        {"payment_id": str, "status": str, "paid": bool}

    Raises:
        RuntimeError on API or payment errors
    """
    if not is_enabled():
        raise RuntimeError("YooKassa direct API is not configured")

    # Derive idempotence key from payment parameters to prevent double-charge on retry
    idem_source = f"autopay:{telegram_id}:{payment_method_id}:{amount_rubles:.2f}:{description or ''}"
    idempotence_key = hashlib.sha256(idem_source.encode()).hexdigest()[:48]

    body = {
        "amount": {
            "value": f"{amount_rubles:.2f}",
            "currency": "RUB",
        },
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": description[:128] if description else "Atlas Secure VPN — автопродление",
        "metadata": metadata or {"telegram_id": str(telegram_id)},
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
            error_text = response.text[:500]
            logger.error(
                f"YooKassa autopayment failed: status={response.status_code}, "
                f"response={error_text}, telegram_id={telegram_id}"
            )
            raise RuntimeError(f"YooKassa autopayment error: {response.status_code}: {error_text}")

        data = response.json()

    payment_id = data.get("id")
    status = data.get("status")
    paid = data.get("paid", False)

    # Check for cancellation
    cancellation = data.get("cancellation_details")
    if cancellation:
        reason = cancellation.get("reason", "unknown")
        party = cancellation.get("party", "unknown")
        logger.warning(
            f"YooKassa autopayment cancelled: payment_id={payment_id}, "
            f"reason={reason}, party={party}, telegram_id={telegram_id}"
        )
        raise RuntimeError(f"Autopayment cancelled: {reason} (party: {party})")

    if status == "canceled":
        logger.warning(
            f"YooKassa autopayment status=canceled: payment_id={payment_id}, "
            f"telegram_id={telegram_id}"
        )
        raise RuntimeError("Autopayment was canceled by YooKassa")

    logger.info(
        f"YooKassa autopayment result: payment_id={payment_id}, status={status}, "
        f"paid={paid}, telegram_id={telegram_id}, amount={amount_rubles}"
    )

    return {
        "payment_id": payment_id,
        "status": status,
        "paid": paid,
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

    # Save payment method if available
    payment_method = verified_payment.get("payment_method", {})
    pm_saved = payment_method.get("saved", False)
    pm_id = payment_method.get("id")
    pm_title = payment_method.get("title")

    if pm_saved and pm_id:
        try:
            await save_user_payment_method(telegram_id, pm_id, pm_title)
            logger.info(
                f"YooKassa: saved payment method for user={telegram_id}, "
                f"pm_id={pm_id[:8]}..., title={pm_title}"
            )
        except Exception as e:
            logger.error(
                f"YooKassa: failed to save payment method for user={telegram_id}: {e}"
            )

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


async def save_user_payment_method(
    telegram_id: int,
    payment_method_id: str,
    title: Optional[str] = None,
) -> bool:
    """
    Save YooKassa payment method ID for a user (enables card auto-renewal).

    Args:
        telegram_id: User's Telegram ID
        payment_method_id: YooKassa payment method ID
        title: Card title/mask (e.g. "Visa •••• 4242")

    Returns:
        True if saved successfully
    """
    pool = await database.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE subscriptions
               SET saved_payment_method_id = $1,
                   saved_payment_method_title = $2,
                   auto_renew_card = TRUE
               WHERE telegram_id = $3""",
            payment_method_id, title, telegram_id,
        )
    logger.info(
        f"SAVED_PAYMENT_METHOD user={telegram_id} pm_id={payment_method_id[:8]}... "
        f"title={title}"
    )
    return True


async def get_user_payment_method(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Get saved payment method info for a user.

    Returns:
        {"payment_method_id": str, "title": str, "auto_renew_card": bool} or None
    """
    pool = await database.get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT saved_payment_method_id, saved_payment_method_title, auto_renew_card
               FROM subscriptions
               WHERE telegram_id = $1""",
            telegram_id,
        )
        if not row or not row.get("saved_payment_method_id"):
            return None
        return {
            "payment_method_id": row["saved_payment_method_id"],
            "title": row["saved_payment_method_title"],
            "auto_renew_card": row.get("auto_renew_card", False),
        }


async def remove_user_payment_method(telegram_id: int) -> bool:
    """
    Remove saved payment method and disable card auto-renewal.

    Args:
        telegram_id: User's Telegram ID

    Returns:
        True if removed successfully
    """
    pool = await database.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE subscriptions
               SET saved_payment_method_id = NULL,
                   saved_payment_method_title = NULL,
                   auto_renew_card = FALSE
               WHERE telegram_id = $1""",
            telegram_id,
        )
    logger.info(f"REMOVED_PAYMENT_METHOD user={telegram_id}")
    return True


async def toggle_card_auto_renew(telegram_id: int, enabled: bool) -> bool:
    """
    Enable/disable card-based auto-renewal.

    Args:
        telegram_id: User's Telegram ID
        enabled: Whether to enable card auto-renewal

    Returns:
        True if toggled successfully
    """
    pool = await database.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        # Only enable if payment method is saved
        if enabled:
            row = await conn.fetchrow(
                "SELECT saved_payment_method_id FROM subscriptions WHERE telegram_id = $1",
                telegram_id,
            )
            if not row or not row.get("saved_payment_method_id"):
                return False

        await conn.execute(
            "UPDATE subscriptions SET auto_renew_card = $1 WHERE telegram_id = $2",
            enabled, telegram_id,
        )
    logger.info(f"TOGGLE_CARD_AUTO_RENEW user={telegram_id} enabled={enabled}")
    return True
