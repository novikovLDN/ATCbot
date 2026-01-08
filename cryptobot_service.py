"""
Crypto Bot (Telegram Crypto Pay) Integration

Handles invoice creation and webhook processing for cryptocurrency payments.
"""
import os
import json
import hmac
import hashlib
import logging
from typing import Optional, Dict, Any
import httpx
from aiohttp import web
from aiogram import Bot

logger = logging.getLogger(__name__)

# Configuration
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_WEBHOOK_SECRET = os.getenv("CRYPTOBOT_WEBHOOK_SECRET", "")
CRYPTOBOT_API_URL = os.getenv("CRYPTOBOT_API_URL", "https://pay.crypt.bot/api")

ALLOWED_ASSETS = ["USDT", "TON", "BTC"]


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
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{CRYPTOBOT_API_URL}/createInvoice",
            headers=_get_auth_headers(),
            json=request_body
        )
    
    if response.status_code != 200:
        raise Exception(f"Crypto Bot API error: {response.status_code} - {response.text}")
    
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
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{CRYPTOBOT_API_URL}/createInvoice",
            headers=_get_auth_headers(),
            json=request_body
        )
    
    if response.status_code != 200:
        raise Exception(f"Crypto Bot API error: {response.status_code} - {response.text}")
    
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
    
    # Parse payload
    payload_raw = invoice.get("payload")
    if not payload_raw:
        logger.error(f"Crypto Bot webhook: missing payload, invoice_id={invoice_id}")
        return web.json_response({"status": "invalid"}, status=200)
    
    try:
        payload_data = json.loads(payload_raw)
    except Exception as e:
        logger.error(f"Crypto Bot webhook: failed to parse payload: {e}")
        return web.json_response({"status": "invalid"}, status=200)
    
    purchase_id = payload_data.get("purchase_id")
    telegram_id = payload_data.get("telegram_user_id")
    tariff = payload_data.get("tariff")
    period_days = payload_data.get("period_days")
    
    if not all([purchase_id, telegram_id, tariff, period_days]):
        logger.error(f"Crypto Bot webhook: missing required fields in payload: {payload_data}")
        return web.json_response({"status": "invalid"}, status=200)
    
    try:
        telegram_id = int(telegram_id)
        period_days = int(period_days)
    except (ValueError, TypeError) as e:
        logger.error(f"Crypto Bot webhook: invalid telegram_id or period_days: {e}")
        return web.json_response({"status": "invalid"}, status=200)
    
    # Get pending purchase
    import database
    pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id)
    if not pending_purchase:
        logger.warning(f"Crypto Bot webhook: pending purchase not found: purchase_id={purchase_id}, user={telegram_id}")
        return web.json_response({"status": "not_found"}, status=200)
    
    if pending_purchase.get("status") != "pending":
        logger.info(f"Crypto Bot webhook: purchase already processed: purchase_id={purchase_id}, status={pending_purchase.get('status')}")
        return web.json_response({"status": "already_processed"}, status=200)
    
    # Get payment amount from invoice
    amount_rubles = float(invoice.get("amount", {}).get("fiat", {}).get("value", 0))
    if amount_rubles <= 0:
        amount_rubles = pending_purchase["price_kopecks"] / 100.0
    
    # Create payment record and activate subscription
    pool = await database.get_pool()
    payment_id = None
    
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval(
            "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
            telegram_id, f"{tariff}_{period_days}", int(amount_rubles * 100)
        )
    
    if not payment_id:
        logger.error(f"Crypto Bot webhook: failed to create payment record: user={telegram_id}, purchase_id={purchase_id}")
        return web.json_response({"status": "error"}, status=200)
    
    logger.info(f"Crypto Bot payment received: user={telegram_id}, payment_id={payment_id}, invoice_id={invoice_id}, purchase_id={purchase_id}, amount={amount_rubles} RUB")
    
    # Activate subscription
    from datetime import timedelta
    duration = timedelta(days=period_days)
    
    try:
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source="payment",
            admin_telegram_id=None,
            admin_grant_days=None
        )
        
        if not result:
            raise Exception("grant_access returned None")
        
        expires_at = result.get("subscription_end")
        if result.get("vless_url"):
            vpn_key = result["vless_url"]
        else:
            subscription = await database.get_subscription(telegram_id)
            if subscription and subscription.get("vpn_key"):
                vpn_key = subscription["vpn_key"]
            else:
                uuid = result.get("uuid")
                if uuid:
                    import vpn_utils
                    vpn_key = vpn_utils.generate_vless_url(uuid)
                else:
                    vpn_key = ""
        
        if not expires_at or not vpn_key:
            raise Exception(f"Invalid grant_access result: expires_at={expires_at}, vpn_key={bool(vpn_key)}")
        
        # Mark payment as approved
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE payments SET status = 'approved' WHERE id = $1",
                payment_id
            )
        
        # Mark pending purchase as paid
        await database.mark_pending_purchase_paid(purchase_id)
        
        # Send confirmation to user
        import localization
        user = await database.get_user(telegram_id)
        language = user.get("language", "ru") if user else "ru"
        
        expires_str = expires_at.strftime("%d.%m.%Y")
        text = localization.get_text(language, "payment_approved", date=expires_str)
        
        from handlers import get_vpn_key_keyboard
        await bot.send_message(telegram_id, text, reply_markup=get_vpn_key_keyboard(language), parse_mode="HTML")
        await bot.send_message(telegram_id, f"<code>{vpn_key}</code>", parse_mode="HTML")
        
        logger.info(f"Crypto Bot payment processed successfully: user={telegram_id}, payment_id={payment_id}, invoice_id={invoice_id}")
        
    except Exception as e:
        logger.exception(f"Crypto Bot webhook: failed to activate subscription: user={telegram_id}, payment_id={payment_id}, error={e}")
        # Payment remains in 'pending' status for manual review
        return web.json_response({"status": "error"}, status=200)
    
    return web.json_response({"status": "ok"}, status=200)


async def register_webhook_route(app: web.Application, bot: Bot):
    """Register Crypto Bot webhook route"""
    async def webhook_handler(request: web.Request) -> web.Response:
        return await handle_webhook(request, bot)
    
    app.router.add_post("/webhooks/cryptobot", webhook_handler)
    logger.info("Crypto Bot webhook registered: POST /webhooks/cryptobot")

