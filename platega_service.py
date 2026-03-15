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
        logger.warning("Platega webhook: DB not ready")
        return {"status": "degraded"}

    # Verify authentication headers (case-insensitive lookup)
    merchant_id = headers.get("x-merchantid", "") or headers.get("X-MerchantId", "")
    secret = headers.get("x-secret", "") or headers.get("X-Secret", "")

    if not hmac.compare_digest(str(merchant_id), str(PLATEGA_MERCHANT_ID)) or not hmac.compare_digest(str(secret), str(PLATEGA_SECRET)):
        logger.warning(
            f"Platega webhook: auth failed, merchant_id_match={merchant_id == PLATEGA_MERCHANT_ID}"
        )
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
        return {"status": "invalid"}

    # Look up pending purchase
    pending_purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)

    if not pending_purchase:
        logger.warning(f"Platega webhook: purchase not found: purchase_id={purchase_id}")
        return {"status": "not_found"}

    telegram_id = pending_purchase["telegram_id"]
    purchase_status = pending_purchase.get("status")

    if purchase_status != "pending":
        logger.info(
            f"Platega webhook: purchase already processed: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return {"status": "already_processed"}

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
            try:
                await bot.send_message(telegram_id, text, parse_mode="HTML")
            except Exception as send_err:
                logger.warning(f"Platega: failed to send topup confirmation to user={telegram_id}: {send_err}")
            logger.info(
                f"Platega payment processed (balance topup): user={telegram_id}, "
                f"payment_id={payment_id}, amount={topup_amount} RUB"
            )
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
            text = f"🎉 Добро пожаловать в Atlas Secure!\n{_emoji} Тариф: {_label}\n📅 До: {expires_str}"

            from app.handlers.common.keyboards import get_connect_keyboard
            try:
                await bot.send_message(
                    telegram_id, text, reply_markup=get_connect_keyboard(), parse_mode="HTML"
                )
            except Exception as send_err:
                logger.warning(f"Platega: failed to send subscription confirmation to user={telegram_id}: {send_err}")
            logger.info(
                f"Platega payment processed: user={telegram_id}, payment_id={payment_id}, "
                f"purchase_id={purchase_id}, subscription_activated=True"
            )

    except ValueError as e:
        logger.info(
            f"Platega webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "already_processed"}
    except Exception as e:
        logger.exception(
            f"Platega webhook: finalize_purchase failed: user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "error"}

    return {"status": "ok"}


async def register_webhook_route(app, bot: Bot):
    """Register Platega webhook route (legacy aiohttp — unused when running on FastAPI)."""
    logger.info("Platega webhook registered via FastAPI: POST /webhooks/platega")
