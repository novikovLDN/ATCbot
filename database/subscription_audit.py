"""Журнал жизненного цикла подписки: аудит, история, сигнал сторожу.

ЧТО ЗДЕСЬ
    Всё, что только ПИШЕТ следы происходящего и ничего не решает:
        _log_audit_event_atomic            запись в audit_log внутри чужой транзакции
        _log_audit_event_atomic_standalone то же, но со своей транзакцией
        _log_vpn_lifecycle_audit_async     события VPN (выдача/снятие/продление/истечение)
        _log_vpn_lifecycle_audit_fire_and_forget  та же запись без ожидания
        _log_subscription_history_atomic   строка в subscription_history
        _notify_watchdog_expires_at        сигнал сторожу о записи expires_at

ПОЧЕМУ ОТДЕЛЬНО
    Эти шесть функций звали отовсюду: выдача, перевыпуск, истечение,
    админские операции, финализация покупки. Пока они лежали в
    database/subscriptions.py, любой, кому нужна была одна строчка аудита,
    тянул за собой весь модуль на три тысячи строк вместе с grant_access
    и finalize_purchase.

ГЛАВНОЕ СВОЙСТВО, КОТОРОЕ ЛЕГКО СЛОМАТЬ
    Ни одна функция отсюда НЕ имеет права бросить исключение — кроме
    _log_subscription_history_atomic, которая пишет историю и падает
    вместе с транзакцией осознанно. Остальные глушат всё: аудит не должен
    отменять выдачу доступа или проведённый платёж. Если убрать
    try/except «чтобы увидеть ошибку», сломается не лог, а оплата.

    _log_audit_event_atomic обязана вызываться ТОЛЬКО внутри уже открытой
    транзакции — она пишет в переданное соединение и своей транзакции не
    открывает.
"""
import asyncpg
import logging
from datetime import datetime
from typing import Optional

import database.core as _core
from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


def _notify_watchdog_expires_at(
    telegram_id: int,
    *,
    grant_action: str,
    old_expires_at: Optional[datetime],
    new_expires_at: datetime,
    source: Optional[str] = None,
    tariff: Optional[str] = None,
    admin_telegram_id: Optional[int] = None,
    admin_grant_days: Optional[int] = None,
) -> None:
    """Fire-and-forget bridge to app.services.subscription_watchdog.

    Called after every UPDATE/INSERT that writes `subscriptions.expires_at`.
    Never raises. If the new value is > NOW + 8 years for a PREMIUM row,
    the watchdog logs it to `subscription_over_issuance_log` and sends a
    Telegram admin alert. Bypass-only rows are filtered out here — the
    watchdog also checks, but this is a fast early-out.
    """
    if not new_expires_at:
        return
    try:
        from app.services.subscription_watchdog import notify_expires_at_write
        notify_expires_at_write(
            telegram_id,
            old_expires_at=old_expires_at,
            new_expires_at=new_expires_at,
            grant_action=grant_action,
            source=source,
            tariff=tariff,
            admin_telegram_id=admin_telegram_id,
            admin_grant_days=admin_grant_days,
            is_bypass_only=False,
        )
    except Exception:
        # Watchdog must never break the grant flow.
        pass


async def _log_audit_event_atomic(
    conn,
    action: str,
    telegram_id: int,
    target_user: Optional[int] = None,
    details: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Записать событие аудита в таблицу audit_log
    
    STEP 5 — COMPLIANCE & AUDITABILITY:
    Must be called ONLY within an active transaction.
    
    PART F — FAILURE SAFETY:
    Non-blocking, best-effort. Never throws exceptions.
    
    Args:
        conn: Database connection (within transaction)
        action: Action type (e.g., 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID of the actor
        target_user: Telegram ID of the target user (optional)
        details: Additional details (optional, JSON string)
        correlation_id: Correlation ID for tracing (optional)
    """
    try:
        # STEP 5 — PART F: FAILURE SAFETY
        # Try to insert with correlation_id if column exists, fallback to without it
        try:
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, details, correlation_id)
                   VALUES ($1, $2, $3, $4, $5)""",
                action, telegram_id, target_user, details, correlation_id
            )
        except (asyncpg.UndefinedColumnError, asyncpg.PostgresError):
            # Fallback if correlation_id column doesn't exist yet
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, details)
                   VALUES ($1, $2, $3, $4)""",
                action, telegram_id, target_user, details
            )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"audit_log table missing or inaccessible — skipping audit log: action={action}, telegram_id={telegram_id}")
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"Error logging audit event: {e}")


async def _log_vpn_lifecycle_audit_async(
    action: str,
    telegram_id: int,
    uuid: Optional[str] = None,
    source: Optional[str] = None,
    result: str = "success",
    details: Optional[str] = None
):
    """
    Записать событие VPN lifecycle в audit_log (async, non-blocking).
    
    Используется для логирования:
    - add_user: создание UUID через VPN API
    - remove_user: удаление UUID через VPN API
    - renew: продление подписки (без создания UUID)
    - expire: автоматическое истечение подписки
    
    Не блокирует основной flow - ошибки логируются, но не пробрасываются.
    
    Args:
        action: Тип действия ('vpn_add_user', 'vpn_remove_user', 'vpn_renew', 'vpn_expire')
        telegram_id: Telegram ID пользователя
        uuid: UUID пользователя (опционально, частично логируется для безопасности)
        source: Источник ('payment', 'admin', 'auto-expiry', 'test')
        result: Результат операции ('success' или 'error')
        details: Дополнительные детали (опционально)
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Безопасное логирование UUID (только первые 8 символов в БД)
            uuid_safe = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or None)
            
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, uuid, source, result, details)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                action, telegram_id, telegram_id, uuid_safe, source, result, details
            )
            logger.debug(
                f"VPN audit logged: action={action}, user={telegram_id}, uuid={uuid_safe}, "
                f"source={source}, result={result}"
            )
    except Exception as e:
        # Не блокируем основной flow при ошибках логирования
        logger.warning(f"Failed to log VPN audit event: action={action}, user={telegram_id}, error={e}")


def _log_vpn_lifecycle_audit_fire_and_forget(
    action: str,
    telegram_id: int,
    uuid: Optional[str] = None,
    source: Optional[str] = None,
    result: str = "success",
    details: Optional[str] = None
):
    """
    Записать событие VPN lifecycle в audit_log (fire-and-forget, не блокирует).
    
    Создаёт async task для логирования, не ожидает завершения.
    Используется когда нужно залогировать событие вне async контекста.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Если event loop уже запущен, создаём task
            asyncio.create_task(
                _log_vpn_lifecycle_audit_async(action, telegram_id, uuid, source, result, details)
            )
        else:
            # Если event loop не запущен, запускаем корутину
            asyncio.run(_log_vpn_lifecycle_audit_async(action, telegram_id, uuid, source, result, details))
    except Exception as e:
        # Не блокируем основной flow
        logger.warning(f"Failed to schedule VPN audit log: action={action}, user={telegram_id}, error={e}")


async def _log_subscription_history_atomic(conn, telegram_id: int, vpn_key: str, start_date: datetime, end_date: datetime, action_type: str):
    """Записать запись в историю подписок
    
    Должна вызываться ТОЛЬКО внутри активной транзакции.
    
    Args:
        conn: Соединение с БД (внутри транзакции)
        telegram_id: Telegram ID пользователя
        vpn_key: VPN-ключ (может быть None для pending activations)
        start_date: Дата начала периода
        end_date: Дата окончания периода
        action_type: Тип действия ('purchase', 'renewal', 'reissue', 'manual_reissue')
    """
    # Пропускаем запись истории для pending activations (vpn_key == None)
    # История будет записана позже, когда activation_worker активирует подписку
    if vpn_key is None:
        logger.info(
            f"SUBSCRIPTION_HISTORY_SKIPPED [reason=pending_activation, user={telegram_id}, "
            f"action={action_type}, subscription_end={end_date.isoformat()}]"
        )
        return
    
    await conn.execute(
        """INSERT INTO subscription_history (telegram_id, vpn_key, start_date, end_date, action_type)
           VALUES ($1, $2, $3, $4, $5)""",
        telegram_id, vpn_key, _to_db_utc(start_date), _to_db_utc(end_date), action_type
    )


async def _log_audit_event_atomic_standalone(
    action: str,
    telegram_id: int,
    target_user: Optional[int] = None,
    details: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Записать событие аудита в таблицу audit_log (standalone версия)
    
    STEP 5 — COMPLIANCE & AUDITABILITY:
    Creates its own transaction. Used when audit event needs to be logged outside existing transaction.
    
    PART F — FAILURE SAFETY:
    Non-blocking, best-effort. Never throws exceptions.
    
    Args:
        action: Тип действия (например, 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID администратора, который выполнил действие
        target_user: Telegram ID пользователя, над которым выполнено действие (опционально)
        details: Дополнительные детали действия (опционально, JSON string)
        correlation_id: Correlation ID for tracing (optional)
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), audit log skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, audit log skipped")
        return
    
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _log_audit_event_atomic(conn, action, telegram_id, target_user, details, correlation_id)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"audit_log table missing or inaccessible — skipping audit log: action={action}, telegram_id={telegram_id}")
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"Error logging audit event (standalone): {e}")
