"""
Handler decorators: exception boundaries, error wrappers.
Shared across all handler domains.
"""
import logging
import time

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE
from app.utils.logging_helpers import log_handler_entry, log_handler_exit, classify_error

logger = logging.getLogger(__name__)


def handler_exception_boundary(handler_name: str, operation: str = None):
    """
    Decorator for explicit handler exception boundaries.

    STEP 3 — PART A: HARD FAILURE BOUNDARIES
    Ensures no exception propagates past handler boundary.

    Args:
        handler_name: Name of the handler function
        operation: Operation name (defaults to handler_name)

    Usage:
        @handler_exception_boundary("cmd_start", "user_start")
        @router.message(Command("start"))
        async def cmd_start(message: Message):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            correlation_id = None
            telegram_id = None

            if args and hasattr(args[0], 'message_id'):
                correlation_id = str(args[0].message_id)
            elif args and hasattr(args[0], 'message') and hasattr(args[0].message, 'message_id'):
                correlation_id = str(args[0].message.message_id)

            if args and hasattr(args[0], 'from_user'):
                telegram_id = args[0].from_user.id
            elif args and hasattr(args[0], 'message') and hasattr(args[0].message, 'from_user'):
                telegram_id = args[0].message.from_user.id

            start_time = time.time()
            op_name = operation or handler_name

            log_handler_entry(
                handler_name=handler_name,
                telegram_id=telegram_id,
                operation=op_name,
                correlation_id=correlation_id,
            )

            if not database.DB_READY:
                message_or_query = args[0] if args else None
                if message_or_query:
                    try:
                        warning_text = i18n_get_text(
                            "ru",
                            "errors.db_init_stage_warning"
                        )
                        if hasattr(message_or_query, 'answer') and hasattr(message_or_query, 'text'):
                            await message_or_query.answer(warning_text)
                        elif hasattr(message_or_query, 'message') and hasattr(message_or_query, 'answer'):
                            await message_or_query.message.answer(warning_text)
                            await message_or_query.answer()
                    except Exception as e:
                        logger.debug(f"Error sending DB not ready warning: {e}")
                return

            try:
                result = await func(*args, **kwargs)

                duration_ms = (time.time() - start_time) * 1000
                log_handler_exit(
                    handler_name=handler_name,
                    outcome="success",
                    telegram_id=telegram_id,
                    operation=op_name,
                    duration_ms=duration_ms,
                )

                return result

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                error_type = classify_error(e)

                logger.error(
                    f"[FAILURE_BOUNDARY] Handler exception caught: handler={handler_name}, "
                    f"operation={op_name}, correlation_id={correlation_id}, "
                    f"error_type={error_type}, error={type(e).__name__}: {str(e)[:200]}"
                )

                log_handler_exit(
                    handler_name=handler_name,
                    outcome="failed",
                    telegram_id=telegram_id,
                    operation=op_name,
                    error_type=error_type,
                    duration_ms=duration_ms,
                    reason=f"Exception: {type(e).__name__}"
                )

                try:
                    if args and hasattr(args[0], 'answer'):
                        event = args[0]
                        tid = getattr(getattr(event, 'from_user', None), 'id', None) or telegram_id
                        language = await resolve_user_language(tid) if tid else DEFAULT_LANGUAGE
                        error_text = i18n_get_text(language, "main.error_occurred")
                        await args[0].answer(error_text)
                except Exception:
                    pass

                return None

        return wrapper
    return decorator
