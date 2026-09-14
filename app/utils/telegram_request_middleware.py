"""Bot API request middleware for Telegram-runtime errors (TG-RT-6).

Under load (or after a slow DB / panel call) a handler answers a callback
after Telegram's ~15 s window: answerCallbackQuery fails with 400 "query is
too old and response timeout expired or query ID is invalid". The answer is
only the button spinner — but the exception aborted the whole handler, so the
user got no screen at all (the error boundary swallowed it silently).

IgnoreStaleCallbackAnswer turns exactly that error, for exactly that method,
into a debug log and a normal `True` result. Every other error still raises.
Installed on the Bot session in main.py (and in the e2e World, which mirrors
main.py).
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery

logger = logging.getLogger(__name__)

_STALE_MARKERS = ("query is too old", "query id is invalid")


class IgnoreStaleCallbackAnswer(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method) -> Any:
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            if isinstance(method, AnswerCallbackQuery) and any(m in str(e).lower() for m in _STALE_MARKERS):
                logger.debug("CALLBACK_ANSWER_TOO_LATE query_id=%s — ignored, handler continues",
                             method.callback_query_id)
                return True
            raise


def install(bot) -> None:
    """Register the Telegram-runtime request middlewares on `bot`'s session."""
    bot.session.middleware(IgnoreStaleCallbackAnswer())
