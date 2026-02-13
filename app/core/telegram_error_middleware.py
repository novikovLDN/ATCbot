"""
Global Telegram update error boundary middleware.

Ensures no handler exception can crash polling.
Never swallows CancelledError.
"""
import asyncio
import logging
from typing import Callable, Awaitable, Dict, Any

from aiogram import BaseMiddleware

from app.core.structured_logger import log_event

logger = logging.getLogger(__name__)


class TelegramErrorBoundaryMiddleware(BaseMiddleware):
    """
    Middleware that wraps handler execution in a strict error boundary.

    Catches all exceptions except CancelledError.
    On exception: logs, attempts graceful callback answer, returns None.
    Never raises; never swallows CancelledError.
    """

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            user_id = None
            correlation_id = None
            if hasattr(event, "update_id"):
                correlation_id = str(event.update_id)
            elif hasattr(event, "callback_query") and event.callback_query and hasattr(event.callback_query, "id"):
                correlation_id = str(event.callback_query.id)
            elif hasattr(event, "message") and event.message and hasattr(event.message, "message_id"):
                correlation_id = str(event.message.message_id)
            if hasattr(event, "from_user") and event.from_user:
                user_id = getattr(event.from_user, "id", None)
            elif hasattr(event, "callback_query") and event.callback_query and hasattr(event.callback_query, "from_user"):
                user_id = getattr(event.callback_query.from_user, "id", None)
            elif hasattr(event, "message") and event.message and hasattr(event.message, "from_user"):
                user_id = getattr(event.message.from_user, "id", None)

            log_event(
                logger,
                component="telegram",
                operation="update_processing",
                correlation_id=correlation_id,
                outcome="failed",
                reason=f"{type(e).__name__}: {str(e)[:200]}",
                level="error",
            )
            logger.exception("UNHANDLED_HANDLER_EXCEPTION", extra={"update_type": type(event).__name__, "user_id": user_id})

            # Graceful fallback response for callback
            answer_target = None
            if hasattr(event, "answer"):
                answer_target = event
            elif hasattr(event, "callback_query") and event.callback_query and hasattr(event.callback_query, "answer"):
                answer_target = event.callback_query

            if answer_target:
                try:
                    await answer_target.answer("⚠️ Произошла ошибка. Попробуйте позже.", show_alert=False)
                except Exception:
                    pass

            return None
