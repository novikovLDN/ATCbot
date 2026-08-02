"""Реферальная аналитика для админки: сводки, детализация, история наград.

Выделено из database/subscriptions.py. Это отчётный слой: только чтение,
тяжёлые агрегирующие запросы. Держать его рядом с денежными транзакциями
не было причин — их правят по разным поводам и с разной осторожностью.
"""
import asyncpg
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc, safe_int

logger = logging.getLogger(__name__)


async def get_admin_referral_stats(
    search_query: Optional[str] = None,
    sort_by: str = "total_revenue",  # "total_revenue", "invited_count", "cashback_paid"
    sort_order: str = "DESC",  # "ASC", "DESC"
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Получить агрегированную статистику по всем рефералам для админ-дашборда
    
    Args:
        search_query: Поисковый запрос (telegram_id или username)
        sort_by: Поле для сортировки ("total_revenue", "invited_count", "cashback_paid")
        sort_order: Порядок сортировки ("ASC", "DESC")
        limit: Максимальное количество записей
        offset: Смещение для пагинации
    
    Returns:
        Список словарей с агрегированной статистикой по каждому рефереру:
        - referrer_id: Telegram ID реферера
        - username: Username реферера
        - invited_count: Всего приглашённых
        - paid_count: Сколько оплатили
        - conversion_percent: Процент конверсии
        - total_invited_revenue: Общий доход от приглашённых (рубли)
        - total_cashback_paid: Общий выплаченный кешбэк (рубли)
        - current_cashback_percent: Текущий процент кешбэка
        - first_referral_date: Дата первого приглашения
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_admin_referral_stats skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        return []
    
    try:
        # FIX: Все операции с conn должны происходить строго внутри async with
        async with pool.acquire() as conn:
            # Базовый запрос для агрегированной статистики
            # Используем подзапросы для корректной агрегации
            base_query = """
            SELECT
                u.telegram_id AS referrer_id,
                u.username,
                COALESCE(ref_stats.invited_count, 0) AS invited_count,
                COALESCE(paid_stats.paid_count, 0) AS paid_count,
                COALESCE(trial_stats.trial_count, 0) AS trial_count,
                COALESCE(MIN(r.created_at), NULL) AS first_referral_date,
                COALESCE(revenue_stats.total_revenue_kopecks, 0) AS total_invited_revenue_kopecks,
                COALESCE(cashback_stats.total_cashback_kopecks, 0) AS total_cashback_paid_kopecks
            FROM users u
            LEFT JOIN referrals r ON u.telegram_id = r.referrer_user_id
            LEFT JOIN (
                SELECT referrer_user_id, COUNT(DISTINCT referred_user_id) AS invited_count
                FROM referrals
                GROUP BY referrer_user_id
            ) ref_stats ON u.telegram_id = ref_stats.referrer_user_id
            LEFT JOIN (
                SELECT r.referrer_user_id, COUNT(DISTINCT r.referred_user_id) AS paid_count
                FROM referrals r
                INNER JOIN payments p ON r.referred_user_id = p.telegram_id AND p.status = 'approved'
                GROUP BY r.referrer_user_id
            ) paid_stats ON u.telegram_id = paid_stats.referrer_user_id
            LEFT JOIN (
                -- Скольким из приглашённых он же (реферер) активировал триал.
                -- Триал считаем активированным если users.trial_used_at IS NOT NULL
                -- (значение проставляется в момент /trial даже если сам триал уже истёк).
                SELECT r.referrer_user_id, COUNT(DISTINCT r.referred_user_id) AS trial_count
                FROM referrals r
                INNER JOIN users u2 ON r.referred_user_id = u2.telegram_id
                WHERE u2.trial_used_at IS NOT NULL
                GROUP BY r.referrer_user_id
            ) trial_stats ON u.telegram_id = trial_stats.referrer_user_id
            LEFT JOIN (
                SELECT r.referrer_user_id, SUM(p.amount) AS total_revenue_kopecks
                FROM referrals r
                INNER JOIN payments p ON r.referred_user_id = p.telegram_id AND p.status = 'approved'
                GROUP BY r.referrer_user_id
            ) revenue_stats ON u.telegram_id = revenue_stats.referrer_user_id
            LEFT JOIN (
                SELECT bt.user_id AS referrer_user_id, SUM(bt.amount) AS total_cashback_kopecks
                FROM balance_transactions bt
                WHERE bt.type = 'cashback' AND bt.source = 'referral'
                GROUP BY bt.user_id
            ) cashback_stats ON u.telegram_id = cashback_stats.referrer_user_id
            """
            
            where_clauses = []
            params = []
            param_index = 1
            
            # Фильтр по поисковому запросу
            if search_query:
                try:
                    # Пробуем найти по telegram_id
                    telegram_id = int(search_query)
                    where_clauses.append(f"u.telegram_id = ${param_index}")
                    params.append(telegram_id)
                    param_index += 1
                except ValueError:
                    # Иначе ищем по username
                    where_clauses.append(f"LOWER(u.username) LIKE LOWER(${param_index})")
                    params.append(f"%{search_query}%")
                    param_index += 1
            
            # Фильтр: показываем только рефереров (тех, кто пригласил хотя бы одного)
            where_clauses.append(f"ref_stats.invited_count > 0 OR EXISTS (SELECT 1 FROM referrals r2 WHERE r2.referrer_user_id = u.telegram_id)")
            
            # Группировка по рефереру
            group_by = "GROUP BY u.telegram_id, u.username, ref_stats.invited_count, paid_stats.paid_count, trial_stats.trial_count, revenue_stats.total_revenue_kopecks, cashback_stats.total_cashback_kopecks"
            
            # Сортировка
            sort_column_map = {
                "total_revenue": "total_invited_revenue_kopecks",
                "invited_count": "invited_count",
                "cashback_paid": "total_cashback_paid_kopecks"
            }
            sort_column = sort_column_map.get(sort_by, "total_invited_revenue_kopecks")
            # Validate sort_order to prevent SQL injection
            if sort_order.upper() not in ("ASC", "DESC"):
                sort_order = "DESC"
            order_by = f"ORDER BY {sort_column} {sort_order.upper()}, u.telegram_id ASC"
            
            # Пагинация
            limit_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
            params.extend([limit, offset])
            
            # Собираем полный запрос
            where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            full_query = f"{base_query} {where_clause} {group_by} {order_by} {limit_clause}"
            
            # FIX: Все операции с conn.fetch() происходят строго внутри блока async with
            rows = await conn.fetch(full_query, *params)
            
            # FIX: Извлекаем все данные из rows внутри блока async with
            # Преобразуем rows в список словарей, чтобы не зависеть от connection после выхода из блока
            rows_data = []
            for row in rows:
                rows_data.append(dict(row))
        
        # FIX: Обработка результатов происходит ПОСЛЕ выхода из блока async with
        # Это гарантирует, что conn не используется после release
        result = []
        for row_data in rows_data:
            try:
                referrer_id = row_data.get("referrer_id")
                if referrer_id is None:
                    continue  # Пропускаем строки без referrer_id
                
                # Безопасное извлечение значений с обработкой NULL
                invited_count = safe_int(row_data.get("invited_count"))
                paid_count = safe_int(row_data.get("paid_count"))
                trial_count = safe_int(row_data.get("trial_count"))

                # Вычисляем процент конверсии (защита от деления на 0)
                conversion_percent = (paid_count / invited_count * 100) if invited_count > 0 else 0.0
                trial_percent = (trial_count / invited_count * 100) if invited_count > 0 else 0.0
                
                # Конвертируем из копеек в рубли с безопасной обработкой NULL
                total_invited_revenue_kopecks = safe_int(row_data.get("total_invited_revenue_kopecks"))
                total_cashback_paid_kopecks = safe_int(row_data.get("total_cashback_paid_kopecks"))
                total_invited_revenue = total_invited_revenue_kopecks / 100.0
                total_cashback_paid = total_cashback_paid_kopecks / 100.0
                
                # Определяем текущий процент кешбэка (безопасно)
                # FIX: Вызываем после выхода из блока conn, чтобы избежать проблем с connection lifecycle
                try:
                    from database.users import get_referral_cashback_percent
                    current_cashback_percent = await get_referral_cashback_percent(referrer_id)
                except Exception as e:
                    logger.warning(f"Error getting cashback percent for referrer_id={referrer_id}: {e}")
                    current_cashback_percent = 10  # Значение по умолчанию
                
                result.append({
                    "referrer_id": referrer_id,
                    "username": row_data.get("username") or f"ID{referrer_id}",
                    "invited_count": invited_count,
                    "trial_count": trial_count,
                    "trial_percent": round(trial_percent, 2),
                    "paid_count": paid_count,
                    "conversion_percent": round(conversion_percent, 2),
                    "total_invited_revenue": round(total_invited_revenue, 2),
                    "total_cashback_paid": round(total_cashback_paid, 2),
                    "current_cashback_percent": current_cashback_percent,
                    "first_referral_date": row_data.get("first_referral_date")
                })
            except Exception as e:
                logger.exception(f"Error processing row in get_admin_referral_stats: {e}, row={row_data}")
                continue  # Пропускаем проблемные строки, но продолжаем обработку
        
        return result
    except asyncpg.PostgresError as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping admin referral stats: {e}")
        return []
    except Exception as e:
        logger.warning(f"Error getting admin referral stats: {e}")
        return []


async def get_admin_referral_detail(referrer_id: int) -> Optional[Dict[str, Any]]:
    """
    Получить детальную информацию по конкретному рефереру
    
    Args:
        referrer_id: Telegram ID реферера
    
    Returns:
        Словарь с детальной информацией:
        - referrer_id: Telegram ID реферера
        - username: Username реферера
        - invited_list: Список приглашённых с деталями:
          - invited_user_id: Telegram ID приглашённого
          - username: Username приглашённого
          - registered_at: Дата регистрации
          - first_payment_date: Дата первой оплаты
          - purchase_amount: Сумма покупки (рубли)
          - cashback_amount: Сумма кешбэка (рубли)
          - purchase_id: ID платежа
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_admin_referral_detail skipped")
        return None
    
    pool = await get_pool()
    if pool is None:
        return None
    
    try:
        async with pool.acquire() as conn:
            # Получаем информацию о реферере
            referrer = await conn.fetchrow(
                "SELECT telegram_id, username FROM users WHERE telegram_id = $1",
                referrer_id
            )
            
            if not referrer:
                return None
            
            # Получаем список всех приглашённых с детальной информацией
            invited_list_query = """
            SELECT 
                r.referred_user_id AS invited_user_id,
                u.username,
                r.created_at AS registered_at,
                MIN(p.created_at) AS first_payment_date,
                MIN(p.id) AS purchase_id,
                MIN(p.amount) AS purchase_amount_kopecks,
                COALESCE(SUM(CASE 
                    WHEN bt.type = 'cashback' AND bt.source = 'referral' 
                    AND bt.related_user_id = r.referred_user_id THEN bt.amount 
                    ELSE 0 
                END), 0) AS cashback_amount_kopecks
            FROM referrals r
            LEFT JOIN users u ON r.referred_user_id = u.telegram_id
            LEFT JOIN payments p ON r.referred_user_id = p.telegram_id 
                AND p.status = 'approved'
            LEFT JOIN balance_transactions bt ON bt.user_id = $1 
                AND bt.type = 'cashback' 
                AND bt.source = 'referral'
                AND bt.related_user_id = r.referred_user_id
            WHERE r.referrer_user_id = $1
            GROUP BY r.referred_user_id, u.username, r.created_at
            ORDER BY r.created_at DESC
            """
            
            invited_rows = await conn.fetch(invited_list_query, referrer_id)
            
            invited_list = []
            for row in invited_rows:
                invited_list.append({
                    "invited_user_id": row["invited_user_id"],
                    "username": row["username"] or f"ID{row['invited_user_id']}",
                    "registered_at": row["registered_at"],
                    "first_payment_date": row["first_payment_date"],
                    "purchase_amount": (row["purchase_amount_kopecks"] or 0) / 100.0,
                    "cashback_amount": (row["cashback_amount_kopecks"] or 0) / 100.0,
                    "purchase_id": row["purchase_id"]
                })
            
            return {
                "referrer_id": referrer_id,
                "username": referrer["username"] or f"ID{referrer_id}",
                "invited_list": invited_list
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping admin referral detail: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error getting admin referral detail: {e}")
        return None


async def get_referral_overall_stats(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Получить общую статистику по реферальной системе
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
    
    Returns:
        Словарь с общей статистикой:
        - total_referrers: Всего рефереров
        - total_referrals: Всего приглашённых пользователей
        - total_paid_referrals: Всего оплативших рефералов
        - total_revenue: Общий доход от рефералов (рубли)
        - total_cashback_paid: Общий выплаченный кешбэк (рубли)
        - avg_cashback_per_referrer: Средний кешбэк на реферера (рубли)
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_overall_stats skipped")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    
    try:
        async with pool.acquire() as conn:
            # Базовые условия для фильтрации по дате
            date_filter = ""
            params = []
            if date_from or date_to:
                conditions = []
                if date_from:
                    conditions.append("rr.created_at >= $1")
                    params.append(date_from)
                if date_to:
                    param_idx = len(params) + 1
                    conditions.append(f"rr.created_at <= ${param_idx}")
                    params.append(date_to)
                date_filter = "WHERE " + " AND ".join(conditions)
            
            # Всего рефереров (уникальных)
            # Безопасная обработка NULL через COALESCE
            total_referrers_query = f"""
                SELECT COALESCE(COUNT(DISTINCT rr.referrer_id), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_referrers_val = await conn.fetchval(total_referrers_query, *params)
            total_referrers = safe_int(total_referrers_val)
            
            # Всего приглашённых (из таблицы referrals)
            total_referrals_query = "SELECT COALESCE(COUNT(DISTINCT referred_user_id), 0) FROM referrals"
            if date_from or date_to:
                # Если есть фильтр по дате, применяем его к referrals
                if date_from:
                    total_referrals_query += " WHERE created_at >= $1"
                if date_to:
                    param_idx = len([date_from]) + 1
                    total_referrals_query += f" {'AND' if date_from else 'WHERE'} created_at <= ${param_idx}"
            total_referrals_val = await conn.fetchval(total_referrals_query, *params)
            total_referrals = safe_int(total_referrals_val)
            
            # Всего оплативших рефералов (уникальных buyer_id из referral_rewards)
            total_paid_referrals_query = f"""
                SELECT COALESCE(COUNT(DISTINCT rr.buyer_id), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_paid_referrals_val = await conn.fetchval(total_paid_referrals_query, *params)
            total_paid_referrals = safe_int(total_paid_referrals_val)
            
            # Общий доход от рефералов (сумма purchase_amount из referral_rewards)
            total_revenue_query = f"""
                SELECT COALESCE(SUM(rr.purchase_amount), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_revenue_kopecks_val = await conn.fetchval(total_revenue_query, *params)
            total_revenue_kopecks = safe_int(total_revenue_kopecks_val)
            total_revenue = total_revenue_kopecks / 100.0
            
            # Общий выплаченный кешбэк (сумма reward_amount из referral_rewards)
            total_cashback_query = f"""
                SELECT COALESCE(SUM(rr.reward_amount), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_cashback_kopecks_val = await conn.fetchval(total_cashback_query, *params)
            total_cashback_kopecks = safe_int(total_cashback_kopecks_val)
            total_cashback_paid = total_cashback_kopecks / 100.0
            
            # Средний кешбэк на реферера (защита от деления на 0)
            avg_cashback_per_referrer = total_cashback_paid / total_referrers if total_referrers > 0 else 0.0
            
            return {
                "total_referrers": total_referrers,
                "total_referrals": total_referrals,
                "total_paid_referrals": total_paid_referrals,
                "total_revenue": round(total_revenue, 2),
                "total_cashback_paid": round(total_cashback_paid, 2),
                "avg_cashback_per_referrer": round(avg_cashback_per_referrer, 2)
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or referral_rewards tables missing or inaccessible — skipping referral overall stats: {e}")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    except Exception as e:
        logger.warning(f"Error getting referral overall stats: {e}")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }


async def get_referral_rewards_history(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Получить историю начислений реферального кешбэка
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
        limit: Максимальное количество записей
        offset: Смещение для пагинации
    
    Returns:
        Список словарей с историей начислений:
        - id: ID записи
        - referrer_id: Telegram ID реферера
        - referrer_username: Username реферера
        - buyer_id: Telegram ID покупателя
        - buyer_username: Username покупателя
        - purchase_amount: Сумма покупки (рубли)
        - percent: Процент кешбэка
        - reward_amount: Сумма кешбэка (рубли)
        - created_at: Дата начисления
        - purchase_id: ID покупки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Базовый запрос
        base_query = """
            SELECT 
                rr.id,
                rr.referrer_id,
                referrer_user.username AS referrer_username,
                rr.buyer_id,
                buyer_user.username AS buyer_username,
                rr.purchase_amount,
                rr.percent,
                rr.reward_amount,
                rr.created_at,
                rr.purchase_id
            FROM referral_rewards rr
            LEFT JOIN users referrer_user ON rr.referrer_id = referrer_user.telegram_id
            LEFT JOIN users buyer_user ON rr.buyer_id = buyer_user.telegram_id
        """
        
        where_clauses = []
        params = []
        param_index = 1
        
        # Фильтрация по дате
        if date_from:
            where_clauses.append(f"rr.created_at >= ${param_index}")
            params.append(date_from)
            param_index += 1
        
        if date_to:
            where_clauses.append(f"rr.created_at <= ${param_index}")
            params.append(date_to)
            param_index += 1
        
        # Собираем запрос
        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        order_by = "ORDER BY rr.created_at DESC"
        limit_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
        params.extend([limit, offset])
        
        full_query = f"{base_query} {where_clause} {order_by} {limit_clause}"
        
        rows = await conn.fetch(full_query, *params)
        
        # Обрабатываем результаты
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "referrer_id": row["referrer_id"],
                "referrer_username": row["referrer_username"] or f"ID{row['referrer_id']}",
                "buyer_id": row["buyer_id"],
                "buyer_username": row["buyer_username"] or f"ID{row['buyer_id']}",
                "purchase_amount": (row["purchase_amount"] or 0) / 100.0,
                "percent": row["percent"] or 0,
                "reward_amount": (row["reward_amount"] or 0) / 100.0,
                "created_at": row["created_at"],
                "purchase_id": row["purchase_id"]
            })
        
        return result


async def get_referral_rewards_history_count(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> int:
    """
    Получить общее количество записей в истории начислений (для пагинации)
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
    
    Returns:
        Общее количество записей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        base_query = "SELECT COUNT(*) FROM referral_rewards rr"
        
        where_clauses = []
        params = []
        param_index = 1
        
        if date_from:
            where_clauses.append(f"rr.created_at >= ${param_index}")
            params.append(date_from)
            param_index += 1
        
        if date_to:
            where_clauses.append(f"rr.created_at <= ${param_index}")
            params.append(date_to)
            param_index += 1
        
        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        full_query = f"{base_query} {where_clause}"
        
        count = await conn.fetchval(full_query, *params) or 0
        return count
