"""
Platega.io (SBP) Integration

Handles SBP payment creation and webhook processing.
Configuration: merchant_id/secret/API URL resolved via config.py only.
"""
import config
import database
import json
import logging
import math
from typing import Optional, Dict, Any
from uuid import uuid4
import httpx
from aiohttp import web
from aiogram import Bot
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


async def create_transaction(
    amount_rubles: float,
    description: str,
    purchase_id: str,
    return_url: Optional[str] = None,
    failed_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create SBP payment transaction via Platega API.

    Args:
        amount_rubles: Payment amount in rubles (already with markup applied)
        description: Payment description
        purchase_id: Internal purchase ID (stored in payload)
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
        "paymentMethod": 2,  # SBP
        "id": str(uuid4()),
        "paymentDetails": {
            "amount": round(amount_rubles, 2),
            "currency": "RUB",
        },
        "description": description[:250] if description else "Atlas Secure VPN",
        "payload": json.dumps({"purchase_id": purchase_id}),
    }
    if return_url:
        request_body["return"] = return_url
        request_body["returnUrl"] = None
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


async def handle_webhook(request: web.Request, bot: Bot) -> web.Response:
    """
    Handle Platega webhook callback.

    Platega sends POST with headers X-MerchantId and X-Secret for auth.
    Payload contains transaction status and payload with purchase_id.

    Always returns 200 OK for reliability.
    """
    if not is_enabled():
        logger.warning("Platega webhook received but service is disabled")
        return web.json_response({"status": "disabled"}, status=200)

    if not database.DB_READY:
        logger.warning("Platega webhook: DB not ready")
        return web.json_response({"status": "degraded"}, status=200)

    # Verify authentication headers
    merchant_id = request.headers.get("X-MerchantId", "")
    secret = request.headers.get("X-Secret", "")

    if merchant_id != PLATEGA_MERCHANT_ID or secret != PLATEGA_SECRET:
        logger.warning(
            f"Platega webhook: auth failed, merchant_id_match={merchant_id == PLATEGA_MERCHANT_ID}"
        )
        return web.json_response({"status": "unauthorized"}, status=200)

    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Platega webhook: invalid JSON: {e}")
        return web.json_response({"status": "invalid"}, status=200)

    transaction_id = body.get("id") or body.get("transactionId")
    status = (body.get("status") or "").lower()

    logger.info(
        f"Platega webhook received: transaction_id={transaction_id}, status={status}"
    )

    # Only process confirmed/completed payments
    if status not in ("confirmed", "completed", "paid"):
        logger.info(f"Platega webhook: ignoring status={status}")
        return web.json_response({"status": "ignored"}, status=200)

    # Extract purchase_id from payload
    payload_raw = body.get("payload")
    purchase_id = None

    if payload_raw:
        try:
            if isinstance(payload_raw, str):
                payload_data = json.loads(payload_raw)
            else:
                payload_data = payload_raw
            purchase_id = payload_data.get("purchase_id")
        except (json.JSONDecodeError, TypeError):
            pass

    if not purchase_id:
        logger.error(f"Platega webhook: could not extract purchase_id, payload={payload_raw}")
        return web.json_response({"status": "invalid"}, status=200)

    # Look up pending purchase
    pending_purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)

    if not pending_purchase:
        logger.warning(f"Platega webhook: purchase not found: purchase_id={purchase_id}")
        return web.json_response({"status": "not_found"}, status=200)

    telegram_id = pending_purchase["telegram_id"]
    purchase_status = pending_purchase.get("status")

    if purchase_status != "pending":
        logger.info(
            f"Platega webhook: purchase already processed: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return web.json_response({"status": "already_processed"}, status=200)

    # Get payment amount
    payment_details = body.get("paymentDetails", {})
    amount_rubles = float(payment_details.get("amount", 0))
    if amount_rubles <= 0:
        amount_rubles = pending_purchase["price_kopecks"] / 100.0

    logger.info(
        f"payment_event_received: provider=platega, user={telegram_id}, "
        f"transaction_id={transaction_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB"
    )

    # Finalize purchase
    try:
        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider="platega",
            amount_rubles=amount_rubles,
            invoice_id=str(transaction_id),
        )

        if not result or not result.get("success"):
            logger.error(f"Platega webhook: finalize_purchase failed: {result}")
            raise Exception(f"finalize_purchase returned invalid result: {result}")

        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        is_balance_topup = result.get("is_balance_topup", False)

        # Send confirmation to user
        from app.services.language_service import resolve_user_language
        from app.i18n import get_text as i18n_get_text

        language = await resolve_user_language(telegram_id)

        if is_balance_topup:
            topup_amount = result.get("amount", amount_rubles)
            text = i18n_get_text(language, "main.balance_topup_success", amount=topup_amount)
            await bot.send_message(telegram_id, text, parse_mode="HTML")
            logger.info(
                f"Platega payment processed (balance topup): user={telegram_id}, "
                f"payment_id={payment_id}, amount={topup_amount} RUB"
            )
        else:
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            subscription_type = (result.get("subscription_type") or "basic").strip().lower()
            if subscription_type not in ("basic", "plus"):
                subscription_type = "basic"
            if subscription_type == "plus":
                text = f"🎉 Добро пожаловать в Atlas Secure!\n⭐️ Тариф: Plus\n📅 До: {expires_str}"
            else:
                text = f"🎉 Добро пожаловать в Atlas Secure!\n📦 Тариф: Basic\n📅 До: {expires_str}"

            from app.handlers.common.keyboards import get_connect_keyboard
            await bot.send_message(
                telegram_id, text, reply_markup=get_connect_keyboard(), parse_mode="HTML"
            )
            logger.info(
                f"Platega payment processed: user={telegram_id}, payment_id={payment_id}, "
                f"purchase_id={purchase_id}, subscription_activated=True"
            )

    except ValueError as e:
        logger.info(
            f"Platega webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return web.json_response({"status": "already_processed"}, status=200)
    except Exception as e:
        logger.exception(
            f"Platega webhook: finalize_purchase failed: user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
        )
        return web.json_response({"status": "error"}, status=200)

    return web.json_response({"status": "ok"}, status=200)


async def register_webhook_route(app: web.Application, bot: Bot):
    """Register Platega webhook route."""
    async def webhook_handler(request: web.Request) -> web.Response:
        return await handle_webhook(request, bot)

    app.router.add_post("/webhooks/platega", webhook_handler)
    logger.info("Platega webhook registered: POST /webhooks/platega")
