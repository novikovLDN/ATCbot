"""
Payment Webhook API (FastAPI)

Webhook endpoints for payment providers:
- POST /webhooks/platega — Platega (SBP) payment notifications
- POST /webhooks/cryptobot — CryptoBot (Crypto Pay) payment notifications
- POST /webhooks/lava — Lava (Card) payment notifications

Security:
- Signature/auth verification required per provider.
- Idempotent: duplicate webhooks return 200, no re-activation.
- Amount tolerance: ±1 RUB.
- Pending expiry: 30 min (pending_purchases.expires_at).
"""

import asyncio
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.payments.confirmation import TransientPaymentError

# Outer timeout for entire webhook processing — must complete before
# Railway's 30s request timeout. Prevents event loop starvation if
# payment provider APIs are slow.
_WEBHOOK_TIMEOUT = 25.0

logger = logging.getLogger(__name__)

router = APIRouter()

_bot = None


async def _log_pe(
    stage: str,
    provider: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Fire-and-forget payment_errors logger. Webhooks must respond
    fast; never let logging slow down or break a webhook reply."""
    try:
        import database
        await database.log_payment_error(
            stage=stage,
            payment_provider=provider,
            error_code=error_code,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning("payment_errors log skipped (%s): %s", stage, e)


def setup(bot):
    """Store bot instance for webhook handlers."""
    global _bot
    _bot = bot


async def _handle_platega_webhook(request: Request):
    """Handle Platega (SBP) webhook callback."""
    if _bot is None:
        logger.critical("Platega webhook received but bot is not initialized — setup() not called")
        await _log_pe("setup_missing", "platega", error_message="bot not initialized")
        return JSONResponse({"status": "error"}, status_code=500)
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
            await _log_pe("webhook_invalid_json", "platega", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            platega_service.process_webhook_data(headers, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("platega_service not available")
        await _log_pe("service_missing", "platega")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        # Idempotency: already-processed payment — return 200 so provider stops retrying
        logger.info(f"Platega webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"Platega webhook transient error (returning 500 for retry): {e}")
        await _log_pe("transient", "platega", error_message=str(e)[:500])
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("Platega webhook timeout (returning 500 for retry)")
        await _log_pe("timeout", "platega", error_message=f">{_WEBHOOK_TIMEOUT}s")
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"Platega webhook error: {e}")
        await _log_pe("unhandled_exception", "platega",
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
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
    if _bot is None:
        logger.critical("CryptoBot webhook received but bot is not initialized — setup() not called")
        await _log_pe("setup_missing", "cryptobot", error_message="bot not initialized")
        return JSONResponse({"status": "error"}, status_code=500)
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
            await _log_pe("webhook_invalid_json", "cryptobot", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            cryptobot_service.process_webhook_data(headers, raw_body, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("cryptobot_service not available")
        await _log_pe("service_missing", "cryptobot")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        logger.info(f"CryptoBot webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"CryptoBot webhook transient error (returning 500 for retry): {e}")
        await _log_pe("transient", "cryptobot", error_message=str(e)[:500])
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("CryptoBot webhook timeout (returning 500 for retry)")
        await _log_pe("timeout", "cryptobot", error_message=f">{_WEBHOOK_TIMEOUT}s")
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"CryptoBot webhook error: {e}")
        await _log_pe("unhandled_exception", "cryptobot",
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/cryptobot")
async def cryptobot_webhook(request: Request):
    return await _handle_cryptobot_webhook(request)


async def _handle_lava_webhook(request: Request):
    """Handle Lava (Card) webhook callback."""
    if _bot is None:
        logger.critical("Lava webhook received but bot is not initialized — setup() not called")
        await _log_pe("setup_missing", "lava", error_message="bot not initialized")
        return JSONResponse({"status": "error"}, status_code=500)
    try:
        import lava_service
        if not lava_service.is_enabled():
            logger.warning("Lava webhook received but service is disabled")
            return JSONResponse({"status": "disabled"})

        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Lava webhook: invalid JSON: {e}")
            await _log_pe("webhook_invalid_json", "lava", error_message=str(e)[:300])
            return JSONResponse({"status": "invalid"}, status_code=400)

        result = await asyncio.wait_for(
            lava_service.process_webhook_data(headers, body, _bot),
            timeout=_WEBHOOK_TIMEOUT,
        )
        return JSONResponse(result)

    except ImportError:
        logger.error("lava_service not available")
        await _log_pe("service_missing", "lava")
        return JSONResponse({"status": "error"}, status_code=500)
    except ValueError as e:
        # Idempotency: already-processed payment — return 200 so provider stops retrying
        logger.info(f"Lava webhook: already processed: {e}")
        return JSONResponse({"status": "already_processed"})
    except TransientPaymentError as e:
        logger.error(f"Lava webhook transient error (returning 500 for retry): {e}")
        await _log_pe("transient", "lava", error_message=str(e)[:500])
        return JSONResponse({"status": "transient_error"}, status_code=500)
    except asyncio.TimeoutError:
        logger.error("Lava webhook timeout (returning 500 for retry)")
        await _log_pe("timeout", "lava", error_message=f">{_WEBHOOK_TIMEOUT}s")
        return JSONResponse({"status": "timeout"}, status_code=500)
    except Exception as e:
        logger.exception(f"Lava webhook error: {e}")
        await _log_pe("unhandled_exception", "lava",
                      error_code=type(e).__name__,
                      error_message=str(e)[:500])
        return JSONResponse({"status": "error"}, status_code=500)


@router.post("/webhooks/lava")
async def lava_webhook(request: Request):
    return await _handle_lava_webhook(request)
