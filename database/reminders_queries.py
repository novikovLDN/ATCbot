"""Напоминания об истечении подписки: кому и когда писать.

ЧТО ЗДЕСЬ ЕСТЬ
    Выборки подписок, которым пора отправить напоминание, и отметки о том,
    что напоминание уже ушло. Сама отправка живёт в reminders.py — здесь
    только работа с базой.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/subscriptions.py: этот код читают и правят при
    работе над рассылками, а не над платежами, и держать его рядом с
    денежными транзакциями было незачем.

ВАЖНО ПРО ФЛАГИ
    Отметки о напоминаниях (reminder_sent, notified_*) нужны, чтобы не
    писать пользователю повторно. Любая новая выборка обязана их учитывать,
    иначе при рестарте воркера пользователь получит письмо второй раз.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import (
    get_pool,
    _to_db_utc,
    _from_db_utc,
    _normalize_subscription_row,
)

logger = logging.getLogger(__name__)


async def get_subscriptions_needing_reminder() -> list:
    """Получить подписки, которым нужно отправить напоминание
    
    Возвращает список подписок, где:
    - expires_at > now (активная)
    - reminder_sent = FALSE
    - expires_at <= now + 3 days
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscriptions_needing_reminder skipped")
        return []
    now = datetime.now(timezone.utc)
    reminder_date = now + timedelta(days=3)
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_needing_reminder skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE expires_at > $1 
               AND expires_at <= $2
               AND reminder_sent = FALSE
               ORDER BY expires_at ASC""",
            _to_db_utc(now), _to_db_utc(reminder_date)
        )
        return [_normalize_subscription_row(row) for row in rows]


async def mark_reminder_sent(telegram_id: int):
    """Отметить, что напоминание отправлено пользователю (старая функция, для совместимости)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET reminder_sent = TRUE WHERE telegram_id = $1",
            telegram_id
        )


# SECURITY: Pre-built SQL queries for each reminder flag.
# Eliminates f-string SQL interpolation — only static SQL strings are used.
_REMINDER_FLAG_UPDATE_QUERIES = {
    "reminder_7d_sent": (
        "UPDATE subscriptions SET reminder_7d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_3d_sent": (
        "UPDATE subscriptions SET reminder_3d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_1d_sent": (
        "UPDATE subscriptions SET reminder_1d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_24h_sent": (
        "UPDATE subscriptions SET reminder_24h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_3h_sent": (
        "UPDATE subscriptions SET reminder_3h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_6h_sent": (
        "UPDATE subscriptions SET reminder_6h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "trial_notif_24h_sent": (
        "UPDATE subscriptions SET trial_notif_24h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "trial_notif_3h_sent": (
        "UPDATE subscriptions SET trial_notif_3h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
}

# Expose frozenset for external validation (used by app/services/notifications/service.py)
_ALLOWED_REMINDER_FLAGS = frozenset(_REMINDER_FLAG_UPDATE_QUERIES.keys())


async def mark_reminder_flag_sent(telegram_id: int, flag_name: str):
    """Отметить, что конкретное напоминание отправлено пользователю

    Args:
        telegram_id: Telegram ID пользователя
        flag_name: Имя флага ('reminder_3d_sent', 'reminder_24h_sent', 'reminder_3h_sent', 'reminder_6h_sent')

    Raises:
        ValueError: если flag_name не в whitelist
    """
    query = _REMINDER_FLAG_UPDATE_QUERIES.get(flag_name)
    if query is None:
        raise ValueError(
            f"Invalid flag_name '{flag_name}'. "
            f"Allowed: {sorted(_ALLOWED_REMINDER_FLAGS)}"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(query, telegram_id)


async def mark_user_unreachable(telegram_id: int) -> None:
    """Mark user as unreachable (chat not found, blocked). Background workers filter by is_reachable."""
    if not _core.DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_reachable = FALSE WHERE telegram_id = $1",
                telegram_id
            )
    except asyncpg.UndefinedColumnError:
        logger.debug("mark_user_unreachable skipped: is_reachable column not present")
    except Exception as e:
        logger.warning(f"mark_user_unreachable failed for user={telegram_id}: {e}")


async def update_last_reminder_at(subscription_id: int) -> None:
    """Update last_reminder_at for idempotency guard (container restart protection)."""
    if not _core.DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE id = $1",
                subscription_id
            )
    except Exception as e:
        logger.warning(f"update_last_reminder_at failed for subscription_id={subscription_id}: {e}")


# Active promo definition: is_active=true AND deleted_at IS NULL AND expires_at > now() AND used_count < max_uses

# Окна воркера напоминаний по умолчанию: (часов до конца, допуск, флаг).
# Держатся синхронно с app/services/notifications/service.py — оттуда же их
# передаёт reminders.py с учётом настроек дашборда. Значения-дубли тут только
# на случай вызова без аргументов (тесты, ручные проверки): импортировать
# сервисный слой из database нельзя, он сам импортирует database.
DEFAULT_REMINDER_WINDOWS = (
    (24 * 7, 12.0, "reminder_7d_sent"),
    (24 * 3, 6.0, "reminder_3d_sent"),
    (24.0, 2.0, "reminder_1d_sent"),
    (3.0, 1.0, "reminder_3h_sent"),
    (6.0, 0.5, "reminder_6h_sent"),    # админский грант на сутки
    (24.0, 1.0, "reminder_24h_sent"),  # админский грант на неделю
)

# Сколько строк готовы отдать за один проход. Верхняя граница нужна, чтобы
# при аномалии (например, админ выставил допуск в 1000 часов) воркер не
# вытянул полбазы и не упёрся в свой таймаут.
_REMINDER_BATCH_SIZE = 500
_REMINDER_MAX_ROWS = 20000


async def get_subscriptions_for_reminders(
    windows=None,
    batch_size: int = _REMINDER_BATCH_SIZE,
    max_rows: int = _REMINDER_MAX_ROWS,
) -> list:
    """Кандидаты на напоминание — отбор в SQL, чтение батчами.

    ЧТО БЫЛО. Запрос тянул ВСЕ строки с expires_at > now, без лимита, с
    коррелированным подзапросом к subscription_history на каждую. Сюда же
    попадали bypass-only строки с датой «сейчас + 10 лет» и триальные, которых
    обслуживает отдельный воркер. Итерация в reminders.py обёрнута в
    asyncio.wait_for(120s); на базе в десятки тысяч подписок она в него не
    укладывалась, срабатывал WORKER_TIMEOUT, и цикл отменялся. Из-за
    ORDER BY expires_at ASC хвост выборки — это ровно кандидаты на
    напоминание за 7 дней, то есть они не получали письма систематически, а в
    логах виднелось только «iteration cancelled».

    ЧТО ТЕПЕРЬ. Окна (7д/3д/1д/3ч + админские 6ч/24ч) и невыставленные флаги
    проверяются в SQL, bypass-only и триалы отсекаются там же, строки читаются
    страницами по `batch_size` с курсором по id — соединение пула берётся на
    каждый батч отдельно и не удерживается на весь проход.

    `windows` — список (часов до конца, допуск в часах, имя флага). Передаётся
    воркером из настроек дашборда, чтобы выборка не разъезжалась с
    should_send_reminder. Имя флага сверяется с whitelist: оно уходит в текст
    запроса, параметром колонку не подставить.
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscriptions_for_reminders skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_for_reminders skipped")
        return []

    now = datetime.now(timezone.utc)
    params: List[Any] = [_to_db_utc(now)]
    window_terms: List[str] = []
    for before_hours, tolerance_hours, flag in (windows or DEFAULT_REMINDER_WINDOWS):
        if flag not in _ALLOWED_REMINDER_FLAGS:
            raise ValueError(
                f"Invalid reminder flag '{flag}'. Allowed: {sorted(_ALLOWED_REMINDER_FLAGS)}"
            )
        lower = now + timedelta(hours=float(before_hours) - float(tolerance_hours))
        upper = now + timedelta(hours=float(before_hours) + float(tolerance_hours))
        params.append(_to_db_utc(lower))
        lower_idx = len(params)
        params.append(_to_db_utc(upper))
        upper_idx = len(params)
        window_terms.append(
            f"(s.expires_at BETWEEN ${lower_idx} AND ${upper_idx} "
            f"AND COALESCE(s.{flag}, FALSE) = FALSE)"
        )
    if not window_terms:
        return []

    cursor_idx = len(params) + 1
    limit_idx = len(params) + 2
    params.extend([0, int(batch_size)])

    window_clause = " OR ".join(window_terms)
    history_column = (
        "(SELECT action_type FROM subscription_history\n"
        "              WHERE telegram_id = s.telegram_id\n"
        "              ORDER BY created_at DESC LIMIT 1) AS last_action_type"
    )
    # Полный вариант: живой чат (is_reachable) + отсев bypass-only.
    query_full = f"""
        SELECT s.*, {history_column}
        FROM subscriptions s
        JOIN users u ON s.telegram_id = u.telegram_id
        WHERE s.expires_at > $1
          AND COALESCE(u.is_reachable, TRUE) = TRUE
          AND COALESCE(s.is_bypass_only, FALSE) = FALSE
          AND LOWER(COALESCE(s.source, '')) <> 'trial'
          AND LOWER(COALESCE(s.subscription_type, '')) <> 'trial'
          AND ({window_clause})
          AND s.id > ${cursor_idx}
        ORDER BY s.id
        LIMIT ${limit_idx}"""
    # Запасной — для схемы без is_reachable / is_bypass_only (миграция 014 и
    # более поздние). Триалы отсекаются и здесь: двойные уведомления «пробный
    # период заканчивается» + «подписка заканчивается» уже ловили на проде.
    query_fallback = f"""
        SELECT s.*, {history_column}
        FROM subscriptions s
        WHERE s.expires_at > $1
          AND LOWER(COALESCE(s.source, '')) <> 'trial'
          AND LOWER(COALESCE(s.subscription_type, '')) <> 'trial'
          AND ({window_clause})
          AND s.id > ${cursor_idx}
        ORDER BY s.id
        LIMIT ${limit_idx}"""

    query = query_full
    out: list = []
    last_id = 0
    while True:
        params[cursor_idx - 1] = last_id
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, *params)
            except asyncpg.UndefinedColumnError:
                if query is query_fallback:
                    raise
                logger.warning(
                    "DB_SCHEMA_OUTDATED: is_reachable/is_bypass_only missing, "
                    "fallback to legacy reminder query"
                )
                query = query_fallback
                rows = await conn.fetch(query, *params)
        if not rows:
            break
        out.extend(_normalize_subscription_row(row) for row in rows)
        last_id = rows[-1]["id"]
        if len(rows) < batch_size:
            break
        if len(out) >= max_rows:
            logger.warning(
                "get_subscriptions_for_reminders: достигнут потолок %d строк — "
                "остаток обслужим следующим проходом", max_rows,
            )
            break
    return out
