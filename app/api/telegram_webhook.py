"""
Telegram webhook endpoint.
Receives updates from Telegram and feeds them to aiogram Dispatcher.
"""
import asyncio
import hmac
import logging
import time
from fastapi import APIRouter, Request, Response, Header
from aiogram.types import Update
import config

logger = logging.getLogger(__name__)

router = APIRouter()

# Bot and Dispatcher are set from main.py at startup
_bot = None
_dp = None

# Liveness heartbeat — updated on every authenticated Telegram update.
# Imported by main.py watchdog to track "last sign of life from Telegram".
last_webhook_update_at: float = time.monotonic()


def setup(bot, dp):
    global _bot, _dp
    _bot = bot
    _dp = dp


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    # Validate secret token FIRST (before heartbeat — reject unauthorized requests early)
    if not config.WEBHOOK_SECRET:
        logger.error("WEBHOOK_SECRET not configured")
        return Response(status_code=503)

    if not hmac.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(),
        config.WEBHOOK_SECRET.encode(),
    ):
        logger.warning(
            "WEBHOOK_SECRET_MISMATCH ip=%s",
            request.client.host if request.client else "unknown"
        )
        return Response(status_code=403)

    # Update liveness heartbeat AFTER validation (only for authenticated requests)
    global last_webhook_update_at
    last_webhook_update_at = time.monotonic()

    # SECURITY: Reject oversized request bodies (DDoS / memory exhaustion protection)
    # Telegram updates are typically < 10 KB; 1 MB is a generous upper bound.
    MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > MAX_BODY_SIZE:
            logger.warning(
                "WEBHOOK_BODY_TOO_LARGE ip=%s content_length=%s",
                request.client.host if request.client else "unknown",
                content_length,
            )
            return Response(status_code=413)
    except (ValueError, TypeError):
        logger.warning(
            "WEBHOOK_INVALID_CONTENT_LENGTH ip=%s content_length=%s",
            request.client.host if request.client else "unknown",
            content_length,
        )
        return Response(status_code=400)

    # Parse and feed update to aiogram with timeout
    webhook_start = time.monotonic()
    try:
        body = await request.json()
        update = Update.model_validate(body)
        logger.debug("WEBHOOK_UPDATE update_id=%s", update.update_id)

        # Wrap handler execution with timeout (25s — Railway request timeout is 30s)
        try:
            await asyncio.wait_for(
                _dp.feed_webhook_update(_bot, update),
                timeout=25.0
            )
        except asyncio.TimeoutError:
            logger.error(
                "WEBHOOK_HANDLER_TIMEOUT update_id=%s — returning 200 to prevent retry",
                update.update_id
            )
            try:
                from app.core.metrics import get_metrics
                get_metrics().requests_timeout.inc()
            except Exception:
                pass
            # Return 200 anyway — prevents Telegram from retrying
            return Response(status_code=200)
    except Exception as e:
        logger.error("WEBHOOK_PROCESSING_ERROR error=%s", e)
        try:
            from app.core.metrics import get_metrics
            get_metrics().webhook_errors.inc()
            get_metrics().errors.record(type(e).__name__, str(e)[:200], "webhook")
        except Exception:
            pass
        # Return 200 anyway — prevents Telegram from retrying a bad update
        return Response(status_code=200)
    finally:
        # Record webhook metrics
        try:
            from app.core.metrics import get_metrics
            m = get_metrics()
            m.webhook_requests.inc()
            m.webhook_latency.observe(time.monotonic() - webhook_start)
        except Exception:
            pass

    return Response(status_code=200)
