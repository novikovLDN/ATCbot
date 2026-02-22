"""
Crypto Bot (Telegram Crypto Pay) Integration

Handles invoice creation and webhook processing for cryptocurrency payments.
Configuration: Token/webhook/API URL resolved via config.py only (Railway env-safe).
"""
import config
import json
import hmac
import hashlib
import logging
from typing import Optional, Dict, Any
import httpx
from aiohttp import web
from aiogram import Bot
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py (STAGE_CRYPTOBOT_* / PROD_CRYPTOBOT_*)
CRYPTOBOT_TOKEN = config.CRYPTOBOT_TOKEN
CRYPTOBOT_WEBHOOK_SECRET = config.CRYPTOBOT_WEBHOOK_SECRET
CRYPTOBOT_API_URL = config.CRYPTOBOT_API_URL

ALLOWED_ASSETS = ["USDT", "TON", "BTC"]


class CryptoBotError(Exception):
    """Base class for Crypto Bot API errors"""
    pass


class CryptoBotAuthError(CryptoBotError):
    """Authentication error (401, 403)"""
    pass


class CryptoBotInvalidResponseError(CryptoBotError):
    """Invalid response error (4xx)"""
    pass


def is_enabled() -> bool:
    """Check if Crypto Bot is configured"""
    return bool(CRYPTOBOT_TOKEN and CRYPTOBOT_WEBHOOK_SECRET)


def _get_auth_headers() -> Dict[str, str]:
    """Get authentication headers for Crypto Bot API"""
    return {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }


def _verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify webhook signature using HMAC-SHA256
    
    Args:
        payload: Raw request body
        signature: Signature from X-Crypto-Pay-API-Signature header
        
    Returns:
        True if signature is valid
    """
    if not CRYPTOBOT_WEBHOOK_SECRET:
        return False
    
    expected_signature = hmac.new(
        CRYPTOBOT_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)


async def create_invoice(
    telegram_id: int,
    tariff: str,
    period_days: int,
    amount_rubles: float,
    purchase_id: str,
    asset: str = "USDT",
    description: str = ""
) -> Dict[str, Any]:
    """
    Create invoice via Crypto Bot API
    
    Args:
        telegram_id: User Telegram ID
        tariff: Tariff type (basic/plus)
        period_days: Subscription period in days
        amount_rubles: Amount in rubles
        purchase_id: Purchase session ID
        asset: Cryptocurrency asset (USDT/TON/BTC)
        description: Invoice description
        
    Returns:
        Invoice data with invoice_id and pay_url
        
    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("Crypto Bot not configured")
    
    if asset not in ALLOWED_ASSETS:
        raise ValueError(f"Invalid asset: {asset}. Allowed: {ALLOWED_ASSETS}")
    
    payload_data = {
        "purchase_id": purchase_id,
        "telegram_user_id": telegram_id,
        "tariff": tariff,
        "period_days": period_days,
    }
    
    request_body = {
        "amount": round(float(amount_rubles), 2),
        "fiat": "RUB",
        "asset": asset,
        "payload": json.dumps(payload_data, ensure_ascii=False),
        "description": description[:250] if description else f"Atlas Secure VPN {tariff} {period_days} days",
        "allow_comments": False,
        "allow_anonymous": False,
    }
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{CRYPTOBOT_API_URL}/createInvoice",
                headers=_get_auth_headers(),
                json=request_body
            )
            # Convert 401/403 to AuthError (should NOT be retried)
            if response.status_code == 401 or response.status_code == 403:
                error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"Crypto Bot API error: {error_msg}")
                raise CryptoBotAuthError(error_msg)
            
            # Convert 4xx to InvalidResponseError (should NOT be retried)
            if 400 <= response.status_code < 500:
                error_msg = f"Client error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"Crypto Bot API error: {error_msg}")
                raise CryptoBotInvalidResponseError(error_msg)
            
            # Only 5xx/timeout/network errors will be retried
            if response.status_code != 200:
                # Let httpx raise HTTPStatusError for 5xx, which will be retried
                response.raise_for_status()
            return response
    
    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=0.5,
        max_delay=3.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError)
    )
    
    data = response.json()
    if not data.get("ok"):
        error_msg = data.get("error", {}).get("name", "Unknown error")
        raise Exception(f"Crypto Bot API error: {error_msg}")
    
    result = data.get("result", {})
    if not result.get("invoice_id") or not result.get("pay_url"):
        raise Exception("Invalid response from Crypto Bot API: missing invoice_id or pay_url")
    
    return result


async def create_balance_invoice(
    telegram_id: int,
    amount_rubles: float,
    description: str = ""
) -> Dict[str, Any]:
    """
    Create balance top-up invoice via Crypto Bot API
    
    Args:
        telegram_id: User Telegram ID
        amount_rubles: Amount in rubles
        description: Invoice description
        
    Returns:
        Invoice data with invoice_id and pay_url
        
    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("Crypto Bot not configured")
    
    import time
    timestamp = int(time.time())
    payload_data = {
        "telegram_user_id": telegram_id,
        "amount": amount_rubles,
        "type": "balance_topup",
        "timestamp": timestamp,
    }
    
    request_body = {
        "amount": round(float(amount_rubles), 2),
        "fiat": "RUB",
        "asset": "USDT",
        "payload": json.dumps(payload_data, ensure_ascii=False),
        "description": description[:250] if description else f"Пополнение баланса {amount_rubles} RUB",
        "allow_comments": False,
        "allow_anonymous": False,
    }
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{CRYPTOBOT_API_URL}/createInvoice",
                headers=_get_auth_headers(),
                json=request_body
            )
            # Convert 401/403 to AuthError (should NOT be retried)
            if response.status_code == 401 or response.status_code == 403:
                error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"Crypto Bot API error: {error_msg}")
                raise CryptoBotAuthError(error_msg)
            
            # Convert 4xx to InvalidResponseError (should NOT be retried)
            if 400 <= response.status_code < 500:
                error_msg = f"Client error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"Crypto Bot API error: {error_msg}")
                raise CryptoBotInvalidResponseError(error_msg)
            
            # Only 5xx/timeout/network errors will be retried
            if response.status_code != 200:
                # Let httpx raise HTTPStatusError for 5xx, which will be retried
                response.raise_for_status()
            return response
    
    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError)
    )
    
    data = response.json()
    if not data.get("ok"):
        error_msg = data.get("error", {}).get("name", "Unknown error")
        raise Exception(f"Crypto Bot API error: {error_msg}")
    
    result = data.get("result", {})
    if not result.get("invoice_id") or not result.get("pay_url"):
        raise Exception("Invalid response from Crypto Bot API: missing invoice_id or pay_url")
    
    return result


async def handle_webhook(request: web.Request, bot: Bot) -> web.Response:
    """
    Handle Crypto Bot webhook
    
    Requirements:
    - Always returns 200 OK
    - Validates webhook signature
    - Processes only invoice_paid events
    - Idempotent: duplicate payments are ignored
    """
    if not is_enabled():
        logger.warning("Crypto Bot webhook received but service is disabled")
        return web.json_response({"status": "disabled"}, status=200)
    
    if not database.DB_READY:
        logger.warning("Crypto Bot webhook: DB not ready")
        return web.json_response({"status": "degraded"}, status=200)
    
    # Verify signature
    signature = request.headers.get("X-Crypto-Pay-API-Signature", "")
    if not signature:
        logger.warning("Crypto Bot webhook: missing signature")
        return web.json_response({"status": "unauthorized"}, status=200)
    
    try:
        body_bytes = await request.read()
        if not _verify_webhook_signature(body_bytes, signature):
            logger.warning("Crypto Bot webhook: invalid signature")
            return web.json_response({"status": "unauthorized"}, status=200)
        
        body = json.loads(body_bytes.decode())
    except Exception as e:
        logger.error(f"Crypto Bot webhook: invalid JSON: {e}")
        return web.json_response({"status": "invalid"}, status=200)
    
    # Process only invoice_paid events
    update_type = body.get("update_type")
    if update_type != "invoice_paid":
        logger.debug(f"Crypto Bot webhook: ignored update_type={update_type}")
        return web.json_response({"status": "ignored"}, status=200)
    
    invoice = body.get("payload", {})
    invoice_id = invoice.get("invoice_id")
    status = invoice.get("status")
    
    if status != "paid":
        logger.info(f"Crypto Bot webhook: invoice not paid, status={status}, invoice_id={invoice_id}")
        return web.json_response({"status": "ignored"}, status=200)
    
    # Parse payload — support both formats:
    # 1) JSON: {"purchase_id":"...","telegram_user_id":123,"tariff":"basic","period_days":30}
    # 2) String: "purchase:{purchase_id}" (from payments/cryptobot flow)
    payload_raw = invoice.get("payload")
    if not payload_raw:
        logger.error(f"Crypto Bot webhook: missing payload, invoice_id={invoice_id}")
        return web.json_response({"status": "invalid"}, status=200)
    
    purchase_id = None
    telegram_id = None
    pending_purchase = None
    
    try:
        payload_data = json.loads(payload_raw)
        if isinstance(payload_data, dict):
            purchase_id = payload_data.get("purchase_id")
            telegram_id = payload_data.get("telegram_user_id")
            if telegram_id is not None:
                telegram_id = int(telegram_id)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    
    if not purchase_id and isinstance(payload_raw, str) and payload_raw.startswith("purchase:"):
        purchase_id = payload_raw.split(":", 1)[1].strip()
    
    if not purchase_id:
        logger.error(f"Crypto Bot webhook: could not extract purchase_id from payload: {payload_raw[:100]}")
        return web.json_response({"status": "invalid"}, status=200)
    
    # Get pending purchase — by purchase_id only when telegram_id unknown (payload format 2)
    import database
    logger.info(f"Crypto Bot webhook: looking for purchase: purchase_id={purchase_id}")
    if telegram_id is not None:
        pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
    else:
        pending_purchase = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
    
    if pending_purchase:
        telegram_id = pending_purchase["telegram_id"]
    if not pending_purchase:
        logger.warning(f"Crypto Bot webhook: pending purchase not found: purchase_id={purchase_id}, user={telegram_id}")
        return web.json_response({"status": "not_found"}, status=200)
    
    purchase_status = pending_purchase.get("status")
    if purchase_status != "pending":
        logger.info(f"Crypto Bot webhook: purchase already processed: purchase_id={purchase_id}, status={purchase_status}")
        return web.json_response({"status": "already_processed"}, status=200)
    
    logger.info(f"Crypto Bot webhook: valid pending purchase found: purchase_id={purchase_id}, user={telegram_id}, tariff={pending_purchase.get('tariff')}, period_days={pending_purchase.get('period_days')}")
    
    # Get payment amount from invoice (Crypto Pay may return fiat in USD or RUB)
    amount_obj = invoice.get("amount") or {}
    fiat_obj = (amount_obj.get("fiat") or {}) if isinstance(amount_obj, dict) else {}
    amount_value = float(fiat_obj.get("value", 0) or 0)
    fiat_currency = (fiat_obj.get("currency") or "RUB").upper()
    if amount_value > 0 and fiat_currency == "USD":
        from payments.cryptobot import RUB_TO_USD_RATE
        amount_rubles = amount_value * RUB_TO_USD_RATE
    else:
        amount_rubles = amount_value if amount_value > 0 else 0
    if amount_rubles <= 0:
        amount_rubles = pending_purchase["price_kopecks"] / 100.0
    
    # КРИТИЧНО: Логируем получение события оплаты от Crypto Bot
    logger.info(
        f"payment_event_received: provider=cryptobot, user={telegram_id}, invoice_id={invoice_id}, "
        f"purchase_id={purchase_id}, amount={amount_rubles:.2f} RUB, "
        f"status=paid, update_type=invoice_paid"
    )
    
    # КРИТИЧНО: Проверка суммы платежа перед активацией
    expected_amount_rubles = pending_purchase["price_kopecks"] / 100.0
    amount_diff = abs(amount_rubles - expected_amount_rubles)
    
    if amount_diff > 1.0:
        logger.error(
            f"payment_rejected: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"reason=amount_mismatch, expected={expected_amount_rubles:.2f} RUB, "
            f"actual={amount_rubles:.2f} RUB, diff={amount_diff:.2f} RUB"
        )
        return web.json_response({"status": "amount_mismatch"}, status=200)
    
    logger.info(
        f"payment_verified: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
        f"amount={amount_rubles:.2f} RUB, amount_match=True, purchase_status=pending"
    )
    
    # ЕДИНАЯ ФУНКЦИЯ ФИНАЛИЗАЦИИ ПОКУПКИ
    # Все операции в одной транзакции: pending_purchase → paid, payment → approved, subscription activated
    try:
        logger.info(f"Crypto Bot webhook: calling finalize_purchase: purchase_id={purchase_id}, provider=cryptobot, amount={amount_rubles} RUB")
        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider="cryptobot",
            amount_rubles=amount_rubles,
            invoice_id=invoice_id
        )
        
        if not result or not result.get("success"):
            error_msg = f"finalize_purchase returned invalid result: {result}"
            logger.error(f"Crypto Bot webhook: {error_msg}")
            raise Exception(error_msg)
        
        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        vpn_key = result.get("vpn_key")
        is_balance_topup = result.get("is_balance_topup", False)
        
        # Send confirmation to user
        from app.services.language_service import resolve_user_language
        from app.i18n import get_text as i18n_get_text
        
        language = await resolve_user_language(telegram_id)
        
        if is_balance_topup:
            topup_amount = result.get("amount", amount_rubles)
            text = i18n_get_text(language, "main.balance_topup_success", amount=topup_amount)
            await bot.send_message(telegram_id, text, parse_mode="HTML")
            logger.info(f"Crypto Bot payment processed (balance topup): user={telegram_id}, payment_id={payment_id}, invoice_id={invoice_id}, amount={topup_amount} RUB")
        else:
            logger.info(f"Crypto Bot webhook: purchase_finalized: purchase_id={purchase_id}, payment_id={payment_id}, expires_at={expires_at.isoformat() if expires_at else None}")
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            subscription_type = (result.get("subscription_type") or "basic").strip().lower()
            if subscription_type not in ("basic", "plus"):
                subscription_type = "basic"
            if subscription_type == "plus":
                text = f"🎉 Добро пожаловать в Atlas Secure!\n⭐️ Тариф: Plus\n📅 До: {expires_str}"
            else:
                text = f"🎉 Добро пожаловать в Atlas Secure!\n📦 Тариф: Basic\n📅 До: {expires_str}"
            from app.handlers.common.keyboards import get_connect_keyboard
            await bot.send_message(telegram_id, text, reply_markup=get_connect_keyboard(), parse_mode="HTML")
            logger.info(f"Crypto Bot payment processed successfully: user={telegram_id}, payment_id={payment_id}, invoice_id={invoice_id}, purchase_id={purchase_id}, subscription_activated=True")
        
    except ValueError as e:
        # Pending purchase уже обработан или не найден - это нормально при повторных callback
        logger.info(f"Crypto Bot webhook: purchase already processed (ValueError): purchase_id={purchase_id}, error={e}")
        return web.json_response({"status": "already_processed"}, status=200)
    except Exception as e:
        logger.exception(f"Crypto Bot webhook: finalize_purchase failed: user={telegram_id}, purchase_id={purchase_id}, error={e}")
        # Payment remains in 'pending' status for manual review
        return web.json_response({"status": "error"}, status=200)
    
    return web.json_response({"status": "ok"}, status=200)


async def register_webhook_route(app: web.Application, bot: Bot):
    """Register Crypto Bot webhook route. Also registers unified /webhook/payment."""
    async def webhook_handler(request: web.Request) -> web.Response:
        return await handle_webhook(request, bot)

    # Primary: Crypto Pay API uses this URL (configure in @CryptoBot)
    app.router.add_post("/webhooks/cryptobot", webhook_handler)
    # Unified: Single entry point for payment webhooks (routes by X-Crypto-Pay-API-Signature)
    app.router.add_post("/webhook/payment", webhook_handler)
    logger.info("Crypto Bot webhook registered: POST /webhooks/cryptobot, POST /webhook/payment")

