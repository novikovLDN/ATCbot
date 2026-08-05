"""Отчёты дашборда: временные ряды и сводки за период.

ЧТО ЗДЕСЬ
    Агрегаты по всей базе — выручка, платежи, новые пользователи и подписки:
    по дням, по часам суток, за день, за месяц. Плюс средний LTV и разрез по
    рефералам. Только чтение, ни одного UPDATE.

СУТКИ РЕЖУТСЯ ПО МОСКВЕ
    Раньше суточный ряд резался по UTC, а тайл «Доход сегодня» на том же
    экране считался от полуночи МСК. Три часа покупок — с 00:00 до 03:00 МСК —
    у тайла попадали в сегодня, а у графика во вчера: две цифры про один день
    не сходились, и понять, какая правильная, было нельзя. Теперь весь
    дашборд режет сутки одинаково.

ВЫРУЧКА — ТОЛЬКО ВНЕШНИЕ ПОСТУПЛЕНИЯ
    Строки с payment_provider = 'balance' — это внутреннее движение денег
    (покупка с баланса), они уже посчитаны в момент пополнения. Убрать это
    условие = удвоить выручку по всем отчётам разом.

ЧТО ЛЕГКО СЛОМАТЬ
    Запись `col AT TIME ZONE 'Europe/Moscow'` рассчитана на TIMESTAMPTZ.
    Колонка без зоны в том же выражении сдвинет сутки на три часа и ничего
    при этом не сломает — расхождение заметят по цифрам, не по ошибке.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


async def get_daily_timeseries(days: int) -> Dict[str, Any]:
    """Daily аггрегаты за последние `days` суток по Москве.

    Один payload — три серии: revenue, new_users, new_subscriptions.
    Все пустые дни в окне присутствуют с нулями (через generate_series),
    чтобы фронту не приходилось их добивать самому — графики получают
    непрерывный X.

    ПОЧЕМУ МОСКВА, А НЕ UTC.
    Раньше сутки резались по UTC, а тайл «Доход сегодня» на том же экране
    считался от полуночи МСК (фронт присылает since=mskTodayStartIso,
    см. dashboard/src/lib/format.ts). Три часа покупок — с 00:00 до 03:00
    МСК — у тайла попадали в сегодня, а у графика во вчера: две цифры про
    один и тот же день не сходились, и понять, какая из них правильная,
    было нельзя. Часовой график (get_hourly_timeseries) уже жил в МСК.
    Теперь весь дашборд режет сутки одинаково.

    Границу окна считаем от полуночи МСК того же дня, что и первая точка
    generate_series: иначе в первый день окна попадал бы хвост предыдущих
    суток и первая точка графика выглядела бы аномально низкой/высокой.

    Выручка — только внешние поступления: строки с payment_provider =
    'balance' это внутреннее движение денег (покупка с баланса), они уже
    посчитаны в момент пополнения. См. REVENUE_EXTERNAL_ONLY_SQL в
    database/analytics.py.

    Запись `col AT TIME ZONE 'Europe/Moscow'` рассчитана на TIMESTAMPTZ:
    pending_purchases.created_at и subscriptions.activated_at перевели
    миграцией 024, users.created_at — миграцией 025. Если кто-то заведёт
    здесь колонку без зоны, та же запись сдвинет сутки на три часа и
    ничего при этом не сломает — расхождение заметят по цифрам, не по
    ошибке.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH days AS (
                SELECT generate_series(
                    DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow')) - ($1::int - 1) * INTERVAL '1 day',
                    DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow')),
                    INTERVAL '1 day'
                )::date AS day
            ),
            win AS (
                SELECT (
                    (DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                        - ($1::int - 1) * INTERVAL '1 day') AT TIME ZONE 'Europe/Moscow'
                ) AS since
            ),
            pay AS (
                SELECT DATE_TRUNC('day', created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COALESCE(SUM(price_kopecks), 0)::bigint AS revenue_kopecks,
                       COUNT(*)::int AS payments_count
                FROM pending_purchases
                WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                  AND created_at >= (SELECT since FROM win)
                GROUP BY 1
            ),
            usr AS (
                SELECT DATE_TRUNC('day', created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COUNT(*)::int AS new_users
                FROM users
                WHERE created_at >= (SELECT since FROM win)
                GROUP BY 1
            ),
            sub AS (
                SELECT DATE_TRUNC('day', activated_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COUNT(*)::int AS new_subs,
                       COUNT(*) FILTER (WHERE source = 'payment')::int AS new_paid_subs
                FROM subscriptions
                WHERE activated_at IS NOT NULL
                  AND activated_at >= (SELECT since FROM win)
                GROUP BY 1
            )
            SELECT
                d.day,
                COALESCE(pay.revenue_kopecks, 0)  AS revenue_kopecks,
                COALESCE(pay.payments_count, 0)   AS payments_count,
                COALESCE(usr.new_users, 0)        AS new_users,
                COALESCE(sub.new_subs, 0)         AS new_subs,
                COALESCE(sub.new_paid_subs, 0)    AS new_paid_subs
            FROM days d
            LEFT JOIN pay ON pay.day = d.day
            LEFT JOIN usr ON usr.day = d.day
            LEFT JOIN sub ON sub.day = d.day
            ORDER BY d.day
            """,
            days,
        )

    series = [
        {
            "date": r["day"].isoformat(),
            "revenue_rubles": float(r["revenue_kopecks"]) / 100.0,
            "payments_count": int(r["payments_count"]),
            "new_users": int(r["new_users"]),
            "new_subscriptions": int(r["new_subs"]),
            "new_paid_subscriptions": int(r["new_paid_subs"]),
        }
        for r in rows
    ]
    # tz отдаём явно — чтобы на фронте не гадать, в каких сутках точка.
    return {"days": days, "tz": "Europe/Moscow", "series": series}


async def get_hourly_timeseries(days: int) -> Dict[str, Any]:
    """Hour-of-day аггрегаты за последние `days` суток.

    24 строки (0..23 — час Europe/Moscow). Суммируем revenue, платежи,
    новых юзеров и новые подписки. Извлекаем час из timestamptz после
    конвертации в МСК — админу удобнее видеть пики в местном времени,
    чем в UTC.

    Используем `generate_series(0, 23)` для гарантии полных 24 точек
    даже если в каком-то часу не было активности.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH hrs AS (
                SELECT generate_series(0, 23) AS hour
            ),
            pay AS (
                SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COALESCE(SUM(price_kopecks), 0)::bigint AS revenue_kopecks,
                       COUNT(*)::int AS payments_count
                FROM pending_purchases
                WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                  AND created_at >= (NOW() AT TIME ZONE 'UTC') - $1::int * INTERVAL '1 day'
                GROUP BY 1
            ),
            usr AS (
                SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COUNT(*)::int AS new_users
                FROM users
                WHERE created_at >= (NOW() AT TIME ZONE 'UTC') - $1::int * INTERVAL '1 day'
                GROUP BY 1
            ),
            sub AS (
                SELECT EXTRACT(HOUR FROM activated_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COUNT(*)::int AS new_subs,
                       COUNT(*) FILTER (WHERE source = 'payment')::int AS new_paid_subs
                FROM subscriptions
                WHERE activated_at IS NOT NULL
                  AND activated_at >= (NOW() AT TIME ZONE 'UTC') - $1::int * INTERVAL '1 day'
                GROUP BY 1
            )
            SELECT
                hrs.hour,
                COALESCE(pay.revenue_kopecks, 0) AS revenue_kopecks,
                COALESCE(pay.payments_count, 0)  AS payments_count,
                COALESCE(usr.new_users, 0)       AS new_users,
                COALESCE(sub.new_subs, 0)        AS new_subs,
                COALESCE(sub.new_paid_subs, 0)   AS new_paid_subs
            FROM hrs
            LEFT JOIN pay ON pay.hour = hrs.hour
            LEFT JOIN usr ON usr.hour = hrs.hour
            LEFT JOIN sub ON sub.hour = hrs.hour
            ORDER BY hrs.hour
            """,
            days,
        )
    series = [
        {
            "hour": int(r["hour"]),
            "revenue_rubles": float(r["revenue_kopecks"]) / 100.0,
            "payments_count": int(r["payments_count"]),
            "new_users": int(r["new_users"]),
            "new_subscriptions": int(r["new_subs"]),
            "new_paid_subscriptions": int(r["new_paid_subs"]),
        }
        for r in rows
    ]
    return {"days": days, "tz": "Europe/Moscow", "series": series}


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
                   SELECT telegram_id, SUM(price_kopecks) as user_total
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
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
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
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
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
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
