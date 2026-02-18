"""
Telegram webhook endpoint.
Receives updates from Telegram and feeds them to aiogram Dispatcher.
"""
import logging
from fastapi import APIRouter, Request, Response, Header
from aiogram.types import Update
import config

logger = logging.getLogger(__name__)

router = APIRouter()

# Bot and Dispatcher are set from main.py at startup
_bot = None
_dp = None

def setup(bot, dp):
    global _bot, _dp
    _bot = bot
    _dp = dp

@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
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

    # Parse and feed update to aiogram
    try:
        body = await request.json()
        update = Update.model_validate(body)
        logger.debug("WEBHOOK_UPDATE update_id=%s", update.update_id)
        await _dp.feed_webhook_update(_bot, update)
    except Exception as e:
        logger.error("WEBHOOK_PROCESSING_ERROR error=%s", e)
        # Return 200 anyway — prevents Telegram from retrying a bad update
        return Response(status_code=200)

    return Response(status_code=200)
