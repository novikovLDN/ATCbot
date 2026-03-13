"""
Payment Webhook API (FastAPI)

Webhook endpoints for payment providers:
- POST /webhooks/platega — Platega (SBP) payment notifications
- POST /webhooks/crypto2328 — 2328.io crypto payment notifications

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


@router.post("/webhooks/platega")
async def platega_webhook(request: Request):
    """Handle Platega (SBP) webhook callback."""
    try:
        import platega_service
        if not platega_service.is_enabled():
            logger.warning("Platega webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = dict(request.headers)
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Platega webhook: invalid JSON: {e}")
            return JSONResponse({"status": "invalid"})

        result = await platega_service.process_webhook_data(headers, body, _bot)
        return JSONResponse(result)

    except ImportError:
        logger.error("platega_service not available")
        return JSONResponse({"status": "error"})
    except Exception as e:
        logger.exception(f"Platega webhook error: {e}")
        return JSONResponse({"status": "error"})


@router.post("/webhooks/crypto2328")
async def crypto2328_webhook(request: Request):
    """Handle 2328.io crypto webhook callback."""
    try:
        import crypto2328_service
        if not crypto2328_service.is_enabled():
            logger.warning("2328.io webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"2328.io webhook: invalid JSON: {e}")
            return JSONResponse({"status": "invalid"})

        result = await crypto2328_service.process_webhook_data(body, _bot)
        return JSONResponse(result)

    except ImportError:
        logger.error("crypto2328_service not available")
        return JSONResponse({"status": "error"})
    except Exception as e:
        logger.exception(f"2328.io webhook error: {e}")
        return JSONResponse({"status": "error"})
