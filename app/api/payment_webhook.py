"""
Payment Webhook API (FastAPI)

Webhook endpoints for payment providers:
- POST /webhooks/platega — Platega (SBP) payment notifications
- POST /webhooks/cryptobot — CryptoBot (Crypto Pay) payment notifications

Security:
- Signature/auth verification required per provider.
- Idempotent: duplicate webhooks return 200, no re-activation.
- Amount tolerance: ±1 RUB.
- Pending expiry: 30 min (pending_purchases.expires_at).
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_bot = None


def setup(bot):
    """Store bot instance for webhook handlers."""
    global _bot
    _bot = bot


async def _handle_platega_webhook(request: Request):
    """Handle Platega (SBP) webhook callback."""
    try:
        import platega_service
        if not platega_service.is_enabled():
            logger.warning("Platega webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Platega webhook: invalid JSON: {e}")
            return JSONResponse({"status": "invalid"})

        result = await platega_service.process_webhook_data(headers, body, _bot)
        return JSONResponse(result)

    except ImportError:
        logger.error("platega_service not available")
        return JSONResponse({"status": "error"}, status_code=500)
    except Exception as e:
        logger.exception(f"Platega webhook error: {e}")
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/platega")
async def platega_webhook(request: Request):
    return await _handle_platega_webhook(request)


@router.post("/platega/callback")
async def platega_callback(request: Request):
    """Alias route — Platega dashboard sends webhooks to this URL."""
    return await _handle_platega_webhook(request)


async def _handle_cryptobot_webhook(request: Request):
    """Handle CryptoBot (Crypto Pay) webhook callback."""
    try:
        import cryptobot_service
        if not cryptobot_service.is_enabled():
            logger.warning("CryptoBot webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        raw_body = await request.body()
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"CryptoBot webhook: invalid JSON: {e}")
            return JSONResponse({"status": "invalid"})

        result = await cryptobot_service.process_webhook_data(headers, raw_body, body, _bot)
        return JSONResponse(result)

    except ImportError:
        logger.error("cryptobot_service not available")
        return JSONResponse({"status": "error"}, status_code=500)
    except Exception as e:
        logger.exception(f"CryptoBot webhook error: {e}")
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/cryptobot")
async def cryptobot_webhook(request: Request):
    return await _handle_cryptobot_webhook(request)
