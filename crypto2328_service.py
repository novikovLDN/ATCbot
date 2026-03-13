"""
2328.io Crypto Payment Integration

Handles cryptocurrency payment creation and webhook processing.
API docs: https://doc.2328.io/
Configuration: project_id/api_key/API URL resolved via config.py only.
"""
import config
import database
import json
import hmac
import hashlib
import base64
import logging
from typing import Optional, Dict, Any
import httpx
from aiogram import Bot
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py
CRYPTO2328_PROJECT_ID = config.CRYPTO2328_PROJECT_ID
CRYPTO2328_API_KEY = (config.CRYPTO2328_API_KEY or "").strip()
CRYPTO2328_API_URL = config.CRYPTO2328_API_URL


def is_enabled() -> bool:
    """Check if 2328.io is configured (project_id + api_key)."""
    return bool(CRYPTO2328_PROJECT_ID and CRYPTO2328_API_KEY)


def _serialize_body(data: dict) -> bytes:
    """Serialize request body to compact JSON bytes (matching PHP json_encode behavior)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _compute_signature(body_bytes: bytes) -> str:
    """
    Compute HMAC-SHA256 signature for 2328.io API request.

    Algorithm:
    1. Base64-encode the JSON body bytes
    2. HMAC-SHA256 of the Base64 string using API_KEY
    """
    b64 = base64.b64encode(body_bytes).decode("utf-8")
    return hmac.new(
        CRYPTO2328_API_KEY.encode("utf-8"),
        b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _compute_empty_signature() -> str:
    """Compute signature for bodyless (GET) requests."""
    b64 = base64.b64encode(b"").decode("utf-8")
    return hmac.new(
        CRYPTO2328_API_KEY.encode("utf-8"),
        b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _prepare_request(data: Optional[dict] = None) -> tuple[Dict[str, str], Optional[bytes]]:
    """Prepare headers and serialized body for 2328.io API request.

    Returns (headers, body_bytes). body_bytes is None for bodyless requests.
    The same JSON bytes are used for both signature and request body to ensure consistency.
    """
    if data:
        body_bytes = _serialize_body(data)
        sign = _compute_signature(body_bytes)
    else:
        body_bytes = None
        sign = _compute_empty_signature()
    headers = {
        "Content-Type": "application/json",
        "project": CRYPTO2328_PROJECT_ID,
        "sign": sign,
    }
    return headers, body_bytes


def _verify_webhook_signature(webhook_data: dict) -> bool:
    """
    Verify webhook signature from 2328.io.

    Algorithm:
    1. Extract 'sign' field from webhook data
    2. Remove 'sign' from the data
    3. JSON-encode remaining fields (compact, matching PHP json_encode)
    4. Base64-encode the JSON
    5. HMAC-SHA256 from Base64 using API_KEY
    6. Compare with received sign
    """
    received_sign = webhook_data.get("sign", "")
    if not received_sign:
        return False

    data_copy = {k: v for k, v in webhook_data.items() if k != "sign"}
    body_bytes = _serialize_body(data_copy)
    calculated_sign = _compute_signature(body_bytes)

    return hmac.compare_digest(calculated_sign, received_sign)


async def create_payment(
    amount_rubles: float,
    order_id: str,
    description: str = "Atlas Secure VPN",
    callback_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create crypto payment via 2328.io API.

    Args:
        amount_rubles: Payment amount in RUB
        order_id: Internal order/purchase ID
        description: Payment description
        callback_url: URL for webhook notifications

    Returns:
        {"uuid": str, "payment_url": str, "order_id": str}

    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("2328.io not configured")
    if not callback_url:
        raise Exception("2328.io requires url_callback (set PUBLIC_BASE_URL in env)")

    request_body = {
        "amount": f"{amount_rubles:.2f}",
        "currency": "RUB",
        "order_id": order_id,
        "url_callback": callback_url,
    }

    headers, body_bytes = _prepare_request(request_body)

    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CRYPTO2328_API_URL}/v1/payment",
                headers=headers,
                content=body_bytes,
            )
            if 400 <= response.status_code < 500:
                logger.error(
                    f"2328.io API client error: status={response.status_code}, "
                    f"response={response.text[:300]}"
                )
                raise Exception(f"2328.io API error: {response.status_code}")
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
    state = data.get("state")
    if state != 0:
        raise Exception(f"2328.io API error: state={state}, response={data}")

    result = data.get("result", {})
    payment_uuid = result.get("uuid")
    payment_url = result.get("url")

    if not payment_uuid or not payment_url:
        raise Exception(f"Invalid 2328.io response: missing uuid or url. Response: {data}")

    logger.info(
        f"2328.io payment created: uuid={payment_uuid}, "
        f"amount={amount_rubles} RUB, order_id={order_id}"
    )

    return {
        "uuid": payment_uuid,
        "payment_url": payment_url,
        "order_id": order_id,
    }


async def process_webhook_data(body: dict, bot: Bot) -> dict:
    """
    Process 2328.io webhook data (framework-agnostic).

    Args:
        body: Parsed JSON body (includes 'sign' field)
        bot: Bot instance for sending messages

    Returns:
        Response dict with "status" key
    """
    if not database.DB_READY:
        logger.warning("2328.io webhook: DB not ready")
        return {"status": "degraded"}

    # Verify signature
    if not _verify_webhook_signature(body):
        logger.warning("2328.io webhook: invalid signature")
        return {"status": "unauthorized"}

    payment_uuid = body.get("uuid")
    order_id = body.get("order_id")
    payment_status = body.get("payment_status", "")

    logger.info(
        f"2328.io webhook received: uuid={payment_uuid}, "
        f"order_id={order_id}, payment_status={payment_status}"
    )

    # Only process successful payments
    if payment_status not in ("paid", "overpaid"):
        logger.info(f"2328.io webhook: ignoring status={payment_status}")
        return {"status": "ignored"}

    # order_id is our purchase_id
    purchase_id = order_id
    if not purchase_id:
        logger.error(f"2328.io webhook: missing order_id, uuid={payment_uuid}")
        return {"status": "invalid"}

    # Look up pending purchase
    pending_purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)

    if not pending_purchase:
        logger.warning(f"2328.io webhook: purchase not found: purchase_id={purchase_id}")
        return {"status": "not_found"}

    telegram_id = pending_purchase["telegram_id"]
    purchase_status_db = pending_purchase.get("status")

    if purchase_status_db != "pending":
        logger.info(
            f"2328.io webhook: purchase already processed: "
            f"purchase_id={purchase_id}, status={purchase_status_db}"
        )
        return {"status": "already_processed"}

    # Get payment amount — use amount from webhook (in fiat currency, RUB)
    amount_str = body.get("amount", "0")
    amount_rubles = float(amount_str)
    if amount_rubles <= 0:
        amount_rubles = pending_purchase["price_kopecks"] / 100.0

    # Amount tolerance check (±1 RUB)
    expected_amount = pending_purchase["price_kopecks"] / 100.0
    if abs(amount_rubles - expected_amount) > 1.0 and payment_status != "overpaid":
        logger.error(
            f"2328.io webhook: amount mismatch: expected={expected_amount:.2f}, "
            f"actual={amount_rubles:.2f}, purchase_id={purchase_id}"
        )
        return {"status": "amount_mismatch"}

    logger.info(
        f"payment_event_received: provider=crypto2328, user={telegram_id}, "
        f"uuid={payment_uuid}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB, status={payment_status}"
    )

    # Finalize purchase
    try:
        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider="crypto2328",
            amount_rubles=amount_rubles,
            invoice_id=str(payment_uuid),
        )

        if not result or not result.get("success"):
            logger.error(f"2328.io webhook: finalize_purchase failed: {result}")
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
                f"2328.io payment processed (balance topup): user={telegram_id}, "
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
                f"2328.io payment processed: user={telegram_id}, payment_id={payment_id}, "
                f"purchase_id={purchase_id}, subscription_activated=True"
            )

    except ValueError as e:
        logger.info(
            f"2328.io webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "already_processed"}
    except Exception as e:
        logger.exception(
            f"2328.io webhook: finalize_purchase failed: user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "error"}

    return {"status": "ok"}


async def register_webhook_route(app, bot: Bot):
    """Register 2328.io webhook route (legacy aiohttp — unused when running on FastAPI)."""
    logger.info("2328.io webhook registered via FastAPI: POST /webhooks/crypto2328")
