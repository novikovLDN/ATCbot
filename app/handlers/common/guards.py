"""
DB readiness and permission guards. Shared across all handler domains.
"""
import logging
import time

import config
import database
from aiogram.types import CallbackQuery

from app.i18n import get_text as i18n_get_text
from app.services.language_service import DEFAULT_LANGUAGE

logger = logging.getLogger(__name__)

# Cache critical tables check result for 30 seconds to avoid DB query on every handler
_critical_tables_cache: dict = {"result": None, "expires": 0.0}
_CRITICAL_TABLES_CACHE_TTL = 30.0


async def _check_critical_tables_cached() -> bool:
    """Check critical tables with TTL cache to avoid per-request DB query."""
    now = time.monotonic()
    if _critical_tables_cache["result"] is not None and now < _critical_tables_cache["expires"]:
        return _critical_tables_cache["result"]
    result = await database.check_critical_tables()
    _critical_tables_cache["result"] = result
    _critical_tables_cache["expires"] = now + _CRITICAL_TABLES_CACHE_TTL
    return result


async def ensure_db_ready_message(message_or_query, allow_readonly_in_stage: bool = False) -> bool:
    """
    Проверка готовности базы данных с отправкой сообщения пользователю

    НОВАЯ ЛОГИКА:
    - CRITICAL ошибки (users table missing) → блокируем UI в PROD
    - NON-CRITICAL ошибки (audit_log, incident_settings missing) → НЕ блокируем UI
    - В STAGE разрешаем read-only операции даже при отсутствии опциональных таблиц

    Args:
        message_or_query: Message или CallbackQuery объект
        allow_readonly_in_stage: Если True, в STAGE разрешает read-only операции без БД

    Returns:
        True если БД готова или операция разрешена, False если БД недоступна (сообщение отправлено)
    """
    critical_ok = await _check_critical_tables_cached()

    if not critical_ok:
        if allow_readonly_in_stage and config.IS_STAGE:
            return True

        language = DEFAULT_LANGUAGE

        if config.IS_PROD:
            error_text = i18n_get_text(language, "main.service_unavailable")
        else:
            error_text = i18n_get_text(language, "errors.db_init_stage_warning")

        try:
            if hasattr(message_or_query, 'answer') and hasattr(message_or_query, 'text'):
                await message_or_query.answer(error_text)
            elif hasattr(message_or_query, 'message') and hasattr(message_or_query, 'answer'):
                await message_or_query.message.answer(error_text)
                await message_or_query.answer()
        except Exception as e:
            logger.exception(f"Error sending degraded mode message: {e}")

        return False

    return True


async def ensure_db_ready_callback(callback: CallbackQuery, allow_readonly_in_stage: bool = False) -> bool:
    """
    Проверка готовности базы данных для CallbackQuery (для удобства)

    Args:
        callback: CallbackQuery объект
        allow_readonly_in_stage: Если True, в STAGE разрешает read-only операции без БД

    Returns:
        True если БД готова или операция разрешена в STAGE, False если БД недоступна (сообщение отправлено)
    """
    return await ensure_db_ready_message(callback, allow_readonly_in_stage=allow_readonly_in_stage)
