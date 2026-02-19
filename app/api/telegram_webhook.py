"""
Telegram webhook endpoint.
Receives updates from Telegram and feeds them to aiogram Dispatcher.
"""
import asyncio
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

# Liveness heartbeat — updated on every incoming Telegram update.
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
    # Update liveness heartbeat FIRST (before any validation) — H3 fix
    global last_webhook_update_at
    last_webhook_update_at = time.monotonic()

    # Validate secret token
    if not config.WEBHOOK_SECRET:
        logger.error("WEBHOOK_SECRET not configured")
        return Response(status_code=503)

    if x_telegram_bot_api_secret_token != config.WEBHOOK_SECRET:
        logger.warning(
            "WEBHOOK_SECRET_MISMATCH ip=%s",
            request.client.host if request.client else "unknown"
        )
        return Response(status_code=403)

    # Parse and feed update to aiogram with timeout — C2 fix
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
            # Return 200 anyway — prevents Telegram from retrying
            return Response(status_code=200)
    except Exception as e:
        logger.error("WEBHOOK_PROCESSING_ERROR error=%s", e)
        # Return 200 anyway — prevents Telegram from retrying a bad update
        return Response(status_code=200)

    return Response(status_code=200)
