"""
Database operations: Admin, Analytics, Broadcasts, Exports, Gifts, VIP, Discounts.

All shared state (get_pool, helpers) imported from database.core.
DB_READY accessed via _core.DB_READY to get live value (not stale import-time copy).
Cross-module calls use lazy imports to avoid circular dependencies.
"""
import asyncpg
import logging
import random
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING, List
import config
import vpn_utils
import database.core as _core
from database.core import (
    get_pool,
    _to_db_utc, _from_db_utc, _ensure_utc,
    _generate_subscription_uuid, safe_int,
    retry_async,
)

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

async def expire_old_pending_purchases() -> int:
    """
    Автоматически помечает истёкшие pending покупки как expired
    
    Returns:
        Количество истёкших покупок
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, expire_old_pending_purchases skipped")
        return 0
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, expire_old_pending_purchases skipped")
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE status = 'pending' AND expires_at <= NOW()"
        )
        
        # Извлекаем количество обновлённых строк из результата
        # Формат результата: "UPDATE N"
        if result and result.startswith("UPDATE "):
            count = int(result.split()[1])
            if count > 0:
                logger.info(f"Expired {count} old pending purchases")
            return count
        return 0


async def get_all_users_for_export() -> list:
    """Получить всех пользователей для экспорта
    
    Returns:
        Список словарей с данными пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
        return [dict(row) for row in rows]


async def get_active_subscriptions_for_export() -> list:
    """Получить все активные подписки для экспорта
    
    Returns:
        Список словарей с данными активных подписок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch(
            "SELECT * FROM subscriptions WHERE expires_at > $1 ORDER BY expires_at DESC",
            _to_db_utc(now)
        )
        return [dict(row) for row in rows]


# Функция get_vpn_keys_stats удалена - больше не используется
# VPN-ключи теперь создаются динамически через Outline API, статистика по пулу не актуальна


async def get_subscription_history(telegram_id: int, limit: int = 5) -> list:
    """Получить историю подписок пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        limit: Максимальное количество записей (по умолчанию 5)
    
    Returns:
        Список словарей с записями истории, отсортированные по created_at DESC
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscription_history skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription_history skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscription_history 
               WHERE telegram_id = $1 
               ORDER BY created_at DESC 
               LIMIT $2""",
            telegram_id, limit
        )
        return [dict(row) for row in rows]


async def get_user_extended_stats(telegram_id: int) -> Dict[str, Any]:
    """Получить расширенную статистику пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Словарь со статистикой:
        - renewals_count: количество продлений подписки
        - reissues_count: количество перевыпусков ключа
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Подсчитываем продления (action_type = 'renewal')
        renewals_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history 
               WHERE telegram_id = $1 AND action_type = 'renewal'""",
            telegram_id
        )
        
        # Подсчитываем перевыпуски ключа (action_type IN ('reissue', 'manual_reissue'))
        reissues_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history 
               WHERE telegram_id = $1 AND action_type IN ('reissue', 'manual_reissue')""",
            telegram_id
        )
        
        return {
            "renewals_count": renewals_count or 0,
            "reissues_count": reissues_count or 0
        }


async def get_business_metrics() -> Dict[str, Any]:
    """Получить бизнес-метрики сервиса
    
    Returns:
        Словарь с метриками:
        - avg_payment_approval_time_seconds: среднее время подтверждения оплаты (в секундах)
        - avg_subscription_lifetime_days: среднее время жизни подписки (в днях)
        - avg_renewals_per_user: среднее количество продлений на пользователя
        - approval_rate_percent: процент подтвержденных платежей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Среднее время подтверждения оплаты
        # Используем audit_log для получения времени подтверждения
        # Парсим Payment ID из details поля через CTE
        avg_approval_time = await conn.fetchval(
            """WITH payment_approvals AS (
                SELECT 
                    al.created_at as approved_at,
                    CAST(SUBSTRING(al.details FROM 'Payment ID: ([0-9]+)') AS INTEGER) as payment_id
                FROM audit_log al
                WHERE al.action IN ('payment_approved', 'subscription_renewed')
                AND al.details LIKE 'Payment ID: %'
            )
            SELECT AVG(EXTRACT(EPOCH FROM (pa.approved_at - p.created_at))) 
            FROM payment_approvals pa
            JOIN payments p ON p.id = pa.payment_id
            WHERE p.status = 'approved'"""
        )
        
        # 2. Среднее время жизни подписки (из subscription_history)
        # Используем только завершенные подписки (end_date < now)
        avg_lifetime = await conn.fetchval(
            """SELECT AVG(EXTRACT(EPOCH FROM (end_date - start_date)) / 86400.0)
               FROM subscription_history
               WHERE end_date IS NOT NULL
               AND end_date < NOW()"""
        )
        
        # 3. Среднее количество продлений на пользователя
        total_renewals = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history WHERE action_type = 'renewal'"""
        )
        total_users_with_subscriptions = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) FROM subscription_history"""
        )
        avg_renewals = 0.0
        if total_users_with_subscriptions and total_users_with_subscriptions > 0:
            avg_renewals = (total_renewals or 0) / total_users_with_subscriptions
        
        # 4. Процент подтвержденных платежей
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM payments")
        approved_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'approved'"
        )
        approval_rate = 0.0
        if total_payments and total_payments > 0:
            approval_rate = ((approved_payments or 0) / total_payments) * 100
        
        return {
            "avg_payment_approval_time_seconds": float(avg_approval_time) if avg_approval_time else None,
            "avg_subscription_lifetime_days": float(avg_lifetime) if avg_lifetime else None,
            "avg_renewals_per_user": float(avg_renewals) if avg_renewals else 0.0,
            "approval_rate_percent": float(approval_rate) if approval_rate else 0.0,
        }


async def get_last_audit_logs(limit: int = 10) -> list:
    """Получить последние записи из audit_log
    
    Args:
        limit: Количество записей для получения (по умолчанию 10)
    
    Returns:
        Список словарей с записями audit_log, отсортированных по created_at DESC
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_last_audit_logs skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        return []
    
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM audit_log 
                   ORDER BY created_at DESC 
                   LIMIT $1""",
                limit
            )
            return [dict(row) for row in rows]
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"audit_log table missing or inaccessible — skipping: {e}")
        return []
    except Exception as e:
        logger.warning(f"Error getting audit logs: {e}")
        return []


async def create_broadcast(title: str, message: str, broadcast_type: str, segment: str, sent_by: int, is_ab_test: bool = False, message_a: str = None, message_b: str = None) -> int:
    """Создать новое уведомление
    
    Args:
        title: Заголовок уведомления
        message: Текст уведомления (для обычных уведомлений)
        broadcast_type: Тип уведомления (info | maintenance | security | promo)
        segment: Сегмент получателей (all_users | active_subscriptions)
        sent_by: Telegram ID администратора
        is_ab_test: Является ли уведомление A/B тестом
        message_a: Текст варианта A (для A/B тестов)
        message_b: Текст варианта B (для A/B тестов)
    
    Returns:
        ID созданного уведомления
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_ab_test:
            row = await conn.fetchrow(
                """INSERT INTO broadcasts (title, message_a, message_b, is_ab_test, type, segment, sent_by)
                   VALUES ($1, $2, $3, TRUE, $4, $5, $6)
                   RETURNING id""",
                title, message_a, message_b, broadcast_type, segment, sent_by
            )
        else:
            row = await conn.fetchrow(
                """INSERT INTO broadcasts (title, message, is_ab_test, type, segment, sent_by)
                   VALUES ($1, $2, FALSE, $3, $4, $5)
                   RETURNING id""",
                title, message, broadcast_type, segment, sent_by
            )
        return row["id"]


async def get_broadcast(broadcast_id: int) -> Optional[Dict[str, Any]]:
    """Получить уведомление по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM broadcasts WHERE id = $1", broadcast_id
        )
        return dict(row) if row else None


async def save_broadcast_discount(broadcast_id: int, discount_percent: int, discount_hours: int = 168, discount_label: str = "7 дней") -> None:
    """Save discount percentage and duration for a broadcast promo button."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ensure columns exist
        try:
            await conn.execute("ALTER TABLE broadcast_discounts ADD COLUMN IF NOT EXISTS discount_hours INTEGER DEFAULT 168")
            await conn.execute("ALTER TABLE broadcast_discounts ADD COLUMN IF NOT EXISTS discount_label TEXT DEFAULT '7 дней'")
        except Exception:
            pass
        await conn.execute(
            """INSERT INTO broadcast_discounts (broadcast_id, discount_percent, discount_hours, discount_label)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (broadcast_id) DO UPDATE SET discount_percent = $2, discount_hours = $3, discount_label = $4""",
            broadcast_id, discount_percent, discount_hours, discount_label
        )


async def get_broadcast_discount(broadcast_id: int) -> Optional[Dict[str, Any]]:
    """Get discount info for a broadcast promo button."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM broadcast_discounts WHERE broadcast_id = $1",
            broadcast_id
        )
        return dict(row) if row else None


async def get_analytics_by_period(
    hours: int,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Получить аналитику за указанный период.

    Если `since` задан — окно [since, now). Иначе trailing `hours` часов
    от текущего момента (старое поведение). `since` нужен дашборду,
    чтобы считать «сегодня по МСК» (UTC+3) — окно с 00:00 МСК.

    Returns:
        Словарь с ключами:
        - new_users: новые пользователи за период
        - trial_activated: активировали пробный период за период
        - new_subscriptions: новые платные подписки за период
        - total_users: общее количество пользователей
        - total_trial_used: всего активировали trial
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
        since_db = _to_db_utc(since)

        new_users = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1",
            since_db
        )

        trial_activated = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE trial_used_at IS NOT NULL AND trial_used_at >= $1",
            since_db
        )

        new_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE activated_at >= $1",
            since_db
        )

        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")

        total_trial_used = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE trial_used_at IS NOT NULL"
        )

        return {
            "new_users": new_users or 0,
            "trial_activated": trial_activated or 0,
            "new_subscriptions": new_subscriptions or 0,
            "total_users": total_users or 0,
            "total_trial_used": total_trial_used or 0,
        }


async def get_active_paid_subscriptions_count() -> int:
    """Count of subscriptions that are paid, not bypass-only, not trial,
    with expires_at in the future. This is the number an admin actually
    cares about — get_extended_bot_stats's active_subscriptions also
    includes trial rows and bypass-only entries, which inflates it."""
    pool = await get_pool()
    if pool is None:
        return 0
    now = _to_db_utc(datetime.now(timezone.utc))
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval(
                """SELECT COUNT(*) FROM subscriptions
                   WHERE status = 'active'
                     AND expires_at > $1
                     AND COALESCE(is_bypass_only, FALSE) = FALSE
                     AND COALESCE(source, '') != 'trial'
                     AND subscription_type IN (
                         'basic', 'plus', 'biz_starter', 'biz_team',
                         'biz_business', 'biz_pro', 'biz_enterprise',
                         'biz_ultimate'
                     )""",
                now,
            )
            return int(n or 0)
    except Exception as e:
        logger.warning("get_active_paid_subscriptions_count failed: %s", e)
        return 0


async def get_revenue_for_period(
    hours: int,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Money in over the window from paid pending_purchases.

    If `since` is given, the lower bound is that exact moment (used for
    "today MSK" tile on the dashboard). Otherwise — trailing N hours.

    Returns totals (rubles) + counts split by purchase_type so the
    UI can render a single KPI for the period plus a small breakdown.
    """
    pool = await get_pool()
    if pool is None:
        return {
            "revenue_rubles": 0.0,
            "payments_count": 0,
            "avg_check_rubles": 0.0,
            "by_type": {},
        }
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since = _to_db_utc(since)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS total_kopecks,
                   COUNT(*)::BIGINT AS count
               FROM pending_purchases
               WHERE status = 'paid' AND created_at >= $1""",
            since,
        )
        by_type_rows = await conn.fetch(
            """SELECT
                   COALESCE(purchase_type, 'subscription') AS purchase_type,
                   COUNT(*)::BIGINT AS count,
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS revenue_kopecks
               FROM pending_purchases
               WHERE status = 'paid' AND created_at >= $1
               GROUP BY purchase_type
               ORDER BY revenue_kopecks DESC""",
            since,
        )
    total = int(row["total_kopecks"]) if row else 0
    count = int(row["count"]) if row else 0
    return {
        "revenue_rubles": total / 100,
        "payments_count": count,
        "avg_check_rubles": (total / 100 / count) if count else 0.0,
        "by_type": {
            r["purchase_type"]: {
                "count": int(r["count"]),
                "revenue_rubles": int(r["revenue_kopecks"]) / 100,
            }
            for r in by_type_rows
        },
    }


async def get_payments_by_provider(hours: int) -> list:
    """Breakdown of paid purchases by payment_provider.

    Uses the payment_provider column (migration 054) when present.
    NULL rows are bucketed as 'unknown' so old data is still visible.
    """
    pool = await get_pool()
    if pool is None:
        return []
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """SELECT
                       COALESCE(payment_provider, 'unknown') AS provider,
                       COUNT(*)::BIGINT AS count,
                       COALESCE(SUM(price_kopecks), 0)::BIGINT AS revenue_kopecks
                   FROM pending_purchases
                   WHERE status = 'paid' AND created_at >= $1
                   GROUP BY provider
                   ORDER BY revenue_kopecks DESC""",
                since,
            )
        except asyncpg.UndefinedColumnError:
            # Migration 054 not applied yet — return only what we can
            # infer from payments table.
            rows = []
    return [
        {
            "provider": r["provider"],
            "count": int(r["count"]),
            "revenue_rubles": int(r["revenue_kopecks"]) / 100,
        }
        for r in rows
    ]


async def get_recent_payments_feed(
    limit: int = 100,
    hours: Optional[int] = None,
    status: Optional[str] = None,
) -> list:
    """Recent paid (and optionally pending/expired) purchases for the
    Payments page feed. Joins users so we render @username with no
    second round-trip."""
    pool = await get_pool()
    if pool is None:
        return []
    where = ["pp.created_at IS NOT NULL"]
    params: list = []
    if hours is not None:
        params.append(_to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours)))
        where.append(f"pp.created_at >= ${len(params)}")
    if status:
        params.append(status)
        where.append(f"pp.status = ${len(params)}")
    params.append(limit)
    limit_idx = len(params)
    sql = f"""
        SELECT
            pp.id, pp.purchase_id, pp.telegram_id, pp.tariff,
            pp.purchase_type, pp.period_days, pp.price_kopecks,
            pp.status, pp.created_at, pp.promo_code, pp.is_combo,
            pp.country, pp.farm_plot_id,
            COALESCE(pp.payment_provider, 'unknown') AS payment_provider,
            u.username
        FROM pending_purchases pp
        LEFT JOIN users u ON u.telegram_id = pp.telegram_id
        WHERE {' AND '.join(where)}
        ORDER BY pp.created_at DESC
        LIMIT ${limit_idx}
    """
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(sql, *params)
        except asyncpg.UndefinedColumnError:
            # Migration not applied — fall back without payment_provider
            sql_fallback = sql.replace(
                "COALESCE(pp.payment_provider, 'unknown') AS payment_provider,",
                "'unknown' AS payment_provider,",
            )
            rows = await conn.fetch(sql_fallback, *params)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = _from_db_utc(d["created_at"])
        # convert kopecks to rubles for UI
        d["price_rubles"] = (d.get("price_kopecks") or 0) / 100
        out.append(d)
    return out


async def get_user_purchases(telegram_id: int, limit: int = 100) -> list:
    """Все покупки одного пользователя из pending_purchases.

    Этот стол — источник правды для всего, что юзер покупал в боте:
    подписки (basic / plus / биз-тарифы), trafic-паки, балансовые
    пополнения, telegram premium, steam, прокси, фарм-участки.
    Старая таблица payments тоже была, но она устарела и не покрывает
    весь поток — поэтому в карточке юзера показываем именно
    pending_purchases.

    Возвращает все строки (paid + pending + expired) свежие первые.
    """
    pool = await get_pool()
    if pool is None:
        return []
    sql = """
        SELECT
            pp.id, pp.purchase_id, pp.tariff,
            pp.purchase_type, pp.period_days, pp.price_kopecks,
            pp.status, pp.created_at, pp.expires_at, pp.promo_code,
            pp.is_combo, pp.country, pp.farm_plot_id,
            COALESCE(pp.payment_provider, 'unknown') AS payment_provider,
            pp.provider_invoice_id
        FROM pending_purchases pp
        WHERE pp.telegram_id = $1
        ORDER BY pp.created_at DESC NULLS LAST
        LIMIT $2
    """
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(sql, telegram_id, limit)
        except asyncpg.UndefinedColumnError:
            sql_fb = sql.replace(
                "COALESCE(pp.payment_provider, 'unknown') AS payment_provider,",
                "'unknown' AS payment_provider,",
            )
            rows = await conn.fetch(sql_fb, telegram_id, limit)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = _from_db_utc(d["created_at"])
        if d.get("expires_at"):
            d["expires_at"] = _from_db_utc(d["expires_at"])
        d["price_rubles"] = (d.get("price_kopecks") or 0) / 100
        out.append(d)
    return out


async def log_payment_error(
    *,
    stage: str,
    telegram_id: Optional[int] = None,
    purchase_id: Optional[str] = None,
    payment_provider: Optional[str] = None,
    amount_rubles: Optional[float] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Append a payment-error row. Never raises — payment-error logging
    must not break the caller's own error handling. Returns the inserted
    row's id, or None on failure (e.g. table not migrated yet).

    `stage` is a short label like 'webhook_validation', 'amount_mismatch',
    'provider_callback_invalid', 'provision_failed', 'idempotency_rejected'.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None

    import json
    payload_json = None
    if raw_payload is not None:
        try:
            payload_json = json.dumps(raw_payload, default=str)[:8000]
        except Exception:
            payload_json = None

    try:
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """INSERT INTO payment_errors
                       (telegram_id, purchase_id, payment_provider,
                        amount_rubles, stage, error_code, error_message,
                        raw_payload)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                   RETURNING id""",
                telegram_id, purchase_id, payment_provider,
                amount_rubles, stage,
                (error_code or "")[:120] if error_code else None,
                (error_message or "")[:2000] if error_message else None,
                payload_json,
            )
            try:
                from app.events import bus
                bus.publish({
                    "type": "payment:error",
                    "id": int(row_id) if row_id else None,
                    "telegram_id": telegram_id,
                    "stage": stage,
                    "provider": payment_provider,
                })
            except Exception:
                pass
            return int(row_id) if row_id else None
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning("log_payment_error: table missing — %s", e)
        return None
    except Exception as e:
        logger.warning("log_payment_error: %s", e)
        return None


async def get_recent_payment_errors(
    limit: int = 100,
    hours: Optional[int] = None,
    provider: Optional[str] = None,
    stage: Optional[str] = None,
) -> list:
    """Recent payment_errors rows, newest first. Returns [] if the
    table doesn't exist yet."""
    pool = await get_pool()
    if pool is None:
        return []
    where = ["TRUE"]
    params: list = []
    if hours is not None:
        params.append(_to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours)))
        where.append(f"created_at >= ${len(params)}")
    if provider:
        params.append(provider)
        where.append(f"payment_provider = ${len(params)}")
    if stage:
        params.append(stage)
        where.append(f"stage = ${len(params)}")
    params.append(limit)
    limit_idx = len(params)
    sql = f"""
        SELECT pe.*, u.username
        FROM payment_errors pe
        LEFT JOIN users u ON u.telegram_id = pe.telegram_id
        WHERE {' AND '.join(where)}
        ORDER BY pe.created_at DESC
        LIMIT ${limit_idx}
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
        return []

    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = _from_db_utc(d["created_at"])
        if d.get("amount_rubles") is not None:
            try:
                d["amount_rubles"] = float(d["amount_rubles"])
            except Exception:
                d["amount_rubles"] = None
        out.append(d)
    return out


async def get_payment_errors_summary(hours: int = 24) -> Dict[str, Any]:
    """Counters for the Payments page header — total errors in window,
    plus by stage and by provider."""
    pool = await get_pool()
    if pool is None:
        return {"total": 0, "by_stage": [], "by_provider": []}
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM payment_errors WHERE created_at >= $1",
                since,
            ) or 0
            by_stage = await conn.fetch(
                """SELECT stage, COUNT(*)::BIGINT AS count
                   FROM payment_errors
                   WHERE created_at >= $1
                   GROUP BY stage
                   ORDER BY count DESC
                   LIMIT 10""",
                since,
            )
            by_provider = await conn.fetch(
                """SELECT COALESCE(payment_provider, 'unknown') AS provider,
                          COUNT(*)::BIGINT AS count
                   FROM payment_errors
                   WHERE created_at >= $1
                   GROUP BY provider
                   ORDER BY count DESC""",
                since,
            )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
        return {"total": 0, "by_stage": [], "by_provider": []}
    return {
        "total": int(total),
        "by_stage": [{"stage": r["stage"], "count": int(r["count"])} for r in by_stage],
        "by_provider": [
            {"provider": r["provider"], "count": int(r["count"])}
            for r in by_provider
        ],
    }


async def get_traffic_stats(hours: int) -> Dict[str, Any]:
    """Traffic-purchase stats — separate revenue/count + breakdown by
    payment_method (column may be optional on older deploys)."""
    pool = await get_pool()
    if pool is None:
        return {"count": 0, "revenue_rubles": 0.0, "total_gb": 0, "by_method": []}
    since = _to_db_utc(datetime.now(timezone.utc) - timedelta(hours=hours))
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """SELECT
                       COUNT(*)::BIGINT AS count,
                       COALESCE(SUM(price_rub), 0)::BIGINT AS revenue_rubles,
                       COALESCE(SUM(gb_amount), 0)::BIGINT AS total_gb
                   FROM traffic_purchases
                   WHERE created_at >= $1""",
                since,
            )
        except asyncpg.UndefinedTableError:
            return {"count": 0, "revenue_rubles": 0.0, "total_gb": 0, "by_method": []}

        by_method = []
        try:
            method_rows = await conn.fetch(
                """SELECT
                       COALESCE(payment_method, 'unknown') AS method,
                       COUNT(*)::BIGINT AS count,
                       COALESCE(SUM(price_rub), 0)::BIGINT AS revenue_rubles,
                       COALESCE(SUM(gb_amount), 0)::BIGINT AS total_gb
                   FROM traffic_purchases
                   WHERE created_at >= $1
                   GROUP BY method
                   ORDER BY revenue_rubles DESC""",
                since,
            )
            by_method = [
                {
                    "method": r["method"],
                    "count": int(r["count"]),
                    "revenue_rubles": int(r["revenue_rubles"]),
                    "total_gb": int(r["total_gb"]),
                }
                for r in method_rows
            ]
        except (asyncpg.UndefinedColumnError, asyncpg.UndefinedTableError):
            by_method = []

    return {
        "count": int(row["count"]) if row else 0,
        "revenue_rubles": int(row["revenue_rubles"]) if row else 0,
        "total_gb": int(row["total_gb"]) if row else 0,
        "by_method": by_method,
    }


async def get_purchase_breakdown() -> Dict[str, Any]:
    """Per-category purchase counts and revenue across time windows.

    Source: pending_purchases with status='paid' — the single table that
    covers both subscription purchases (finalize_purchase marks it paid) and
    notification-only products like the proxy (mark_pending_purchase_paid).

    Categories: basic, plus, basic_combo, plus_combo, proxy.
    Windows: 24h, 7d, 30d, 180d, 365d, all.

    created_at is the checkout-start time (no separate paid timestamp is
    stored), but payment completes within the ~15-min pending TTL, so it is
    an accurate proxy for windows of a day or more.

    Returns:
        { category: { window: {"count": int, "revenue": int_kopecks} } }
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    s24 = _to_db_utc(now - timedelta(hours=24))
    s7 = _to_db_utc(now - timedelta(days=7))
    s30 = _to_db_utc(now - timedelta(days=30))
    s180 = _to_db_utc(now - timedelta(days=180))
    s365 = _to_db_utc(now - timedelta(days=365))

    query = """
        WITH classified AS (
            SELECT
                CASE
                    WHEN purchase_type = 'proxy' THEN 'proxy'
                    WHEN purchase_type = 'subscription' AND tariff = 'basic'
                         AND COALESCE(is_combo, false) THEN 'basic_combo'
                    WHEN purchase_type = 'subscription' AND tariff = 'plus'
                         AND COALESCE(is_combo, false) THEN 'plus_combo'
                    WHEN purchase_type = 'subscription' AND tariff = 'basic' THEN 'basic'
                    WHEN purchase_type = 'subscription' AND tariff = 'plus'  THEN 'plus'
                    ELSE NULL
                END AS category,
                price_kopecks,
                created_at
            FROM pending_purchases
            WHERE status = 'paid'
        )
        SELECT
            category,
            COUNT(*) FILTER (WHERE created_at >= $1) AS c_24h,
            COUNT(*) FILTER (WHERE created_at >= $2) AS c_7d,
            COUNT(*) FILTER (WHERE created_at >= $3) AS c_30d,
            COUNT(*) FILTER (WHERE created_at >= $4) AS c_180d,
            COUNT(*) FILTER (WHERE created_at >= $5) AS c_365d,
            COUNT(*) AS c_all,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $1), 0) AS r_24h,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $2), 0) AS r_7d,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $3), 0) AS r_30d,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $4), 0) AS r_180d,
            COALESCE(SUM(price_kopecks) FILTER (WHERE created_at >= $5), 0) AS r_365d,
            COALESCE(SUM(price_kopecks), 0) AS r_all
        FROM classified
        WHERE category IS NOT NULL
        GROUP BY category
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, s24, s7, s30, s180, s365)

    windows = ["24h", "7d", "30d", "180d", "365d", "all"]
    result = {
        cat: {w: {"count": 0, "revenue": 0} for w in windows}
        for cat in ("basic", "plus", "basic_combo", "plus_combo", "proxy")
    }
    for row in rows:
        cat = row["category"]
        if cat not in result:
            continue
        for w in windows:
            result[cat][w] = {
                "count": row[f"c_{w}"] or 0,
                "revenue": row[f"r_{w}"] or 0,
            }
    return result


async def get_extended_bot_stats() -> Dict[str, Any]:
    """Расширенная статистика бота для мониторинга."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        now_db = _to_db_utc(now)

        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")

        # Active subscriptions
        active_subs = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at > $1", now_db
        )

        # Expired and not renewed (churn)
        expired_subs = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at <= $1", now_db
        )

        # Trial stats
        total_trial = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE trial_used_at IS NOT NULL"
        )

        # Conversion: users who have at least one subscription
        users_with_sub = await conn.fetchval(
            "SELECT COUNT(DISTINCT telegram_id) FROM subscriptions"
        )

        # Revenue (sum of approved payments)
        total_revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved'"
        ) or 0

        # Revenue last 30 days (MRR estimate)
        mrr_since = _to_db_utc(now - timedelta(days=30))
        mrr = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved' AND created_at >= $1",
            mrr_since
        ) or 0

        # New users today
        today_start = _to_db_utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
        new_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= $1", today_start
        )

        # Broadcasts sent
        total_broadcasts = await conn.fetchval("SELECT COUNT(*) FROM broadcasts")

        # Average subscriptions per paying user
        avg_subs = await conn.fetchval(
            "SELECT ROUND(AVG(cnt), 1) FROM (SELECT COUNT(*) as cnt FROM subscriptions GROUP BY telegram_id) sub"
        )

        conversion_rate = round((users_with_sub / total_users * 100), 1) if total_users > 0 else 0
        trial_rate = round((total_trial / total_users * 100), 1) if total_users > 0 else 0
        churn_rate = round((expired_subs / (active_subs + expired_subs) * 100), 1) if (active_subs + expired_subs) > 0 else 0

        return {
            "total_users": total_users or 0,
            "active_subs": active_subs or 0,
            "expired_subs": expired_subs or 0,
            "total_trial": total_trial or 0,
            "trial_rate": trial_rate,
            "users_with_sub": users_with_sub or 0,
            "conversion_rate": conversion_rate,
            "churn_rate": churn_rate,
            "total_revenue": total_revenue,
            "mrr": mrr,
            "new_today": new_today or 0,
            "total_broadcasts": total_broadcasts or 0,
            "avg_subs_per_user": float(avg_subs) if avg_subs else 0,
        }


async def get_all_users_telegram_ids() -> list:
    """Получить список всех Telegram ID пользователей"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM users")
        return [row["telegram_id"] for row in rows]


async def get_eligible_no_subscription_broadcast_users() -> list:
    """Get users eligible for no-subscription broadcast.
    Eligible = no active paid subscription, no active trial, is_reachable=TRUE.
    Returns list of dicts with telegram_id. Defensive: fallback if is_reachable missing.
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_eligible_no_subscription_broadcast_users skipped")
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        now = _to_db_utc(datetime.now(timezone.utc))
        query_with_reachable = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
              AND COALESCE(u.is_reachable, TRUE) = TRUE
        """
        fallback_query = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
        """
        try:
            rows = await conn.fetch(query_with_reachable, now)
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, no_sub_broadcast fallback")
            rows = await conn.fetch(fallback_query, now)
        return [{"telegram_id": row["telegram_id"]} for row in rows]


async def check_user_still_eligible_for_no_sub_broadcast(conn, telegram_id: int, now: datetime) -> bool:
    """Race-condition re-check before sending. Returns True if still eligible."""
    from database.subscriptions import get_active_paid_subscription
    paid = await get_active_paid_subscription(conn, telegram_id, now)
    if paid:
        return False
    try:
        row = await conn.fetchrow(
            "SELECT trial_expires_at, is_reachable FROM users WHERE telegram_id = $1",
            telegram_id
        )
    except asyncpg.UndefinedColumnError:
        row = await conn.fetchrow(
            "SELECT trial_expires_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
    if not row:
        return False
    trial_expires_at = row.get("trial_expires_at")
    if trial_expires_at:
        trial_expires_at_utc = _from_db_utc(trial_expires_at)
        now_utc = now if (getattr(now, "tzinfo", None) is not None) else datetime.now(timezone.utc)
        if trial_expires_at_utc > now_utc:
            return False
    is_reachable = row.get("is_reachable")
    if is_reachable is False:
        return False
    return True


async def insert_admin_broadcast_record(
    broadcast_type: str,
    total_recipients: int,
    success_count: int = 0,
    fail_count: int = 0
) -> Optional[int]:
    """Insert admin_broadcasts record. Returns id or None."""
    if not _core.DB_READY:
        return None
    try:
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO admin_broadcasts (type, total_recipients, success_count, fail_count)
                   VALUES ($1, $2, $3, $4) RETURNING id""",
                broadcast_type, total_recipients, success_count, fail_count
            )
            return row["id"] if row else None
    except asyncpg.UndefinedTableError:
        logger.debug("admin_broadcasts table not found, skipping audit")
        return None
    except Exception as e:
        logger.warning(f"Failed to insert admin_broadcast record: {e}")
        return None


async def update_admin_broadcast_record(broadcast_id: int, success_count: int, fail_count: int) -> None:
    """Update admin_broadcasts record after completion."""
    if not _core.DB_READY or broadcast_id is None:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE admin_broadcasts
                   SET success_count = $1, fail_count = $2 WHERE id = $3""",
                success_count, fail_count, broadcast_id
            )
    except Exception as e:
        logger.warning(f"Failed to update admin_broadcast record: {e}")


async def get_users_by_segment(segment: str) -> list:
    """Получить список Telegram ID пользователей по сегменту

    Args:
        segment: Сегмент получателей:
            - all_users            — все
            - active_subscriptions — активная подписка
            - no_subscription      — нет активной подписки (включая истёкшие)
            - no_remnawave         — никогда не имели entity в Remnawave
                                     (ни premium, ни bypass)
            - expired_1d / expired_2d / expired_3d — подписка истекла
                                     ровно N полных суток назад
                                     (и сейчас нет активной)
            - started_7d_cold      — холодные лиды: запустили бот за
                                     последние 7 суток (users.created_at)
                                     и до сих пор без активной подписки
                                     И без bypass-entity.
            - trial_ends_in_1d     — у юзера ИДЁТ триал и закончится
                                     в ближайшие 24 часа
                                     (trial_expires_at ∈ (NOW, NOW+24h])
            - trial_expired_6h / 1d / 2d / 3d
                                   — триал закончился N времени назад
                                     по фиксированному бакету:
                                       6h → [NOW-7h, NOW-6h)
                                       1d → [NOW-2d, NOW-1d)
                                       2d → [NOW-3d, NOW-2d)
                                       3d → [NOW-4d, NOW-3d)
                                     И сейчас нет активной подписки.
            - paid_expired_1d      — платная (subscriptions.source='payment')
                                     истекла ровно 1 сутки назад
                                     (expires_at ∈ [NOW-2d, NOW-1d))
                                     и сейчас нет активной подписки

    Returns:
        Список Telegram ID пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if segment == "all_users":
            rows = await conn.fetch("SELECT telegram_id FROM users")
            return [row["telegram_id"] for row in rows]
        elif segment == "active_subscriptions":
            now = _to_db_utc(datetime.now(timezone.utc))
            rows = await conn.fetch(
                """SELECT DISTINCT u.telegram_id
                   FROM users u
                   INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                   WHERE s.expires_at > $1""",
                now
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "no_subscription":
            now = _to_db_utc(datetime.now(timezone.utc))
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE NOT EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id AND s.expires_at > $1
                   )""",
                now
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "no_remnawave":
            # Users who never had ANY Remnawave entity — neither premium
            # nor bypass. They've never been provisioned on the panel.
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE NOT EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                         AND (s.remnawave_premium_uuid IS NOT NULL
                              OR s.remnawave_uuid IS NOT NULL)
                   )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "started_7d_cold":
            # Холодные лиды для прогрева: запустили бот не позже 7 суток
            # назад и до сих пор ничего не купили — ни подписку, ни
            # bypass-ГБ. Условия:
            #   1) users.created_at >= NOW() - 7 days  → свежий старт
            #   2) NO subscription row с expires_at > NOW()  → нет
            #      активной подписки
            #   3) NO subscription row с remnawave_uuid или
            #      remnawave_premium_uuid → не сидит на bypass-only
            #      ключах, оставшихся от триала / прошлой покупки.
            # 1 + 3 — то самое «никаких ключей вообще».
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.created_at >= NOW() - INTERVAL '7 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND (
                               s.expires_at > NOW()
                               OR s.remnawave_uuid IS NOT NULL
                               OR s.remnawave_premium_uuid IS NOT NULL
                           )
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_ends_in_1d":
            # Идёт триал, до конца ≤ 24 часа. Цель — пуш с напоминанием
            # «триал заканчивается, оформи подписку».
            #
            # ВАЖНО про tz: users.trial_expires_at — TIMESTAMP без TZ,
            # в БД хранится naive UTC (см. _to_db_utc). NOW() возвращает
            # TIMESTAMPTZ в session-TZ; implicit cast TIMESTAMP→TIMESTAMPTZ
            # интерпретирует TIMESTAMP в session-TZ и даёт сдвиг, если
            # session-TZ ≠ UTC. Используем `NOW() AT TIME ZONE 'UTC'` —
            # это TIMESTAMP-без-TZ в UTC, сравнение с trial_expires_at
            # надёжно без implicit cast в любой session-TZ.
            #
            # COALESCE: trial_expires_at добавлен в схему users позже,
            # чем trial_used_at. У старых триалов поле могло быть NULL.
            # Fallback на trial_used_at + 3 дня (продолжительность
            # триала — см. app/handlers/callbacks/subscription.py:143).
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.trial_used_at IS NOT NULL
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           >  (NOW() AT TIME ZONE 'UTC')
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '24 hours'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("trial_expired_6h", "trial_expired_1d", "trial_expired_2d", "trial_expired_3d"):
            # Триал закончился N времени назад (фиксированный бакет).
            # Без активной платной — иначе юзер уже купил, незачем
            # ему напоминание.
            #   trial_expired_6h → [NOW-7h, NOW-6h)
            #   trial_expired_1d → [NOW-2d, NOW-1d)
            #   trial_expired_2d → [NOW-3d, NOW-2d)
            #   trial_expired_3d → [NOW-4d, NOW-3d)
            # См. коммент про tz и COALESCE в trial_ends_in_1d.
            if segment == "trial_expired_6h":
                upper_sql = "(NOW() AT TIME ZONE 'UTC') - INTERVAL '6 hours'"
                lower_sql = "(NOW() AT TIME ZONE 'UTC') - INTERVAL '7 hours'"
            else:
                days = int(segment.split("_")[-1].rstrip("d"))
                upper_sql = f"(NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'"
                lower_sql = f"(NOW() AT TIME ZONE 'UTC') - INTERVAL '{days + 1} days'"
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.trial_used_at IS NOT NULL
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            <= {upper_sql}
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            >  {lower_sql}
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "paid_expired_1d":
            # Платная подписка (source='payment') истекла ровно
            # 1 сутки назад (бакет [NOW-2d, NOW-1d)). И сейчас нет
            # активной — это churn-окно, классическая точка реактивации.
            # См. коммент про tz в trial_ends_in_1d.
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                         AND s.source = 'payment'
                         AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'
                         AND s.expires_at >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 days'
                   )
                     AND NOT EXISTS (
                       SELECT 1 FROM subscriptions s2
                       WHERE s2.telegram_id = u.telegram_id
                         AND s2.expires_at > (NOW() AT TIME ZONE 'UTC')
                   )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("expired_1d", "expired_2d", "expired_3d"):
            # User's MOST RECENT subscription expired exactly N full days
            # ago (24-hour bucket). MAX(expires_at) делает выборку
            # устойчивой к history-rows (renewal flow создаёт несколько
            # subscription_row). Также неявно исключает юзеров с активной
            # подпиской — если их max в прошлом, активной нет.
            #
            # ВАЖНО про tz: см. коммент в trial_ends_in_1d. Используем
            # `(NOW() AT TIME ZONE 'UTC')` чтобы сравнение TIMESTAMP-без-TZ
            # работало стабильно в любой session-TZ.
            days = int(segment.split("_")[1].rstrip("d"))
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE (
                       SELECT MAX(s.expires_at) FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                   ) >= (NOW() AT TIME ZONE 'UTC') - $1 * INTERVAL '1 day'
                     AND (
                       SELECT MAX(s.expires_at) FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                   ) <  (NOW() AT TIME ZONE 'UTC') - $2 * INTERVAL '1 day'""",
                days + 1, days,
            )
            return [row["telegram_id"] for row in rows]
        else:
            logging.warning(f"Unknown segment: {segment}, returning empty list")
            return []


async def log_broadcast_send(broadcast_id: int, telegram_id: int, status: str, variant: str = None, message_id: int = None):
    """Записать результат отправки уведомления

    Args:
        broadcast_id: ID уведомления
        telegram_id: Telegram ID пользователя
        status: Статус отправки (sent | failed)
        variant: Вариант сообщения (A или B для A/B тестов)
        message_id: Telegram message_id отправленного сообщения (для удаления)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO broadcast_log (broadcast_id, telegram_id, status, variant, message_id)
               VALUES ($1, $2, $3, $4, $5)""",
            broadcast_id, telegram_id, status, variant, message_id
        )


async def get_broadcast_stats(broadcast_id: int) -> Dict[str, int]:
    """Получить статистику отправки уведомления
    
    Args:
        broadcast_id: ID уведомления
    
    Returns:
        Словарь с количеством отправленных и неудачных отправок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        sent_count = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND status = 'sent'",
            broadcast_id
        )
        failed_count = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND status = 'failed'",
            broadcast_id
        )
        return {"sent": sent_count or 0, "failed": failed_count or 0}


async def get_recent_broadcasts(limit: int = 10) -> list:
    """Get recent broadcasts for admin deletion UI."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT b.id, b.title, b.segment, b.created_at,
                      COUNT(bl.id) FILTER (WHERE bl.status = 'sent') AS sent_count,
                      COUNT(bl.id) FILTER (WHERE bl.message_id IS NOT NULL) AS has_msg_ids
               FROM broadcasts b
               LEFT JOIN broadcast_log bl ON bl.broadcast_id = b.id
               GROUP BY b.id
               ORDER BY b.id DESC
               LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]


async def get_broadcast_message_ids(broadcast_id: int) -> list:
    """Get all (telegram_id, message_id) pairs for a broadcast for bulk deletion."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, message_id FROM broadcast_log
               WHERE broadcast_id = $1 AND status = 'sent' AND message_id IS NOT NULL""",
            broadcast_id,
        )
        return [(r["telegram_id"], r["message_id"]) for r in rows]


async def mark_broadcast_messages_deleted(broadcast_id: int) -> None:
    """Mark broadcast messages as deleted after bulk deletion."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE broadcast_log SET status = 'deleted'
               WHERE broadcast_id = $1 AND status = 'sent' AND message_id IS NOT NULL""",
            broadcast_id,
        )


async def get_ab_test_broadcasts() -> list:
    """Получить список всех A/B тестов (уведомлений с is_ab_test = true)
    
    Returns:
        Список словарей с данными A/B тестов, отсортированных по created_at DESC
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, created_at 
               FROM broadcasts 
               WHERE is_ab_test = TRUE 
               ORDER BY created_at DESC"""
        )
        return [dict(row) for row in rows]


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


async def get_ab_test_stats(broadcast_id: int) -> Optional[Dict[str, Any]]:
    """Получить статистику A/B теста
    
    Args:
        broadcast_id: ID уведомления (должно быть A/B тестом)
    
    Returns:
        Словарь с статистикой:
        - variant_a_sent: количество отправок варианта A
        - variant_b_sent: количество отправок варианта B
        - variant_a_failed: количество неудачных отправок варианта A
        - variant_b_failed: количество неудачных отправок варианта B
        - total_sent: общее количество отправленных
        - total: общее количество (sent + failed)
        Или None, если данных недостаточно
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, что это A/B тест
        broadcast = await conn.fetchrow(
            "SELECT is_ab_test FROM broadcasts WHERE id = $1", broadcast_id
        )
        if not broadcast or not broadcast["is_ab_test"]:
            return None
        
        # Статистика по варианту A
        variant_a_sent = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'A' AND status = 'sent'",
            broadcast_id
        )
        variant_a_failed = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'A' AND status = 'failed'",
            broadcast_id
        )
        
        # Статистика по варианту B
        variant_b_sent = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'B' AND status = 'sent'",
            broadcast_id
        )
        variant_b_failed = await conn.fetchval(
            "SELECT COUNT(*) FROM broadcast_log WHERE broadcast_id = $1 AND variant = 'B' AND status = 'failed'",
            broadcast_id
        )
        
        variant_a_sent = variant_a_sent or 0
        variant_a_failed = variant_a_failed or 0
        variant_b_sent = variant_b_sent or 0
        variant_b_failed = variant_b_failed or 0
        
        total_sent = variant_a_sent + variant_b_sent
        total_failed = variant_a_failed + variant_b_failed
        total = total_sent + total_failed
        
        if total == 0:
            return None
        
        return {
            "variant_a_sent": variant_a_sent,
            "variant_b_sent": variant_b_sent,
            "variant_a_failed": variant_a_failed,
            "variant_b_failed": variant_b_failed,
            "total_sent": total_sent,
            "total": total
        }


async def admin_grant_access_atomic(telegram_id: int, days: int, admin_telegram_id: int, tariff: str = "basic") -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N дней (админ)

    Two-phase activation: Phase 1 add_vless_user outside tx, Phase 2 grant_access inside tx.
    Eliminates orphan UUID risk (no external call inside DB transaction).

    Args:
        telegram_id: Telegram ID пользователя
        days: Количество дней доступа (1, 7 или 14)
        admin_telegram_id: Telegram ID администратора
        tariff: "basic" или "plus" — тип тарифа для VPN API и подписки

    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)

    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
    from database.subscriptions import grant_access, _log_audit_event_atomic, _log_subscription_history_atomic

    duration = timedelta(days=days)
    now_pre = datetime.now(timezone.utc)
    subscription_end_pre = now_pre + duration

    pool = await get_pool()
    # Read existing sub once for the outer is_new_issuance heuristic. We
    # don't lock it — that happens inside the Phase 2 tx via grant_access.
    async with pool.acquire() as conn_pre:
        sub_row = await conn_pre.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
        outer_is_new_issuance = True
        if sub_row:
            sub = dict(sub_row)
            exp_raw = sub.get("expires_at")
            exp = _from_db_utc(exp_raw) if exp_raw else None
            outer_is_new_issuance = (
                sub.get("status") != "active" or not exp or exp <= now_pre or not sub.get("uuid")
            )
        tariff_normalized = (tariff or "basic").strip().lower()
        if tariff_normalized not in config.VALID_SUBSCRIPTION_TYPES:
            tariff_normalized = "basic"

    # Two-attempt loop. Attempt 1 trusts the outer `is_new_issuance` check.
    # Attempt 2 only runs if Phase 2 raised the invariant — i.e. grant_access
    # decided new issuance was needed even though the outer check said no
    # (race: a background worker expired the sub between the two reads, or
    # the row was stale). We force Phase 1 on the retry so pre_provisioned_uuid
    # is set when entering the tx again.
    last_error: Optional[BaseException] = None
    for attempt in (1, 2):
        force_provision = attempt == 2
        pre_provisioned_uuid = None
        uuid_to_cleanup_on_failure = None

        # PHASE 1 (outside DB transaction): Provision UUID via VPN API if needed
        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED:
            try:
                from app.services import purchase_flow
                vless_result = await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff=tariff_normalized,
                    subscription_end=subscription_end_pre,
                    period_days=days,
                    is_trial=False,
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "subscription_type": vless_result.get("subscription_type") or tariff_normalized,
                }
                if vless_result.get("vless_url_plus"):
                    pre_provisioned_uuid["vless_url_plus"] = vless_result["vless_url_plus"]
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"admin_grant_access_atomic: TWO_PHASE_PHASE1_DONE [user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}..., tariff={tariff_normalized}, attempt={attempt}]"
                )
            except Exception as phase1_err:
                # Loud, with traceback, so admin can diagnose Remnawave
                # outages directly from the bot logs without guessing.
                logger.error(
                    f"admin_grant_access_atomic: PHASE1_FAILED [user={telegram_id}, "
                    f"attempt={attempt}, error={phase1_err}]",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"VPN provisioning failed (Phase 1): {phase1_err}"
                ) from phase1_err

        # Defense in depth: if Phase 1 was supposed to run but didn't set
        # a UUID for any reason, bail out cleanly instead of letting the
        # invariant fire inside the tx with no actionable message.
        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED and not pre_provisioned_uuid:
            raise RuntimeError(
                f"Phase 1 produced no UUID for user {telegram_id} — refusing to enter tx"
            )

        ret_val = None
        grant_result_for_removal = None
        invariant_hit = False
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    grant_result_for_removal = result = await grant_access(
                        telegram_id=telegram_id,
                        duration=duration,
                        source="admin",
                        admin_telegram_id=admin_telegram_id,
                        admin_grant_days=days,
                        conn=conn,
                        pre_provisioned_uuid=pre_provisioned_uuid,
                        _caller_holds_transaction=True,
                        tariff=tariff_normalized,
                    )
                    expires_at = result["subscription_end"]
                    if result.get("vless_url"):
                        final_vpn_key = result["vless_url"]
                    else:
                        subscription_row = await conn.fetchrow(
                            "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                            telegram_id
                        )
                        if subscription_row and subscription_row.get("vpn_key"):
                            final_vpn_key = subscription_row["vpn_key"]
                        else:
                            final_vpn_key = result.get("uuid", "")
                    uuid_preview = f"{result['uuid'][:8]}..." if result.get('uuid') and len(result['uuid']) > 8 else (result.get('uuid') or "N/A")
                    logger.info(f"admin_grant_access_atomic: SUCCESS [admin={admin_telegram_id}, user={telegram_id}, days={days}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]")
                    ret_val = (expires_at, final_vpn_key)
                except RuntimeError as e:
                    last_error = e
                    if "INVARIANT_VIOLATION" in str(e) and attempt == 1 and not pre_provisioned_uuid:
                        # Race: outer check said no new issuance, but grant_access
                        # inside the locked tx decided otherwise. Retry once with
                        # forced Phase 1.
                        invariant_hit = True
                        logger.warning(
                            f"admin_grant_access_atomic: INVARIANT_HIT_RETRYING [user={telegram_id}] — "
                            "outer is_new_issuance was False but grant_access disagreed; "
                            "forcing Phase 1 on attempt 2"
                        )
                    else:
                        if uuid_to_cleanup_on_failure:
                            try:
                                await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                                logger.critical(
                                    f"ORPHAN_PREVENTED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason=admin_grant_access_atomic_tx_failed user={telegram_id} error={e}"
                                )
                            except Exception as remove_err:
                                logger.critical(
                                    f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason={remove_err} user={telegram_id}"
                                )
                        logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                        raise
                except Exception as e:
                    last_error = e
                    if uuid_to_cleanup_on_failure:
                        try:
                            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED uuid={uuid_preview} reason=admin_grant_access_atomic_tx_failed "
                                f"user={telegram_id} error={e}"
                            )
                        except Exception as remove_err:
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} user={telegram_id}"
                            )
                    logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                    raise

        if invariant_hit:
            # Loop body falls through, attempt=2 will force Phase 1.
            continue
        # ret_val is set when Phase 2 succeeded; break out of retry loop.
        if ret_val is not None:
            break

    if ret_val is None:
        # Both attempts failed. The exception from attempt 2 (or whatever
        # last bubbled) has already been re-raised above; getting here
        # means the retry loop fell through without a success. Surface
        # whatever error we saved.
        raise last_error or RuntimeError("admin_grant_access_atomic: unknown failure")
    if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("old_uuid_to_remove_after_commit"):
        old_uuid = grant_result_for_removal["old_uuid_to_remove_after_commit"]
        try:
            await vpn_utils.safe_remove_vless_user_with_retry(old_uuid)
            logger.info("OLD_UUID_REMOVED_AFTER_COMMIT", extra={"uuid": old_uuid[:8] + "..."})
        except Exception as e:
            logger.critical(
                "OLD_UUID_REMOVAL_FAILED_POST_COMMIT",
                extra={"uuid": old_uuid[:8] + "...", "error": str(e)[:200]}
            )
    if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_xray_sync_after_commit"):
        sync_info = grant_result_for_removal["renewal_xray_sync_after_commit"]
        try:
            from app.services import purchase_flow
            await purchase_flow.sync_renewal_to_remnawave(sync_info)
        except Exception as e:
            logger.critical(
                "RENEWAL_REMNAWAVE_SYNC_FAILED",
                extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
            )
    return ret_val


async def finalize_balance_purchase(
    telegram_id: int,
    tariff_type: str,
    period_days: int,
    amount_rubles: float,
    description: Optional[str] = None,
    promo_code: Optional[str] = None,
    country: Optional[str] = None
) -> Dict[str, Any]:
    """
    Атомарно обработать покупку подписки с баланса.
    
    Выполняет в одной транзакции:
    - Списывает баланс
    - Активирует подписку
    - Создает запись о платеже
    - Обрабатывает реферальный кешбэк
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff_type: Тип тарифа ('basic' или 'plus')
        period_days: Количество дней подписки
        amount_rubles: Сумма платежа в рублях
        description: Описание платежа (опционально)
        promo_code: Промокод (опционально, потребляется внутри транзакции)
    
    Returns:
        {
            "success": bool,
            "payment_id": Optional[int],
            "expires_at": Optional[datetime],
            "vpn_key": Optional[str],
            "is_renewal": bool,
            "new_balance": float,
            "referral_reward": Optional[Dict[str, Any]]
        }
    
    Raises:
        ValueError: При недостатке баланса или других бизнес-ошибках
        asyncpg exceptions: При финансовых ошибках (откат транзакции)
    """
    from database.subscriptions import grant_access
    from database.users import process_referral_reward

    if amount_rubles <= 0:
        raise ValueError(f"Invalid amount for balance purchase: {amount_rubles}")
    
    amount_kopecks = round(amount_rubles * 100)
    pool = await get_pool()
    
    if pool is None:
        raise RuntimeError("Database pool is not available")

    duration = timedelta(days=period_days)
    now_pre = datetime.now(timezone.utc)
    subscription_end_pre = now_pre + duration

    # PHASE 1 (outside DB transaction): Provision UUID via VPN API if new issuance needed
    pre_provisioned_uuid = None
    uuid_to_cleanup_on_failure = None
    async with pool.acquire() as conn_pre:
        sub_row = await conn_pre.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
        is_new_issuance = True
        if sub_row:
            sub = dict(sub_row)
            exp_raw = sub.get("expires_at")
            exp = _from_db_utc(exp_raw) if exp_raw else None
            is_new_issuance = (
                sub.get("status") != "active" or not exp or exp <= now_pre or not sub.get("uuid")
            )
        if is_new_issuance and config.VPN_ENABLED:
            try:
                # Task 2 cut-over: provision premium + bypass entities in
                # Remnawave; the legacy samopis xray master is no longer
                # called from the balance-purchase path.  Return shape
                # matches add_vless_user so Phase 2 (grant_access) is unchanged.
                from app.services import purchase_flow
                tariff_norm = (tariff_type or "basic").strip().lower()
                vless_result = await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff=tariff_norm,
                    subscription_end=subscription_end_pre,
                    period_days=period_days,
                    is_trial=False,
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "vless_url_plus": vless_result.get("vless_url_plus"),
                    "subscription_type": vless_result.get("subscription_type") or tariff_norm,
                }
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"finalize_balance_purchase: TWO_PHASE_PHASE1_DONE [user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}..., tariff={tariff_norm}]"
                )
            except Exception as phase1_err:
                logger.warning(
                    f"finalize_balance_purchase: Phase 1 provisioning failed: user={telegram_id}, error={phase1_err}"
                )
                pre_provisioned_uuid = None
                uuid_to_cleanup_on_failure = None
    
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_id
                )
                
                # STEP 1: Проверяем и списываем баланс (SELECT FOR UPDATE для блокировки строки)
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    raise ValueError(f"User {telegram_id} not found")
                
                current_balance = row["balance"]
                
                if current_balance < amount_kopecks:
                    raise ValueError(
                        f"Insufficient balance: {current_balance} < {amount_kopecks} "
                        f"(user={telegram_id}, required={amount_rubles:.2f} RUB)"
                    )
                
                # Списываем баланс
                new_balance = current_balance - amount_kopecks
                await conn.execute(
                    "UPDATE users SET balance = $1 WHERE telegram_id = $2",
                    new_balance, telegram_id
                )
                
                # Записываем транзакцию баланса
                transaction_description = description or f"Оплата подписки {tariff_type} на {period_days} дней"
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                       VALUES ($1, $2, $3, $4, $5)""",
                    telegram_id, -amount_kopecks, "subscription_payment", "subscription_payment", transaction_description
                )
                
                # STEP 1.5: Потребляем промокод (если был использован) - atomic UPDATE ... RETURNING
                if promo_code:
                    from database.subscriptions import _consume_promo_in_transaction
                    await _consume_promo_in_transaction(conn, promo_code, telegram_id, None)
                
                # STEP 2: Активируем подписку
                grant_result_for_removal = grant_result = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="payment",
                    admin_telegram_id=None,
                    admin_grant_days=None,
                    conn=conn,
                    pre_provisioned_uuid=pre_provisioned_uuid,
                    _caller_holds_transaction=True,
                    tariff=tariff_type or "basic",
                    country=country,
                )

                expires_at = grant_result["subscription_end"]
                vpn_key = grant_result.get("vless_url") or grant_result.get("vpn_key") or ""
                action = grant_result.get("action")
                is_renewal = action == "renewal"
                
                # expires_at is ALWAYS required (for both new and renewal)
                if not expires_at:
                    raise ValueError(
                        f"grant_access returned invalid result: expires_at={expires_at}"
                    )
                
                # vpn_key is required ONLY for new subscriptions (not for renewals)
                if action != "renewal" and not vpn_key:
                    raise ValueError(
                        "grant_access returned invalid result for NEW subscription: vpn_key is missing"
                    )
                
                # STEP 3: Создаем запись о платеже
                payment_id = await conn.fetchval(
                    "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                    telegram_id, f"{tariff_type}_{period_days}", amount_kopecks
                )
                
                if not payment_id:
                    raise ValueError(f"Failed to create payment record for user {telegram_id}")
                
                # STEP 4: Обрабатываем реферальный кешбэк
                purchase_id = f"balance_purchase_{payment_id}"
                referral_reward_result = None
                
                try:
                    referral_reward_result = await process_referral_reward(
                        buyer_id=telegram_id,
                        purchase_id=purchase_id,
                        amount_rubles=amount_rubles,
                        conn=conn
                    )
                except Exception as e:
                    # FINANCIAL errors propagate and rollback transaction
                    logger.error(
                        f"finalize_balance_purchase: Referral reward financial error "
                        f"(transaction will rollback): user={telegram_id}, purchase_id={purchase_id}, error={e}"
                    )
                    raise
                
                # STEP 5: Получаем новый баланс
                new_balance_kopecks = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                new_balance = (new_balance_kopecks or 0) / 100.0
                
                logger.info(
                    f"finalize_balance_purchase: SUCCESS [user={telegram_id}, payment_id={payment_id}, "
                    f"tariff={tariff_type}, period={period_days}, amount={amount_rubles:.2f} RUB, "
                    f"expires_at={expires_at.isoformat()}, is_renewal={is_renewal}, "
                    f"new_balance={new_balance:.2f} RUB, referral_reward_success={referral_reward_result.get('success') if referral_reward_result else False}]"
                )
                
                subscription_type_ret = (grant_result.get("subscription_type") or "basic").strip().lower()
                if subscription_type_ret not in config.VALID_SUBSCRIPTION_TYPES:
                    subscription_type_ret = "basic"
                vpn_key_plus_ret = grant_result.get("vpn_key_plus") or grant_result.get("vless_url_plus")
                ret_val = {
                    "success": True,
                    "payment_id": payment_id,
                    "expires_at": expires_at,
                    "vpn_key": vpn_key,
                    "vpn_key_plus": vpn_key_plus_ret,
                    "is_renewal": is_renewal,
                    "subscription_type": subscription_type_ret,
                    "new_balance": new_balance,
                    "referral_reward": referral_reward_result,
                    "is_basic_to_plus_upgrade": grant_result.get("is_basic_to_plus_upgrade", False),
                }
        except Exception as e:
            if uuid_to_cleanup_on_failure:
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                    logger.critical(
                        f"ORPHAN_PREVENTED uuid={uuid_preview} reason=finalize_balance_purchase_tx_failed "
                        f"user={telegram_id} error={e}"
                    )
                except Exception as remove_err:
                    uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                    logger.critical(
                        f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} user={telegram_id}"
                    )
            raise
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("old_uuid_to_remove_after_commit"):
            old_uuid = grant_result_for_removal["old_uuid_to_remove_after_commit"]
            try:
                await vpn_utils.safe_remove_vless_user_with_retry(old_uuid)
                logger.info("OLD_UUID_REMOVED_AFTER_COMMIT", extra={"uuid": old_uuid[:8] + "..."})
            except Exception as rem_err:
                logger.critical(
                    "OLD_UUID_REMOVAL_FAILED_POST_COMMIT",
                    extra={"uuid": old_uuid[:8] + "...", "error": str(rem_err)[:200]}
                )
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_xray_sync_after_commit"):
            sync_info = grant_result_for_removal["renewal_xray_sync_after_commit"]
            try:
                from app.services import purchase_flow
                await purchase_flow.sync_renewal_to_remnawave(sync_info)
            except Exception as e:
                logger.critical(
                    "RENEWAL_REMNAWAVE_SYNC_FAILED",
                    extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
                )
        return ret_val


async def finalize_balance_topup(
    telegram_id: int,
    amount_rubles: float,
    provider: str,
    provider_charge_id: str,
    description: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Атомарно обработать пополнение баланса с идемпотентностью.
    
    КРИТИЧЕСКИ ВАЖНО: Эта функция идемпотентна по provider_charge_id.
    Повторный вызов с тем же provider_charge_id НЕ увеличит баланс.
    
    Выполняет в одной транзакции:
    - Проверяет идемпотентность (по provider_charge_id)
    - Пополняет баланс (если не дубликат)
    - Создает запись о платеже
    - Обрабатывает реферальный кешбэк
    
    Args:
        telegram_id: Telegram ID пользователя
        amount_rubles: Сумма пополнения в рублях
        provider: Провайдер платежа ('telegram', 'platega', 'telegram_stars')
        provider_charge_id: Уникальный ID платежа от провайдера (для идемпотентности)
        description: Описание платежа (опционально)
        correlation_id: ID для корреляции логов (опционально)
    
    Returns:
        {
            "success": bool,
            "payment_id": Optional[int],
            "new_balance": float,
            "referral_reward": Optional[Dict[str, Any]],
            "reason": Optional[str]  # "already_processed" if duplicate
        }
    
    Raises:
        ValueError: При некорректной сумме или отсутствии provider_charge_id
        asyncpg exceptions: При финансовых ошибках (откат транзакции)
    """
    from database.users import process_referral_reward

    if amount_rubles <= 0:
        raise ValueError(f"Invalid amount for balance topup: {amount_rubles}")

    if not provider_charge_id:
        raise ValueError("provider_charge_id is required for idempotency")
    
    if provider not in ("telegram", "cryptobot", "platega", "crypto2328", "telegram_stars"):
        raise ValueError(f"Invalid provider: {provider}. Must be 'telegram', 'platega', or 'telegram_stars'")
    
    amount_kopecks = round(amount_rubles * 100)
    pool = await get_pool()
    
    if pool is None:
        raise RuntimeError("Database pool is not available")
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # STEP 1: SCHEMA SAFETY CHECK (P0 HOTFIX - prevent silent failures)
            # Defensive check: ensure idempotency columns exist before querying
            provider_column_map = {
                'telegram': 'telegram_payment_charge_id',
                'cryptobot': 'cryptobot_payment_id',
                'platega': 'platega_payment_id',
                'crypto2328': 'crypto2328_payment_id',
            }
            idempotency_column = provider_column_map[provider]
            column_exists = await conn.fetchval(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'payments'
                  AND column_name = $1
                """,
                idempotency_column
            )

            if not column_exists:
                error_msg = (
                    f"CRITICAL_SCHEMA_MISMATCH: payments.{idempotency_column} "
                    f"column missing. Migration may not have been applied correctly. "
                    f"Provider: {provider}, provider_charge_id: {provider_charge_id}"
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            # STEP 2: IDEMPOTENCY CHECK (CRITICAL - at the very start)
            existing_payment = await conn.fetchrow(
                """
                SELECT id, telegram_id, amount, status
                FROM payments
                WHERE telegram_payment_charge_id = $1
                   OR cryptobot_payment_id = $1
                   OR platega_payment_id = $1
                   OR crypto2328_payment_id = $1
                """,
                provider_charge_id
            )
            
            if existing_payment:
                logger.warning(
                    f"BALANCE_TOPUP_DUPLICATE_SKIPPED [provider={provider}, "
                    f"provider_charge_id={provider_charge_id}, telegram_id={telegram_id}, "
                    f"correlation_id={correlation_id}, existing_payment_id={existing_payment['id']}]"
                )
                # Return existing payment info without modifying balance
                existing_balance_kopecks = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                existing_balance = (existing_balance_kopecks or 0) / 100.0
                
                return {
                    "success": False,
                    "payment_id": existing_payment["id"],
                    "new_balance": existing_balance,
                    "referral_reward": None,
                    "reason": "already_processed"
                }
            
            # STEP 3: Проверяем существование пользователя
            user_exists = await conn.fetchval(
                "SELECT telegram_id FROM users WHERE telegram_id = $1", telegram_id
            )
            
            if user_exists is None:
                raise ValueError(f"User {telegram_id} not found")
            
            # STEP 4: ATOMIC INSERT + CREDIT (payment record FIRST, then balance)
            # Insert payment record with idempotency key
            payment_id = await conn.fetchval(
                """
                INSERT INTO payments (
                    telegram_id,
                    tariff,
                    amount,
                    status,
                    telegram_payment_charge_id,
                    cryptobot_payment_id,
                    platega_payment_id,
                    crypto2328_payment_id
                )
                VALUES (
                    $1, $2, $3, 'approved',
                    CASE WHEN $4 = 'telegram' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'cryptobot' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'platega' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'crypto2328' THEN $5 ELSE NULL END
                )
                RETURNING id
                """,
                telegram_id,
                "balance_topup",
                amount_kopecks,
                provider,
                provider_charge_id
            )
            
            if not payment_id:
                raise ValueError(f"Failed to create payment record for user {telegram_id}")
            
            # STEP 5: Пополняем баланс (AFTER payment record created)
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                amount_kopecks, telegram_id
            )
            
            # STEP 6: Записываем транзакцию баланса
            transaction_description = description or f"Пополнение баланса через {provider}"
            transaction_type = "topup"
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, transaction_type, provider, transaction_description
            )
            
            # STEP 7: Обрабатываем реферальный кешбэк
            purchase_id = f"balance_topup_{payment_id}"
            referral_reward_result = None
            
            try:
                referral_reward_result = await process_referral_reward(
                    buyer_id=telegram_id,
                    purchase_id=purchase_id,
                    amount_rubles=amount_rubles,
                    conn=conn
                )
            except Exception as e:
                # FINANCIAL errors propagate and rollback transaction
                logger.error(
                    f"finalize_balance_topup: Referral reward financial error "
                    f"(transaction will rollback): user={telegram_id}, purchase_id={purchase_id}, error={e}"
                )
                raise
            
            # STEP 8: Получаем новый баланс
            new_balance_kopecks = await conn.fetchval(
                "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
            )
            new_balance = (new_balance_kopecks or 0) / 100.0
            
            logger.info(
                f"BALANCE_TOPUP_SUCCESS [user={telegram_id}, payment_id={payment_id}, "
                f"provider={provider}, provider_charge_id={provider_charge_id}, "
                f"amount={amount_rubles:.2f} RUB, new_balance={new_balance:.2f} RUB, "
                f"referral_reward_success={referral_reward_result.get('success') if referral_reward_result else False}, "
                f"correlation_id={correlation_id}]"
            )
            
            return {
                "success": True,
                "payment_id": payment_id,
                "new_balance": new_balance,
                "referral_reward": referral_reward_result
            }


async def admin_grant_access_minutes_atomic(telegram_id: int, minutes: int, admin_telegram_id: int) -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N минут (админ)

    Two-phase activation: Phase 1 add_vless_user outside tx, Phase 2 grant_access inside tx.
    Eliminates orphan UUID risk (no external call inside DB transaction).

    Args:
        telegram_id: Telegram ID пользователя
        minutes: Количество минут доступа (например, 10)
        admin_telegram_id: Telegram ID администратора

    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)

    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
    from database.subscriptions import grant_access, _log_audit_event_atomic, _log_subscription_history_atomic

    duration = timedelta(minutes=minutes)
    now_pre = datetime.now(timezone.utc)
    subscription_end_pre = now_pre + duration

    pool = await get_pool()
    async with pool.acquire() as conn_pre:
        sub_row = await conn_pre.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
        outer_is_new_issuance = True
        if sub_row:
            sub = dict(sub_row)
            exp_raw = sub.get("expires_at")
            exp = _from_db_utc(exp_raw) if exp_raw else None
            outer_is_new_issuance = (
                sub.get("status") != "active" or not exp or exp <= now_pre or not sub.get("uuid")
            )

    # Two-attempt loop — same race-recovery pattern as the days-variant
    # of this function (see admin_grant_access_atomic above for the why).
    last_error: Optional[BaseException] = None
    ret_val = None
    grant_result_for_removal = None
    for attempt in (1, 2):
        force_provision = attempt == 2
        pre_provisioned_uuid = None
        uuid_to_cleanup_on_failure = None

        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED:
            try:
                from app.services import purchase_flow
                vless_result = await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff="basic",
                    subscription_end=subscription_end_pre,
                    period_days=max(1, minutes // 1440),
                    is_trial=False,
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "vless_url_plus": vless_result.get("vless_url_plus"),
                }
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"admin_grant_access_minutes_atomic: TWO_PHASE_PHASE1_DONE [user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}..., attempt={attempt}]"
                )
            except Exception as phase1_err:
                logger.error(
                    f"admin_grant_access_minutes_atomic: PHASE1_FAILED [user={telegram_id}, "
                    f"attempt={attempt}, error={phase1_err}]",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"VPN provisioning failed (Phase 1): {phase1_err}"
                ) from phase1_err

        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED and not pre_provisioned_uuid:
            raise RuntimeError(
                f"Phase 1 produced no UUID for user {telegram_id} — refusing to enter tx"
            )

        invariant_hit = False
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    grant_result_for_removal = result = await grant_access(
                        telegram_id=telegram_id,
                        duration=duration,
                        source="admin",
                        admin_telegram_id=admin_telegram_id,
                        admin_grant_days=None,
                        conn=conn,
                        pre_provisioned_uuid=pre_provisioned_uuid,
                        _caller_holds_transaction=True
                    )
                    expires_at = result["subscription_end"]
                    if result.get("vless_url"):
                        final_vpn_key = result["vless_url"]
                    else:
                        subscription_row = await conn.fetchrow(
                            "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                            telegram_id
                        )
                        if subscription_row and subscription_row.get("vpn_key"):
                            final_vpn_key = subscription_row["vpn_key"]
                        else:
                            final_vpn_key = result.get("uuid", "")
                    uuid_preview = f"{result['uuid'][:8]}..." if result.get('uuid') and len(result['uuid']) > 8 else (result.get('uuid') or "N/A")
                    logger.info(
                        f"admin_grant_access_minutes_atomic: SUCCESS [admin={admin_telegram_id}, user={telegram_id}, "
                        f"minutes={minutes}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]"
                    )
                    ret_val = (expires_at, final_vpn_key)
                except RuntimeError as e:
                    last_error = e
                    if "INVARIANT_VIOLATION" in str(e) and attempt == 1 and not pre_provisioned_uuid:
                        invariant_hit = True
                        logger.warning(
                            f"admin_grant_access_minutes_atomic: INVARIANT_HIT_RETRYING [user={telegram_id}] — "
                            "outer is_new_issuance was False but grant_access disagreed; "
                            "forcing Phase 1 on attempt 2"
                        )
                    else:
                        if uuid_to_cleanup_on_failure:
                            try:
                                await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                                logger.critical(
                                    f"ORPHAN_PREVENTED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason=admin_grant_access_minutes_atomic_tx_failed user={telegram_id} error={e}"
                                )
                            except Exception as remove_err:
                                logger.critical(
                                    f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason={remove_err} user={telegram_id}"
                                )
                        logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                        raise
                except Exception as e:
                    last_error = e
                    if uuid_to_cleanup_on_failure:
                        try:
                            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED uuid={uuid_preview} reason=admin_grant_access_minutes_tx_failed "
                                f"user={telegram_id} error={e}"
                            )
                        except Exception as remove_err:
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} user={telegram_id}"
                            )
                    logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                    raise

        if invariant_hit:
            continue
        if ret_val is not None:
            break

    if ret_val is None:
        raise last_error or RuntimeError("admin_grant_access_minutes_atomic: unknown failure")
    if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("old_uuid_to_remove_after_commit"):
        old_uuid = grant_result_for_removal["old_uuid_to_remove_after_commit"]
        try:
            await vpn_utils.safe_remove_vless_user_with_retry(old_uuid)
            logger.info("OLD_UUID_REMOVED_AFTER_COMMIT", extra={"uuid": old_uuid[:8] + "..."})
        except Exception as e:
            logger.critical(
                "OLD_UUID_REMOVAL_FAILED_POST_COMMIT",
                extra={"uuid": old_uuid[:8] + "...", "error": str(e)[:200]}
            )
    if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_xray_sync_after_commit"):
        sync_info = grant_result_for_removal["renewal_xray_sync_after_commit"]
        try:
            from app.services import purchase_flow
            await purchase_flow.sync_renewal_to_remnawave(sync_info)
        except Exception as e:
            logger.critical(
                "RENEWAL_REMNAWAVE_SYNC_FAILED",
                extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
            )
    return ret_val


async def admin_revoke_access_atomic(telegram_id: int, admin_telegram_id: int) -> bool:
    """Атомарно лишить доступа пользователя (админ)

    В одной транзакции:
    - удаляет UUID из Xray API (если есть uuid)
    - устанавливает status = 'expired', expires_at = NOW()
    - очищает uuid и vpn_key
    - записывает в subscription_history (action = admin_revoke)
    - записывает событие в audit_log

    Args:
        telegram_id: Telegram ID пользователя
        admin_telegram_id: Telegram ID администратора

    Returns:
        True если доступ был отозван, False если активной подписки не было
    """
    from database.subscriptions import _log_audit_event_atomic, _log_subscription_history_atomic

    pool = await get_pool()
    uuid_to_remove = None
    ret = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                now = datetime.now(timezone.utc)
                now_db = _to_db_utc(now)

                # 1. Проверяем, есть ли активная подписка (FOR UPDATE для блокировки)
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1 AND expires_at > $2 FOR UPDATE",
                    telegram_id, now_db
                )
                
                if not subscription_row:
                    logger.info(f"No active subscription to revoke for user {telegram_id}")
                    return False
                
                subscription = dict(subscription_row)
                old_expires_at = subscription["expires_at"]
                vpn_key = subscription.get("vpn_key", "")
                # PHASE 1: Capture UUID for removal OUTSIDE transaction (no VPN API call inside tx)
                uuid_to_remove = subscription.get("uuid") if subscription.get("uuid") else None
                
                # 2. Очищаем подписку: устанавливаем expires_at = NOW(), очищаем uuid и vpn_key
                await conn.execute(
                    "UPDATE subscriptions SET expires_at = $1, status = 'expired', uuid = NULL, vpn_key = NULL WHERE telegram_id = $2",
                    now_db, telegram_id
                )
                
                # 4. Записываем в историю подписок (используем старый vpn_key для истории, если был)
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key or "", now, now, "admin_revoke")
                
                # 5. Записываем событие в audit_log
                vpn_key_preview = vpn_key[:20] + "..." if vpn_key else "N/A"
                details = f"Revoked access, Old expires_at: {old_expires_at.isoformat()}, VPN key: {vpn_key_preview}"
                await _log_audit_event_atomic(conn, "admin_revoke", admin_telegram_id, telegram_id, details)
                
                logger.info(f"Admin {admin_telegram_id} revoked access for user {telegram_id}")
                ret = True
                
            except Exception as e:
                logger.exception(f"Error in admin_revoke_access_atomic for user {telegram_id}, transaction rolled back")
                raise
        # PHASE 2 (outside transaction): Remove UUID from Xray API
        if uuid_to_remove:
            try:
                await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
                logger.info("ADMIN_REVOKE_UUID_REMOVED", extra={"uuid": uuid_to_remove[:8] + "..."})
            except Exception as e:
                logger.critical(
                    "ADMIN_REVOKE_UUID_REMOVAL_FAILED",
                    extra={"uuid": uuid_to_remove[:8] + "...", "error": str(e)[:200]}
                )
    return ret


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================

async def get_user_discount(telegram_id: int, conn: Optional[asyncpg.Connection] = None) -> Optional[Dict[str, Any]]:
    """Получить активную персональную скидку пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        conn: Опциональное соединение (если передано — используется оно, без pool.acquire)
    
    Returns:
        Словарь с данными скидки или None, если скидки нет или она истекла
    """
    now = datetime.now(timezone.utc)
    if conn is not None:
        row = await conn.fetchrow(
            """SELECT * FROM user_discounts 
               WHERE telegram_id = $1 
               AND (expires_at IS NULL OR expires_at > $2)""",
            telegram_id, _to_db_utc(now)
        )
        return dict(row) if row else None
    pool = await get_pool()
    async with pool.acquire() as acquired:
        row = await acquired.fetchrow(
            """SELECT * FROM user_discounts 
               WHERE telegram_id = $1 
               AND (expires_at IS NULL OR expires_at > $2)""",
            telegram_id, _to_db_utc(now)
        )
        return dict(row) if row else None


async def create_user_discount(telegram_id: int, discount_percent: int, expires_at: Optional[datetime], created_by: int) -> bool:
    """Создать или обновить персональную скидку пользователя

    Args:
        telegram_id: Telegram ID пользователя
        discount_percent: Процент скидки (10, 15, 25, и т.д.)
        expires_at: Дата истечения скидки (None для бессрочной)
        created_by: Telegram ID администратора, создавшего скидку
    
    Returns:
        True если успешно, False в случае ошибки
    """
    from database.subscriptions import _log_audit_event_atomic

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO user_discounts (telegram_id, discount_percent, expires_at, created_by)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (telegram_id)
                   DO UPDATE SET discount_percent = $2, expires_at = $3, created_by = $4, created_at = CURRENT_TIMESTAMP""",
                telegram_id, discount_percent, _to_db_utc(expires_at) if expires_at else None, created_by
            )

            # Логируем создание/обновление скидки
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "бессрочно"
            details = f"Personal discount created/updated: {discount_percent}%, expires_at: {expires_str}"
            await _log_audit_event_atomic(conn, "admin_create_discount", created_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error creating user discount: {e}")
            return False


async def delete_user_discount(telegram_id: int, deleted_by: int) -> bool:
    """Удалить персональную скидку пользователя

    Args:
        telegram_id: Telegram ID пользователя
        deleted_by: Telegram ID администратора, удалившего скидку

    Returns:
        True если успешно, False в случае ошибки
    """
    from database.subscriptions import _log_audit_event_atomic

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            # Проверяем, есть ли скидка
            existing = await conn.fetchrow(
                "SELECT * FROM user_discounts WHERE telegram_id = $1",
                telegram_id
            )
            
            if not existing:
                return False
            
            # Удаляем скидку
            await conn.execute(
                "DELETE FROM user_discounts WHERE telegram_id = $1",
                telegram_id
            )
            
            # Логируем удаление скидки
            discount_percent = existing["discount_percent"]
            details = f"Personal discount deleted: {discount_percent}%"
            await _log_audit_event_atomic(conn, "admin_delete_discount", deleted_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error deleting user discount: {e}")
            return False


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С VIP-СТАТУСОМ ====================

async def is_vip_user(telegram_id: int, conn: Optional[asyncpg.Connection] = None) -> bool:
    """Проверить, является ли пользователь VIP
    
    Args:
        telegram_id: Telegram ID пользователя
        conn: Опциональное соединение (если передано — используется оно, без pool.acquire)
    
    Returns:
        True если пользователь VIP, False иначе
    """
    if conn is not None:
        row = await conn.fetchrow(
            "SELECT telegram_id FROM vip_users WHERE telegram_id = $1",
            telegram_id
        )
        return row is not None
    pool = await get_pool()
    async with pool.acquire() as acquired:
        row = await acquired.fetchrow(
            "SELECT telegram_id FROM vip_users WHERE telegram_id = $1",
            telegram_id
        )
        return row is not None


async def grant_vip_status(telegram_id: int, granted_by: int) -> bool:
    """Назначить VIP-статус пользователю

    Args:
        telegram_id: Telegram ID пользователя
        granted_by: Telegram ID администратора, назначившего VIP

    Returns:
        True если успешно, False в случае ошибки
    """
    from database.subscriptions import _log_audit_event_atomic

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO vip_users (telegram_id, granted_by)
                   VALUES ($1, $2)
                   ON CONFLICT (telegram_id) 
                   DO UPDATE SET granted_by = $2, granted_at = CURRENT_TIMESTAMP""",
                telegram_id, granted_by
            )
            
            # Логируем назначение VIP
            details = f"VIP status granted to user {telegram_id}"
            await _log_audit_event_atomic(conn, "vip_granted", granted_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error granting VIP status: {e}")
            return False


async def revoke_vip_status(telegram_id: int, revoked_by: int) -> bool:
    """Отозвать VIP-статус у пользователя

    Args:
        telegram_id: Telegram ID пользователя
        revoked_by: Telegram ID администратора, отозвавшего VIP

    Returns:
        True если успешно, False в случае ошибки
    """
    from database.subscriptions import _log_audit_event_atomic

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            # Проверяем, есть ли VIP-статус
            existing = await conn.fetchrow(
                "SELECT telegram_id FROM vip_users WHERE telegram_id = $1",
                telegram_id
            )
            
            if not existing:
                return False
            
            # Удаляем VIP-статус
            await conn.execute(
                "DELETE FROM vip_users WHERE telegram_id = $1",
                telegram_id
            )
            
            # Логируем отзыв VIP
            details = f"VIP status revoked from user {telegram_id}"
            await _log_audit_event_atomic(conn, "vip_revoked", revoked_by, telegram_id, details)
            
            return True
        except Exception as e:
            logger.exception(f"Error revoking VIP status: {e}")
            return False


# ============================================================================
# ФИНАНСОВАЯ АНАЛИТИКА
# ============================================================================

async def get_total_revenue() -> float:
    """
    Получить общий доход от всех успешных платежей
    
    Returns:
        Общий доход в рублях (только утвержденные платежи)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все утвержденные платежи
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def get_paying_users_count() -> int:
    """
    Получить количество платящих пользователей
    
    Returns:
        Количество уникальных пользователей с хотя бы одним утвержденным платежом
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        return count


async def get_user_ltv(telegram_id: int) -> float:
    """
    Получить LTV (Lifetime Value) пользователя
    
    LTV = общая сумма платежей за подписки (исключая кешбэк)
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Суммируем все утвержденные платежи за подписки
        total_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'""",
            telegram_id
        ) or 0
        
        return total_kopecks / 100.0  # Конвертируем из копеек в рубли


async def get_average_ltv() -> float:
    """
    Получить средний LTV по всем пользователям
    
    Returns:
        Средний LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем LTV для каждого пользователя
        ltv_data = await conn.fetch(
            """SELECT telegram_id, COALESCE(SUM(amount), 0) as total_payments
               FROM payments
               WHERE status = 'approved'
               GROUP BY telegram_id"""
        )
        
        if not ltv_data:
            return 0.0
        
        total_ltv = sum(row["total_payments"] for row in ltv_data)
        avg_ltv = total_ltv / len(ltv_data)
        
        return avg_ltv / 100.0  # Конвертируем из копеек в рубли


async def get_arpu() -> float:
    """
    Получить ARPU (Average Revenue Per User)
    
    ARPU = общий доход / количество платящих пользователей
    
    Returns:
        ARPU в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Общий доход (только утвержденные платежи)
        total_revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        total_revenue = total_revenue_kopecks / 100.0
        
        # Количество платящих пользователей
        paying_users_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT telegram_id) 
               FROM payments 
               WHERE status = 'approved'"""
        ) or 0
        
        # ARPU = общий доход / платящие пользователи
        arpu = total_revenue / paying_users_count if paying_users_count > 0 else 0.0
        
        return arpu


async def get_ltv() -> float:
    """
    Получить средний LTV (Lifetime Value) по всем платящим пользователям
    
    LTV = средняя сумма всех платежей пользователя за подписки
    
    Returns:
        Средний LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем средний LTV через агрегацию (оптимизированный запрос)
        avg_ltv_kopecks = await conn.fetchval(
            """SELECT COALESCE(AVG(user_total), 0)
               FROM (
                   SELECT telegram_id, SUM(amount) as user_total
                   FROM payments
                   WHERE status = 'approved'
                   GROUP BY telegram_id
               ) as user_ltvs"""
        ) or 0
        
        # PART D.8: Fix Decimal arithmetic bug
        # avg_ltv_kopecks may be Decimal from PostgreSQL
        # Use float() conversion to avoid TypeError: unsupported operand type(s) for /: 'Decimal' and 'float'
        return float(avg_ltv_kopecks) / 100.0  # Конвертируем из копеек в рубли


async def get_referral_analytics() -> Dict[str, Any]:
    """
    Получить реферальную аналитику
    
    Returns:
        Словарь с ключами:
        - referral_revenue: доход от рефералов (сумма платежей приглашенных пользователей)
        - cashback_paid: выплаченный кешбэк
        - net_profit: чистая прибыль (referral_revenue - cashback_paid)
        - referred_users_count: количество приглашенных пользователей
        - active_referrals: количество активных рефералов
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Доход от рефералов: сумма всех платежей пользователей, у которых есть referrer_id
            referral_revenue_kopecks = await conn.fetchval(
                """SELECT COALESCE(SUM(p.amount), 0)
                   FROM payments p
                   JOIN users u ON p.telegram_id = u.telegram_id
                   WHERE p.status = 'approved'
                   AND (u.referrer_id IS NOT NULL OR u.referred_by IS NOT NULL)"""
            ) or 0

            referral_revenue = referral_revenue_kopecks / 100.0

            # Выплаченный кешбэк (сумма всех транзакций типа cashback)
            cashback_paid_kopecks = await conn.fetchval(
                """SELECT COALESCE(SUM(amount), 0)
                   FROM balance_transactions
                   WHERE type = 'cashback'"""
            ) or 0

            cashback_paid = cashback_paid_kopecks / 100.0

            # Чистая прибыль
            net_profit = referral_revenue - cashback_paid

            # Количество приглашенных пользователей
            referred_users_count = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals"
            ) or 0

            # Количество активных рефералов (с активной подпиской)
            active_referrals = await conn.fetchval(
                """SELECT COUNT(DISTINCT r.referred_user_id)
                   FROM referrals r
                   JOIN subscriptions s ON r.referred_user_id = s.telegram_id
                   WHERE s.expires_at > NOW()"""
            ) or 0

            return {
                "referral_revenue": referral_revenue,
                "cashback_paid": cashback_paid,
                "net_profit": net_profit,
                "referred_users_count": referred_users_count,
                "active_referrals": active_referrals,
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping referral analytics: {e}")
        return {
            "referral_revenue": 0.0,
            "cashback_paid": 0.0,
            "net_profit": 0.0,
            "referred_users_count": 0,
            "active_referrals": 0
        }
    except Exception as e:
        logger.warning(f"Error getting referral analytics: {e}")
        return {
            "referral_revenue": 0.0,
            "cashback_paid": 0.0,
            "net_profit": 0.0,
            "referred_users_count": 0,
            "active_referrals": 0
        }


async def get_daily_summary(date: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Получить ежедневную сводку

    Args:
        date: Дата для сводки (если None, используется сегодня)

    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    _empty = {"date": "", "revenue": 0.0, "payments_count": 0, "new_users": 0, "new_subscriptions": 0}
    if not _core.DB_READY:
        logger.warning("DB not ready, get_daily_summary skipped")
        return _empty

    if date is None:
        date = datetime.now(timezone.utc)

    start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)

    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_daily_summary skipped")
        return _empty
    async with pool.acquire() as conn:
        start_naive = _to_db_utc(start_date)
        end_naive = _to_db_utc(end_date)
        # Доход за день (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*)
               FROM subscriptions
               WHERE activated_at >= $1 AND activated_at < $2""",
            start_naive, end_naive
        ) or 0

        return {
            "date": start_date.strftime("%Y-%m-%d"),
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }


async def get_monthly_summary(year: int, month: int) -> Dict[str, Any]:
    """
    Получить ежемесячную сводку
    
    Args:
        year: Год
        month: Месяц (1-12)
    
    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        start_naive = _to_db_utc(start_date)
        end_naive = _to_db_utc(end_date)
        # Доход за месяц (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(amount), 0) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM payments 
               WHERE status = 'approved' 
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*)
               FROM subscriptions
               WHERE activated_at >= $1 AND activated_at < $2""",
            start_naive, end_naive
        ) or 0

        return {
            "year": year,
            "month": month,
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }


async def admin_delete_user_complete(telegram_id: int, admin_telegram_id: int) -> bool:
    """Полное удаление пользователя из БД (все данные).

    В одной транзакции:
    - Удаляет UUID из Xray API (если есть)
    - Удаляет записи из: promo_usage_logs, user_discounts, vip_users,
      referral_rewards, referrals, balance_transactions, subscription_history,
      pending_purchases, payments, subscriptions, broadcast_log, users

    Lazy import: _log_audit_event_atomic from database.subscriptions
    - Записывает событие в audit_log

    Args:
        telegram_id: Telegram ID удаляемого пользователя
        admin_telegram_id: Telegram ID администратора

    Returns:
        True если пользователь был удалён, False если не найден
    """
    from database.subscriptions import _log_audit_event_atomic

    pool = await get_pool()
    uuid_to_remove = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Проверяем существование пользователя
            user_row = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1 FOR UPDATE", telegram_id
            )
            if not user_row:
                return False

            # Получаем UUID из подписки для удаления из Xray
            sub_row = await conn.fetchrow(
                "SELECT uuid FROM subscriptions WHERE telegram_id = $1", telegram_id
            )
            if sub_row and sub_row.get("uuid"):
                uuid_to_remove = sub_row["uuid"]

            # Удаляем все связанные данные (порядок важен для FK constraints)
            await conn.execute("DELETE FROM promo_usage_logs WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM user_discounts WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM vip_users WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM referral_rewards WHERE referrer_id = $1 OR buyer_id = $1", telegram_id)
            await conn.execute("DELETE FROM referrals WHERE referrer_user_id = $1 OR referred_user_id = $1", telegram_id)
            await conn.execute("DELETE FROM balance_transactions WHERE user_id = $1", telegram_id)
            await conn.execute("DELETE FROM subscription_history WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM pending_purchases WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM payments WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM broadcast_log WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM traffic_purchases WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM subscriptions WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM users WHERE telegram_id = $1", telegram_id)

            # Записываем в audit_log
            await _log_audit_event_atomic(
                conn, "admin_delete_user", admin_telegram_id, telegram_id,
                f"Complete user deletion from DB"
            )

            logger.info(f"Admin {admin_telegram_id} deleted user {telegram_id} completely from DB")

    # PHASE 2 (outside transaction): Remove UUID from Xray API
    if uuid_to_remove:
        try:
            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
            logger.info(f"ADMIN_DELETE_UUID_REMOVED uuid={uuid_to_remove[:8]}...")
        except Exception as e:
            logger.error(f"ADMIN_DELETE_UUID_REMOVAL_FAILED uuid={uuid_to_remove[:8]}... error={e}")

    # Delete Remnawave user (fire-and-forget)
    try:
        from app.services.remnawave_service import delete_remnawave_user_bg
        delete_remnawave_user_bg(telegram_id)
    except Exception as rmn_err:
        logger.warning("REMNAWAVE_ADMIN_DELETE_FAIL: tg=%s %s", telegram_id, rmn_err)

    return True


# ====================================================================================
# GIFT SUBSCRIPTIONS: Подарочные подписки
# ====================================================================================

def generate_gift_code() -> str:
    """Генерирует уникальный код подарочной подписки (12 символов, alphanumeric)."""
    import secrets
    import string
    alphabet = string.ascii_uppercase + string.digits
    # Убираем похожие символы для удобства: O/0, I/1/L
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "").replace("L", "")
    return "".join(secrets.choice(alphabet) for _ in range(12))


async def create_gift_subscription(
    buyer_telegram_id: int,
    tariff: str,
    period_days: int,
    price_kopecks: int,
    purchase_id: str,
) -> Dict[str, Any]:
    """
    Создаёт подарочную подписку после оплаты.

    Returns:
        {"gift_code": str, "id": int}
    """
    pool = await get_pool()
    gift_code = generate_gift_code()
    now = datetime.now(timezone.utc)
    # Подарок действителен 90 дней для активации
    gift_expires = now + timedelta(days=90)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO gift_subscriptions
               (gift_code, buyer_telegram_id, tariff, period_days, price_kopecks,
                purchase_id, status, created_at, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, 'paid', $7, $8)
               RETURNING id, gift_code""",
            gift_code, buyer_telegram_id, tariff, period_days, price_kopecks,
            purchase_id, _to_db_utc(now), _to_db_utc(gift_expires),
        )
    logger.info(
        f"GIFT_CREATED buyer={buyer_telegram_id} code={gift_code} "
        f"tariff={tariff} period={period_days}d"
    )
    return {"gift_code": row["gift_code"], "id": row["id"]}


async def get_gift_subscription(gift_code: str) -> Optional[Dict[str, Any]]:
    """Получает подарочную подписку по коду."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM gift_subscriptions WHERE gift_code = $1",
            gift_code.upper().strip(),
        )
    if row is None:
        return None
    d = dict(row)
    for k in ("created_at", "expires_at", "activated_at"):
        if k in d and d[k] is not None and isinstance(d[k], datetime):
            d[k] = _from_db_utc(d[k])
    return d


async def activate_gift_subscription(gift_code: str, activated_by: int) -> Dict[str, Any]:
    """
    Активирует подарочную подписку для пользователя.

    Двухфазная активация:
    - Phase 1: Проверяем подписку, при необходимости создаём UUID через VPN API (вне транзакции)
    - Phase 2: Атомарно обновляем подарок + выдаём доступ через grant_access (внутри транзакции)

    Returns:
        {"success": bool, "error": str | None, "tariff": str, "period_days": int}
    """
    from database.subscriptions import grant_access
    from database.users import process_referral_reward

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # =========================================================================
    # PRE-CHECK: Валидация подарка (без блокировки — быстрая проверка)
    # =========================================================================
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM gift_subscriptions WHERE gift_code = $1",
            gift_code.upper().strip(),
        )
    if row is None:
        return {"success": False, "error": "not_found"}

    gift = dict(row)
    if gift["status"] == "activated":
        return {"success": False, "error": "already_activated"}
    if gift["status"] != "paid":
        return {"success": False, "error": "invalid_status"}

    expires_at = _from_db_utc(gift["expires_at"]) if gift["expires_at"] else None
    if expires_at and expires_at < now:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE gift_subscriptions SET status = 'expired' WHERE id = $1",
                gift["id"],
            )
        return {"success": False, "error": "expired"}

    if gift["buyer_telegram_id"] == activated_by:
        return {"success": False, "error": "self_activation"}

    tariff = gift["tariff"]
    period_days = gift["period_days"]
    duration = timedelta(days=period_days)

    # =========================================================================
    # PHASE 1: Провизия VPN UUID вне транзакции (если нужна новая выдача)
    # =========================================================================
    pre_provisioned = None
    if config.VPN_ENABLED:
        async with pool.acquire() as conn:
            sub_row = await conn.fetchrow(
                "SELECT status, expires_at, uuid FROM subscriptions WHERE telegram_id = $1",
                activated_by,
            )
        needs_new_issuance = True
        if sub_row:
            sub_expires = _ensure_utc(sub_row["expires_at"]) if sub_row["expires_at"] else None
            if (
                sub_row["status"] == "active"
                and sub_expires
                and sub_expires > now
                and sub_row["uuid"]
            ):
                needs_new_issuance = False

        if needs_new_issuance:
            subscription_end = now + duration
            # Task 2 cut-over: provision premium + bypass entities in
            # Remnawave instead of the legacy samopis xray master.
            from app.services import purchase_flow
            vless_result = await purchase_flow.provision_subscription(
                activated_by,
                tariff=tariff,
                subscription_end=subscription_end,
                period_days=period_days,
                is_trial=False,
            )
            pre_provisioned = {
                "uuid": vless_result["uuid"],
                "vless_url": vless_result["vless_url"],
                "vless_url_plus": vless_result.get("vless_url_plus"),
                "subscription_type": vless_result.get("subscription_type", tariff),
            }

    # =========================================================================
    # PHASE 2: Атомарная транзакция — обновление подарка + выдача доступа
    # =========================================================================
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Повторная проверка с блокировкой (защита от race condition)
            row = await conn.fetchrow(
                "SELECT * FROM gift_subscriptions WHERE gift_code = $1 FOR UPDATE",
                gift_code.upper().strip(),
            )
            if row is None:
                return {"success": False, "error": "not_found"}

            gift = dict(row)
            if gift["status"] != "paid":
                return {"success": False, "error": "already_activated" if gift["status"] == "activated" else "invalid_status"}

            # Помечаем подарок как активированный
            await conn.execute(
                """UPDATE gift_subscriptions
                   SET status = 'activated', activated_by = $1, activated_at = $2
                   WHERE id = $3""",
                activated_by, _to_db_utc(now), gift["id"],
            )

            # Активируем подписку через grant_access
            grant_result = await grant_access(
                telegram_id=activated_by,
                duration=duration,
                source="gift",
                tariff=tariff,
                conn=conn,
                _caller_holds_transaction=True,
                pre_provisioned_uuid=pre_provisioned,
            )

    logger.info(
        f"GIFT_ACTIVATED code={gift_code} by={activated_by} "
        f"tariff={tariff} period={period_days}d buyer={gift['buyer_telegram_id']}"
    )
    return {
        "success": True,
        "error": None,
        "tariff": tariff,
        "period_days": period_days,
        "grant_result": grant_result,
    }


async def get_user_gifts(telegram_id: int) -> list:
    """Получает список подарков, купленных пользователем."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM gift_subscriptions
               WHERE buyer_telegram_id = $1
               ORDER BY created_at DESC LIMIT 20""",
            telegram_id,
        )
    return [dict(r) for r in rows]


# ====================================================================
# RECOVERY: rollback of premium expireAt accidentally pushed to ~2036
#
# Bug (introduced by the admin reconcile tool): bypass-only rows in
# `subscriptions` carry expires_at = NOW + 10 years AND the original
# remnawave_premium_uuid (it's never cleared on transition). The earlier
# version of the scan treated them as active premium and PATCHed the
# panel's expireAt to 2036 — granting users a decade of free premium.
#
# To roll back, we need each user's REAL last paid premium end date,
# computed from pending_purchases (paid status, non-bypass tariff).
# ====================================================================

async def get_premium_recovery_candidates() -> list:
    """Users whose bypass-only row is still pinned to a premium uuid AND
    whose expires_at is parked in the far future (the 10-year marker).

    Returns dicts with: telegram_id, remnawave_premium_uuid, db_expires_at.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_uuid, expires_at
               FROM subscriptions
               WHERE is_bypass_only = TRUE
                 AND remnawave_premium_uuid IS NOT NULL
                 AND expires_at > NOW() + INTERVAL '5 years'"""
        )
    return [dict(r) for r in rows]


async def get_user_paid_subscription_history(telegram_id: int) -> list:
    """Chronological list of the user's PAID subscription purchases
    (excludes balance top-ups, traffic packs, and pending/expired rows).

    Returns list of {created_at, period_days, tariff} in ascending order.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT created_at, period_days, tariff
               FROM pending_purchases
               WHERE telegram_id = $1
                 AND status = 'paid'
                 AND period_days > 0
                 AND tariff IN ('basic', 'plus', 'biz_starter', 'biz_team',
                                'biz_business', 'biz_pro', 'biz_enterprise',
                                'biz_ultimate')
               ORDER BY created_at ASC""",
            telegram_id,
        )
    return [dict(r) for r in rows]


async def get_paid_subscription_history_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch paid subscription history for many users in ONE query.

    Returns a dict: telegram_id -> [{created_at, period_days, tariff}, ...]
    sorted ascending by created_at. Missing users get an empty list.

    Used by the premium recovery scan instead of 1k+ separate roundtrips.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, created_at, period_days, tariff
               FROM pending_purchases
               WHERE telegram_id = ANY($1::bigint[])
                 AND status = 'paid'
                 AND period_days > 0
                 AND tariff IN ('basic', 'plus', 'biz_starter', 'biz_team',
                                'biz_business', 'biz_pro', 'biz_enterprise',
                                'biz_ultimate')
               ORDER BY telegram_id, created_at ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]].append({
            "created_at": r["created_at"],
            "period_days": r["period_days"],
            "tariff": r["tariff"],
        })
    return out


async def get_activated_gifts_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch activated gift subscriptions for users in ONE query.

    Returns dict: telegram_id -> [{activated_at, period_days}, ...]
    Ascending by activated_at. Missing users get empty list.

    Recovery uses this to honour gift subscriptions when computing
    real premium end date — paid history might be empty but a real
    gift still grants premium time.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT activated_by, activated_at, period_days
               FROM gift_subscriptions
               WHERE activated_by = ANY($1::bigint[])
                 AND status = 'activated'
                 AND activated_at IS NOT NULL
                 AND period_days > 0
               ORDER BY activated_by, activated_at ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["activated_by"]].append({
            "activated_at": r["activated_at"],
            "period_days": r["period_days"],
        })
    return out


async def get_max_subscription_end_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch the user's MAX(subscription_history.end_date) per user.

    subscription_history is the source-of-truth ledger for every
    subscription event — purchases, renewals, gifts, admin grants —
    so the maximum end_date is the user's actual last legitimate
    premium expiry, regardless of which acquisition path they came
    through. Recovery uses this as the primary signal instead of
    reconstructing dates from pending_purchases.

    Returns dict: telegram_id -> datetime | None.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, MAX(end_date) AS last_end
               FROM subscription_history
               WHERE telegram_id = ANY($1::bigint[])
               GROUP BY telegram_id""",
            telegram_ids,
        )
    out: dict = {tg: None for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]] = r["last_end"]
    return out


async def get_paid_payments_via_purchases_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch settled `payments` rows joined onto pending_purchases.

    A user paid through a provider can have rows in `payments` even
    when pending_purchases status didn't flip to 'paid' for some reason
    (legacy flows, admin approve, edge-case webhooks). Joining on
    purchase_id reconstructs period_days from pending_purchases so we
    can still compute an end date.

    Returns dict: telegram_id -> [{created_at, period_days, tariff}, ...]
    ordered ascending. Used as belt-and-suspenders fallback in recovery.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.telegram_id,
                      COALESCE(p.paid_at, p.created_at) AS created_at,
                      pp.period_days,
                      COALESCE(p.tariff, pp.tariff) AS tariff
               FROM payments p
               LEFT JOIN pending_purchases pp ON pp.purchase_id = p.purchase_id
               WHERE p.telegram_id = ANY($1::bigint[])
                 AND p.status IN ('paid', 'approved')
                 AND pp.period_days IS NOT NULL
                 AND pp.period_days > 0
                 AND COALESCE(p.tariff, pp.tariff) IN
                     ('basic', 'plus', 'biz_starter', 'biz_team',
                      'biz_business', 'biz_pro', 'biz_enterprise',
                      'biz_ultimate')
               ORDER BY p.telegram_id, COALESCE(p.paid_at, p.created_at) ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]].append({
            "created_at": r["created_at"],
            "period_days": r["period_days"],
            "tariff": r["tariff"],
        })
    return out



async def get_active_premium_subscribers() -> list:
    """All subscriptions currently considered active premium (NOT bypass-only).

    For the audit-tool: we want users whose premium subscription is
    nominally active in the bot's DB so we can cross-check it against
    payments and the Remnawave panel.

    Filters:
      - status='active' AND expires_at > NOW (still in their paid window)
      - NOT is_bypass_only (we never audit bypass-only rows, those are
        traffic-pack only and live on +10y by design)
      - subscription_type in the real premium tariffs

    Returns list of dicts: telegram_id, remnawave_premium_uuid,
    expires_at, subscription_type.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_uuid,
                      expires_at, subscription_type
               FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > NOW()
                 AND COALESCE(is_bypass_only, FALSE) = FALSE
                 AND subscription_type IN
                     ('basic', 'plus', 'biz_starter', 'biz_team',
                      'biz_business', 'biz_pro', 'biz_enterprise',
                      'biz_ultimate')
               ORDER BY telegram_id"""
        )
    return [dict(r) for r in rows]


async def get_subscriptions_with_far_future_expires() -> list:
    """All subscriptions whose DB expires_at is parked in the far future.

    This is the symptom of the bug discovered during the audit: when a
    user's premium expired and they had a bypass entity, the
    fast_expiry_cleanup transition rewrote expires_at to NOW + 10 years
    as a bypass-only marker — but the user's subsequent purchases never
    overwrote that marker, leaving the bot UI showing "expires in 10
    years" even though the panel was rolled back to the real date.

    Returns dicts with telegram_id, expires_at, status,
    subscription_type, is_bypass_only.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, expires_at, status, subscription_type,
                      is_bypass_only, remnawave_premium_uuid
               FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > NOW() + INTERVAL '2 years'
               ORDER BY telegram_id"""
        )
    return [dict(r) for r in rows]


async def update_subscription_expires_at_bulk(updates: list) -> int:
    """Bulk-update subscriptions.expires_at.

    Args:
        updates: list of {"telegram_id": int, "new_expires_at": datetime}

    Returns count of rows successfully updated.

    Uses asyncpg.executemany — one round-trip for the whole batch.
    """
    if not updates:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Coerce to naive UTC (the column is TIMESTAMPTZ but the bot's
        # other writers pass naive — keep consistent so equality
        # comparisons elsewhere don't drift across tz casts).
        rows = [
            (u["new_expires_at"], u["telegram_id"])
            for u in updates
        ]
        async with conn.transaction():
            await conn.executemany(
                "UPDATE subscriptions SET expires_at = $1 WHERE telegram_id = $2 AND status = 'active'",
                rows,
            )
    return len(updates)


async def get_active_trial_telegram_ids() -> list:
    """Telegram IDs of users currently on an active trial — and ONLY
    on a trial (no live PAID premium subscription).

    Trial activation writes a `subscriptions` row with source='trial',
    status='active', subscription_type='basic' (default tariff in
    grant_access), expires_at = trial end, is_bypass_only=FALSE. That
    means the "looks like an active paid sub" filter MUST exclude
    source='trial' explicitly — otherwise the audience comes out
    empty (every trial user gets filtered as if they were already
    paying).

    Time comparison uses an explicit `$1` parameter (not NOW()):
    `users.trial_expires_at` is TIMESTAMP without tz in this DB while
    `subscriptions.expires_at` is TIMESTAMPTZ. Mixing NOW() with a
    naive TIMESTAMP column triggers `operator does not exist`
    failures — the same class of error we hit before in this repo.
    `_to_db_utc` produces the naive-UTC datetime asyncpg can compare
    against both columns.

    Filters:
      - users.trial_expires_at > $1                  → trial running
      - NO subscriptions row with:
          - status='active', expires_at > $1
          - source != 'trial'                        → really paid
          - is_bypass_only=FALSE
          - subscription_type IN paid tariffs

    Returns sorted list of telegram_id integers.
    """
    pool = await get_pool()
    now = _to_db_utc(datetime.now(timezone.utc))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.telegram_id
               FROM users u
               WHERE u.trial_expires_at IS NOT NULL
                 AND u.trial_expires_at > $1
                 AND NOT EXISTS (
                     SELECT 1 FROM subscriptions s
                     WHERE s.telegram_id = u.telegram_id
                       AND s.status = 'active'
                       AND s.expires_at > $1
                       AND COALESCE(s.source, '') != 'trial'
                       AND COALESCE(s.is_bypass_only, FALSE) = FALSE
                       AND s.subscription_type IN (
                           'basic', 'plus', 'biz_starter', 'biz_team',
                           'biz_business', 'biz_pro', 'biz_enterprise',
                           'biz_ultimate'
                       )
                 )
               ORDER BY u.telegram_id""",
            now,
        )
    return [r["telegram_id"] for r in rows]
