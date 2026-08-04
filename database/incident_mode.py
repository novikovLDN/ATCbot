"""Режим инцидента: баннер об аварии для всех пользователей.

ЧТО ЗДЕСЬ
    Чтение и запись единственной строки incident_settings — флаг «авария
    идёт» и её текст.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Жил среди рассылок, хотя рассылкой не является: это состояние сервиса,
    которое читают экраны бота. Общего с broadcast_log и сегментами у него
    нет ничего, кроме истории.

ЧТО ЛЕГКО СЛОМАТЬ
    Обе функции fail-safe: при недоступной БД или отсутствующей таблице
    возвращается «инцидента нет», а не исключение. Иначе авария в БД
    вешала бы баннер об аварии — или роняла бы экраны, которые его
    спрашивают.

    Строка в таблице ОДНА, и она выбирается через ORDER BY id LIMIT 1.
    UPDATE без этого условия перепишет все строки, если их вдруг станет
    больше одной.
"""
import asyncpg
import logging
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


async def get_incident_settings() -> Dict[str, Any]:
    """Получить настройки инцидента
    
    Returns:
        Словарь с is_active и incident_text
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_incident_settings skipped")
        return {"is_active": False, "incident_text": None}
    
    pool = await get_pool()
    if pool is None:
        return {"is_active": False, "incident_text": None}
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, incident_text FROM incident_settings ORDER BY id LIMIT 1"
            )
            if row:
                return {"is_active": row["is_active"], "incident_text": row["incident_text"]}
            return {"is_active": False, "incident_text": None}
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"incident_settings table missing or inaccessible — skipping: {e}")
        return {"is_active": False, "incident_text": None}
    except Exception as e:
        logger.warning(f"Error getting incident settings: {e}")
        return {"is_active": False, "incident_text": None}


async def set_incident_mode(is_active: bool, incident_text: Optional[str] = None):
    """Установить режим инцидента
    
    Args:
        is_active: Активен ли режим инцидента
        incident_text: Текст инцидента (опционально)
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), set_incident_mode skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, set_incident_mode skipped")
        return
    
    try:
        async with pool.acquire() as conn:
            if incident_text is not None:
                await conn.execute(
                    """UPDATE incident_settings 
                       SET is_active = $1, incident_text = $2, updated_at = CURRENT_TIMESTAMP
                       WHERE id = (SELECT id FROM incident_settings ORDER BY id LIMIT 1)""",
                    is_active, incident_text
                )
            else:
                await conn.execute(
                    """UPDATE incident_settings 
                       SET is_active = $1, updated_at = CURRENT_TIMESTAMP
                       WHERE id = (SELECT id FROM incident_settings ORDER BY id LIMIT 1)""",
                    is_active
                )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"incident_settings table missing or inaccessible — skipping: {e}")
    except Exception as e:
        logger.warning(f"Error setting incident mode: {e}")
