"""Пользователь глазами админа: выгрузки, история, финансовый профиль.

ЧТО ЗДЕСЬ
    Только чтение. Данные, которые админка показывает в карточке
    пользователя и отдаёт в выгрузки: список всех пользователей, активные
    подписки, история подписок, сводный финансовый и реферальный профиль.

ПОЧЕМУ ОТДЕЛЬНО ОТ ОТЧЁТОВ
    Отчёты (database/admin_reports.py) агрегируют по всей базе и живут в
    часовом поясе Москвы. Здесь всё считается по одному telegram_id и часовой
    пояс не важен. Правят их по разным поводам: тут — когда меняется карточка
    пользователя, там — когда спорят о цифрах на дашборде.

ЧТО ЛЕГКО СЛОМАТЬ
    Имена колонок в get_user_extended_stats. В pending_purchases НЕТ ни
    amount_kopecks, ни paid_at; запрос, который их спрашивал, падал
    UndefinedColumnError на живой базе — и карточка пользователя не
    открывалась ни в боте, ни в дашборде.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import database.core as _core
from database.core import get_pool, _to_db_utc

logger = logging.getLogger(__name__)


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
    """Full financial + referral profile of a user for the admin card.

    Returns:
        renewals_count            — продлений подписки (subscription_history)
        reissues_count            — перевыпусков ключа
        total_spent_rubles        — сумма ВСЕХ approved-платежей в ₽
        total_payments_count      — общее число approved-платежей
        first_paid_at / last_paid_at — граничные даты платежей
        referrer_telegram_id      — кто пригласил (или NULL)
        referrer_username         — username пригласившего (для UI)
        referrals_invited_count   — сколько пригласил сам
        referrals_rewarded_count  — из них сколько «сработали» (bonus paid)
        traffic_gb_purchased_total — суммарно ГБ купил (bypass-паки)
        traffic_purchases_count   — количество GB-покупок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Продления
        renewals_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history
               WHERE telegram_id = $1 AND action_type = 'renewal'""",
            telegram_id,
        )
        # Перевыпуски
        reissues_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history
               WHERE telegram_id = $1
                 AND action_type IN ('reissue','manual_reissue')""",
            telegram_id,
        )
        # Финансы — оплаченные покупки по всем типам (подписки/трафик/etc).
        # price_kopecks / 100 = рубли; NULLs исключаем.
        #
        # Колонки называются именно так, а не иначе, и это важно: в
        # pending_purchases НЕТ ни amount_kopecks, ни paid_at. Запрос,
        # который их запрашивал, падал UndefinedColumnError на живой базе —
        # карточка пользователя в боте и в дашборде не открывалась вовсе.
        #
        # Момент оплаты берём из created_at — это старт чекаута, а не
        # приход денег. Расхождение до срока жизни счёта (минуты). Честная
        # колонка paid_at потребует миграции + записи в двух местах, где
        # статус переводится в 'paid' (database/subscriptions.py,
        # database/pending_purchases.py); до тех пор created_at — лучшее,
        # что есть, и им же считаются все оконные метрики выручки.
        pay_row = await conn.fetchrow(
            """SELECT
                   COUNT(*) AS n,
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS total_kopecks,
                   MIN(created_at) AS first_paid_at,
                   MAX(created_at) AS last_paid_at
               FROM pending_purchases
               WHERE telegram_id = $1
                 AND status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'""",
            telegram_id,
        )
        total_payments = int(pay_row["n"] or 0) if pay_row else 0
        total_kopecks = int(pay_row["total_kopecks"] or 0) if pay_row else 0
        first_paid_at = pay_row["first_paid_at"] if pay_row else None
        last_paid_at = pay_row["last_paid_at"] if pay_row else None

        # Пригласивший
        ref_row = await conn.fetchrow(
            """SELECT u.referrer_id, ru.username AS referrer_username
               FROM users u
               LEFT JOIN users ru ON ru.telegram_id = u.referrer_id
               WHERE u.telegram_id = $1""",
            telegram_id,
        )
        referrer_id = ref_row["referrer_id"] if ref_row else None
        referrer_username = ref_row["referrer_username"] if ref_row else None

        # Сколько сам пригласил
        inv_row = await conn.fetchrow(
            """SELECT COUNT(*) AS n,
                      COUNT(*) FILTER (WHERE is_rewarded) AS rewarded
               FROM referrals
               WHERE referrer_user_id = $1""",
            telegram_id,
        )
        invited = int(inv_row["n"] or 0) if inv_row else 0
        rewarded = int(inv_row["rewarded"] or 0) if inv_row else 0

        # Купил ГБ (bypass). Столбец gb_purchased.gb_amount, count и sum.
        gb_row = None
        try:
            gb_row = await conn.fetchrow(
                """SELECT COUNT(*) AS n,
                          COALESCE(SUM(gb_amount), 0)::INTEGER AS gb_total
                   FROM gb_purchased
                   WHERE telegram_id = $1""",
                telegram_id,
            )
        except Exception as e:
            logger.debug("gb_purchased query failed (табл. может отсутствовать): %s", e)
        gb_total = int(gb_row["gb_total"] or 0) if gb_row else 0
        gb_count = int(gb_row["n"] or 0) if gb_row else 0

        return {
            "renewals_count": renewals_count or 0,
            "reissues_count": reissues_count or 0,
            "total_spent_rubles": total_kopecks / 100.0,
            "total_payments_count": total_payments,
            "first_paid_at": first_paid_at.isoformat() if first_paid_at else None,
            "last_paid_at": last_paid_at.isoformat() if last_paid_at else None,
            "referrer_telegram_id": referrer_id,
            "referrer_username": referrer_username,
            "referrals_invited_count": invited,
            "referrals_rewarded_count": rewarded,
            "traffic_gb_purchased_total": gb_total,
            "traffic_purchases_count": gb_count,
        }


async def get_all_users_telegram_ids() -> list:
    """Получить список всех Telegram ID пользователей"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM users")
        return [row["telegram_id"] for row in rows]
