"""Пользователи, баланс и ферма.

ЧТО ЗДЕСЬ ЕСТЬ
    Карточка пользователя, операции с балансом, данные фермы и заявки
    на вывод средств.

ДЕНЬГИ ХРАНЯТСЯ В КОПЕЙКАХ
    В колонке balance лежат копейки, а функции принимают и отдают рубли.
    Преобразование делается на границе: increase_balance и decrease_balance
    умножают на 100 при записи. Любой прямой SQL к balance обязан помнить
    об этом, иначе ошибка в сто раз пройдёт незамеченной.

ИЗМЕНЕНИЕ БАЛАНСА ТОЛЬКО ЧЕРЕЗ ХЕЛПЕРЫ
    increase_balance и decrease_balance берут advisory-лок на пользователя и
    пишут строку в balance_transactions. Прямой UPDATE баланса в обход них
    ломает и защиту от гонок, и историю операций.

РЕФЕРАЛЬНАЯ ПРОГРАММА
    Вынесена в database/referrals.py, но реэкспортируется отсюда: код годами
    обращался к ней через database.users.
"""
import asyncpg
import base64
import hashlib
import json
import logging
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List
import config
import database.core as _core
from database.core import (
    get_pool, safe_int,
    _to_db_utc, _from_db_utc, _ensure_utc,
    retry_async,
)

logger = logging.getLogger(__name__)

# Реферальная программа вынесена в database/referrals.py.
from database.referrals import (  # noqa: F401,E402
    generate_referral_code,
    create_user,
    get_user_referral_code,
    find_user_by_referral_code,
    register_referral,
    mark_referral_active,
    _mark_referral_active_internal,
    get_referral_stats,
    get_referral_cashback_percent,
    get_cashback_fixed_percent,
    set_cashback_fixed_percent,
    clear_cashback_fixed_percent,
    get_effective_cashback_percent,
    calculate_referral_percent,
    get_referral_level_info,
    get_total_cashback_earned,
    get_referral_metrics,
    calculate_referral_level,
    get_referral_statistics,
    process_referral_reward,
)

async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить пользователя по Telegram ID"""
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_user skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_user skipped")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
        return dict(row) if row else None


async def get_user_balance(telegram_id: int) -> float:
    """
    Получить баланс пользователя в рублях
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Баланс в рублях (0.0 если пользователь не найден)
    """
    from decimal import Decimal
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_user_balance skipped")
        return 0.0
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_user_balance skipped")
        return 0.0
    async with pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
        )
        if balance is None:
            return 0.0
        # Конвертируем из копеек в рубли
        if isinstance(balance, (int, Decimal)):
            return float(balance) / 100.0
        return float(balance) if balance else 0.0


async def increase_balance(telegram_id: int, amount: float, source: str = "telegram_payment", description: Optional[str] = None, conn=None) -> bool:
    """
    Увеличить баланс пользователя (атомарно)
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в рублях (положительное число)
        source: Источник пополнения ('telegram_payment', 'admin', 'referral')
        description: Описание транзакции
        conn: Опциональное соединение (caller holds transaction). Если задано — используем его без pool.acquire.
    
    Returns:
        True если успешно, False при ошибке
    """
    if amount <= 0:
        logger.error(f"Invalid amount for increase_balance: {amount}")
        return False
    
    # Конвертируем рубли в копейки для хранения
    amount_kopecks = round(amount * 100)
    
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, increase_balance skipped")
        return False

    async def _do_increase(c):
        # CRITICAL: advisory lock per user для защиты от race conditions
        await c.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
        await c.execute(
            "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
            amount_kopecks, telegram_id
        )
        transaction_type = "topup"
        if source == "referral" or source == "referral_reward":
            transaction_type = "cashback"
        elif source == "admin" or source == "admin_adjustment":
            transaction_type = "admin_adjustment"
        await c.execute(
            """INSERT INTO balance_transactions (user_id, amount, type, source, description)
               VALUES ($1, $2, $3, $4, $5)""",
            telegram_id, amount_kopecks, transaction_type, source, description
        )
        logger.info(
            f"BALANCE_INCREASED user={telegram_id} amount={amount:.2f} RUB "
            f"({amount_kopecks} kopecks) source={source}"
        )
        return True

    if conn is not None:
        try:
            await _do_increase(conn)
            return True
        except Exception as e:
            logger.exception(f"Error increasing balance for user {telegram_id}")
            return False

    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, increase_balance skipped")
        return False
    async with pool.acquire() as conn_acquired:
        async with conn_acquired.transaction():
            try:
                await _do_increase(conn_acquired)
                return True
            except Exception as e:
                logger.exception(f"Error increasing balance for user {telegram_id}")
                return False


async def get_farm_data(telegram_id: int) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Получить данные фермы пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
    
    Returns:
        Tuple of (farm_plots: list, plot_count: int, balance: int in kopecks)
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_farm_data skipped")
        return ([], 1, 0)
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_farm_data skipped")
        return ([], 1, 0)
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT farm_plots, farm_plot_count, balance FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if row is None:
            # Initialize default farm data
            default_plots = []
            for i in range(1):
                default_plots.append({
                    "plot_id": i,
                    "status": "empty",
                    "plant_type": None,
                    "planted_at": None,
                    "ready_at": None,
                    "dead_at": None,
                    "notified_ready": False,
                    "notified_12h": False,
                    "notified_dead": False,
                    "water_used_at": None,
                    "fertilizer_used_at": None
                })
            await conn.execute(
                "INSERT INTO users (telegram_id, farm_plots, farm_plot_count, balance) VALUES ($1, $2::jsonb, $3, $4) ON CONFLICT (telegram_id) DO UPDATE SET farm_plots = $2::jsonb, farm_plot_count = $3",
                telegram_id, json.dumps(default_plots), 1, 0
            )
            return (default_plots, 1, 0)
        
        farm_plots = row.get("farm_plots")
        if farm_plots is None:
            farm_plots = []
        elif isinstance(farm_plots, str):
            farm_plots = json.loads(farm_plots)
        
        # Ensure plot 0 always exists for every user (free first plot)
        if not farm_plots or len(farm_plots) == 0:
            default_plots = [
                {
                    "plot_id": 0,
                    "status": "empty",
                    "plant_type": None,
                    "planted_at": None,
                    "ready_at": None,
                    "dead_at": None,
                    "notified_ready": False,
                    "notified_12h": False,
                    "notified_dead": False,
                    "water_used_at": None,
                    "fertilizer_used_at": None,
                }
            ]
            farm_plots = default_plots
            await conn.execute(
                "UPDATE users SET farm_plots = $1::jsonb, farm_plot_count = 1 WHERE telegram_id = $2",
                json.dumps(farm_plots), telegram_id
            )
        
        plot_count = row.get("farm_plot_count", 1)
        balance = row.get("balance", 0)
        if balance is None:
            balance = 0
        
        return (farm_plots, plot_count, balance)


async def save_farm_plots(telegram_id: int, farm_plots: List[Dict[str, Any]]) -> None:
    """
    Сохранить данные грядок пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        farm_plots: Список объектов грядок
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, save_farm_plots skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, save_farm_plots skipped")
        return
    
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET farm_plots = $1::jsonb WHERE telegram_id = $2",
            json.dumps(farm_plots), telegram_id
        )


async def update_farm_plot_count(telegram_id: int, count: int) -> None:
    """
    Обновить количество грядок пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        count: Новое количество грядок
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, update_farm_plot_count skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, update_farm_plot_count skipped")
        return
    
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET farm_plot_count = $1 WHERE telegram_id = $2",
            count, telegram_id
        )


async def get_users_with_active_farm() -> List[Dict[str, Any]]:
    """
    Returns users who have at least one growing or ready plot.
    Follows same pattern as other database functions - calls get_pool() internally.
    
    Returns:
        List of user dicts with telegram_id, farm_plots, farm_plot_count
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_users_with_active_farm skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_users_with_active_farm skipped")
        return []
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, farm_plots, farm_plot_count 
               FROM users 
               WHERE farm_plots != '[]'::jsonb 
                 AND farm_plots IS NOT NULL
                 AND jsonb_array_length(farm_plots) > 0"""
        )
        return [dict(row) for row in rows]


async def decrease_balance(telegram_id: int, amount: float, source: str = "subscription_payment", description: Optional[str] = None, conn=None) -> bool:
    """
    Уменьшить баланс пользователя (атомарно)
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в рублях (положительное число)
        source: Источник списания ('subscription_payment', 'admin', 'refund')
        description: Описание транзакции
        conn: Опциональное соединение (caller holds transaction). Если задано — используем его без pool.acquire.
    
    Returns:
        True если успешно, False при ошибке или недостатке средств
    """
    if amount <= 0:
        logger.error(f"Invalid amount for decrease_balance: {amount}")
        return False
    
    # Конвертируем рубли в копейки для хранения
    amount_kopecks = round(amount * 100)
    
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, decrease_balance skipped")
        return False

    async def _do_decrease(c):
        await c.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
        row = await c.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
            telegram_id
        )
        if not row:
            logger.error(f"User {telegram_id} not found")
            return False
        current_balance = row["balance"]
        if current_balance < amount_kopecks:
            logger.warning(f"Insufficient balance for user {telegram_id}: {current_balance} < {amount_kopecks}")
            return False
        new_balance = current_balance - amount_kopecks
        await c.execute(
            "UPDATE users SET balance = $1 WHERE telegram_id = $2",
            new_balance, telegram_id
        )
        transaction_type = "subscription_payment"
        if source == "admin" or source == "admin_adjustment":
            transaction_type = "admin_adjustment"
        elif source == "auto_renew":
            transaction_type = "subscription_payment"
        elif source == "refund":
            transaction_type = "refund"
        await c.execute(
            """INSERT INTO balance_transactions (user_id, amount, type, source, description)
               VALUES ($1, $2, $3, $4, $5)""",
            telegram_id, -amount_kopecks, transaction_type, source, description
        )
        logger.info(
            f"BALANCE_DECREASED user={telegram_id} amount={amount:.2f} RUB "
            f"({amount_kopecks} kopecks) source={source}"
        )
        return True

    if conn is not None:
        try:
            return await _do_decrease(conn)
        except Exception as e:
            logger.exception(f"Error decreasing balance for user {telegram_id}")
            return False

    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, decrease_balance skipped")
        return False
    async with pool.acquire() as conn_acquired:
        async with conn_acquired.transaction():
            try:
                return await _do_decrease(conn_acquired)
            except Exception as e:
                logger.exception(f"Error decreasing balance for user {telegram_id}")
                return False


async def log_balance_transaction(telegram_id: int, amount: float, transaction_type: str, source: Optional[str] = None, description: Optional[str] = None) -> bool:
    """
    Записать транзакцию баланса (без изменения баланса)
    
    Args:
        telegram_id: Telegram ID пользователя
        amount: Сумма в рублях (может быть отрицательной)
        transaction_type: Тип транзакции ('topup', 'subscription_payment', 'refund', 'bonus')
        source: Источник транзакции
        description: Описание транзакции
    
    Returns:
        True если успешно, False при ошибке
    """
    amount_kopecks = round(amount * 100)
    
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, log_balance_transaction skipped")
        return False
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, log_balance_transaction skipped")
        return False
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, transaction_type, source, description
            )
            logger.info(f"Logged balance transaction: user={telegram_id}, amount={amount} RUB, type={transaction_type}, source={source}")
            return True
        except Exception as e:
            logger.exception(f"Error logging balance transaction for user {telegram_id}")
            return False


# ====================================================================================
# WITHDRAWAL REQUESTS (Atlas Secure balance withdrawal system)
# ====================================================================================

# Источники начислений, заработанных в мини-играх. Такие деньги тратить внутри
# бота можно (подписка, трафик, товары), а выводить на карту — нельзя: иначе
# ферма превращается в печатный станок реальных денег.
GAME_EARNING_SOURCES = (
    "farm_harvest",
    "farm_early_harvest",
    "farm_storm_auto_harvest",
)


async def get_balance_breakdown(telegram_id: int, conn=None) -> Dict[str, int]:
    """Разложить баланс на выводимую и игровую части (в копейках).

    Правило учёта. Деньги на балансе обезличены, поэтому нужно соглашение,
    из какой «кучи» списываются траты. Берём самое выгодное для пользователя
    и безопасное для нас: ЛЮБАЯ трата внутри бота (подписка, трафик, товары,
    щит от шторма) сначала съедает игровые деньги и только потом реальные.

        game_credits — сумма всех начислений из мини-игр
        internal_spend — сумма всех трат, кроме самих выводов
        game_locked = max(0, game_credits - internal_spend)
        withdrawable = max(0, balance - game_locked)

    Почему выводы исключены из internal_spend: вывести игровые деньги нельзя
    по определению, значит уменьшать ими игровой остаток неверно — иначе
    один вывод «отмывал» бы следующую порцию фарма.

    Возвращает {"balance", "withdrawable", "game_locked", "game_credits"}.
    При недоступной БД — нули, вызывающий код обязан это учитывать.
    """
    empty = {"balance": 0, "withdrawable": 0, "game_locked": 0, "game_credits": 0}

    async def _query(c) -> Dict[str, int]:
        row = await c.fetchrow(
            """SELECT
                   COALESCE((SELECT balance FROM users WHERE telegram_id = $1), 0)
                       AS balance,
                   COALESCE((SELECT SUM(amount) FROM balance_transactions
                             WHERE user_id = $1 AND amount > 0
                               AND source = ANY($2::text[])), 0)
                       AS game_credits,
                   COALESCE((SELECT SUM(-amount) FROM balance_transactions
                             WHERE user_id = $1 AND amount < 0
                               AND COALESCE(type, '') <> 'withdrawal'), 0)
                       AS internal_spend
            """,
            telegram_id, list(GAME_EARNING_SOURCES),
        )
        balance = int(row["balance"] or 0)
        game_credits = int(row["game_credits"] or 0)
        internal_spend = int(row["internal_spend"] or 0)
        game_locked = max(0, game_credits - internal_spend)
        return {
            "balance": balance,
            "withdrawable": max(0, balance - game_locked),
            "game_locked": min(game_locked, max(0, balance)),
            "game_credits": game_credits,
        }

    # Готовое соединение приходит из транзакции create_withdrawal_request —
    # там проверка DB_READY уже пройдена, и своё соединение брать нельзя:
    # расчёт обязан идти под тем же локом, что и списание.
    if conn is not None:
        return await _query(conn)
    if not _core.DB_READY:
        logger.warning("DB not ready, get_balance_breakdown skipped")
        return empty
    pool = await get_pool()
    if pool is None:
        return empty
    async with pool.acquire() as c:
        return await _query(c)


async def create_withdrawal_request(
    telegram_id: int,
    username: Optional[str],
    amount_kopecks: int,
    requisites: str,
) -> Optional[int]:
    """
    Создать заявку на вывод средств (в транзакции со списанием баланса).
    Advisory lock по telegram_id для защиты от гонок.

    Args:
        telegram_id: Telegram ID пользователя
        username: Username (опционально)
        amount_kopecks: Сумма в копейках
        requisites: Реквизиты (СБП, карта, счёт)

    Returns:
        ID созданной заявки или None при ошибке/недостатке средств
    """
    if amount_kopecks <= 0:
        logger.error(f"Invalid amount_kopecks for create_withdrawal_request: {amount_kopecks}")
        return None
    if not _core.DB_READY:
        logger.warning("DB not ready, create_withdrawal_request skipped")
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    logger.error(f"User {telegram_id} not found for withdrawal")
                    return None
                
                current = row["balance"]

                if current < amount_kopecks:
                    logger.warning(f"Insufficient balance for withdrawal: user={telegram_id}, balance={current}, amount={amount_kopecks}")
                    return None

                # Игровые деньги выводу не подлежат. Считаем внутри той же
                # транзакции и под тем же advisory-локом, что и списание, —
                # иначе между проверкой и списанием можно успеть собрать
                # урожай и вывести намайненное.
                breakdown = await get_balance_breakdown(telegram_id, conn=conn)
                if amount_kopecks > breakdown["withdrawable"]:
                    logger.warning(
                        "WITHDRAWAL_REJECTED_GAME_FUNDS user=%s amount=%s "
                        "balance=%s withdrawable=%s game_locked=%s",
                        telegram_id, amount_kopecks, current,
                        breakdown["withdrawable"], breakdown["game_locked"],
                    )
                    return None

                # Обновляем баланс (строка уже заблокирована FOR UPDATE)
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
                    amount_kopecks, telegram_id
                )
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                       VALUES ($1, $2, $3, $4, $5)""",
                    telegram_id, -amount_kopecks, "withdrawal", "withdrawal_request",
                    f"Вывод средств: {requisites[:50]}"
                )
                row = await conn.fetchrow(
                    """INSERT INTO withdrawal_requests (telegram_id, username, amount, requisites, status)
                       VALUES ($1, $2, $3, $4, 'pending')
                       RETURNING id""",
                    telegram_id, username, amount_kopecks, requisites
                )
                wid = row["id"]
                
                # Structured logging with correlation_id
                correlation_id = str(uuid_lib.uuid4())
                logger.info(
                    f"WITHDRAWAL_REQUEST_CREATED withdrawal_id={wid} user={telegram_id} "
                    f"amount={amount_kopecks} kopecks correlation_id={correlation_id}"
                )
                return wid
            except Exception as e:
                logger.exception(f"Error creating withdrawal request for user {telegram_id}: {e}")
                return None


async def get_withdrawal_request(wid: int) -> Optional[Dict[str, Any]]:
    """Получить заявку на вывод по ID."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM withdrawal_requests WHERE id = $1", wid)
        return dict(row) if row else None


async def approve_withdrawal_request(wid: int, processed_by: int) -> bool:
    """Подтвердить заявку (status=approved). Средства уже списаны при создании."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            # CRITICAL: SELECT FOR UPDATE для защиты от двойного подтверждения
            row = await conn.fetchrow(
                "SELECT id FROM withdrawal_requests WHERE id = $1 AND status = 'pending' FOR UPDATE",
                wid
            )
            if not row:
                return False
            
            # Обновляем статус
            result = await conn.execute(
                "UPDATE withdrawal_requests SET status = 'approved', processed_at = NOW(), processed_by = $1 WHERE id = $2",
                processed_by, wid
            )
            if result == "UPDATE 1":
                # Structured logging
                logger.info(f"WITHDRAWAL_APPROVED withdrawal_id={wid} processed_by={processed_by}")
                return True
            return False


async def reject_withdrawal_request(wid: int, processed_by: int) -> bool:
    """Отклонить заявку и вернуть средства на баланс."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, telegram_id, amount FROM withdrawal_requests WHERE id = $1 AND status = 'pending' FOR UPDATE",
                wid
            )
            if not row:
                return False
            telegram_id = row["telegram_id"]
            amount_kopecks = row["amount"]
            
            # CRITICAL: advisory lock per user для защиты от race conditions
            await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
            
            # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
            user_row = await conn.fetchrow(
                "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                telegram_id
            )
            
            if not user_row:
                logger.error(f"User {telegram_id} not found for withdrawal rejection refund")
                return False
            
            # Обновляем баланс (строка уже заблокирована FOR UPDATE)
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                amount_kopecks, telegram_id
            )
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, "refund", "withdrawal_rejected",
                f"Возврат средств: заявка #{wid} отклонена"
            )
            await conn.execute(
                "UPDATE withdrawal_requests SET status = 'rejected', processed_at = NOW(), processed_by = $1 WHERE id = $2",
                processed_by, wid
            )
            # Structured logging
            logger.info(
                f"WITHDRAWAL_REJECTED withdrawal_id={wid} processed_by={processed_by} "
                f"user={telegram_id} refunded={amount_kopecks} kopecks"
            )
            return True


async def find_user_by_id_or_username(telegram_id: Optional[int] = None, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Найти пользователя по Telegram ID или username

    Args:
        telegram_id: Telegram ID пользователя (опционально)
        username: Username пользователя без @ (опционально)

    Returns:
        Словарь с данными пользователя или None, если не найден

    Note:
        Должен быть указан хотя бы один параметр. Если указаны оба, приоритет у telegram_id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if telegram_id is not None:
            # Поиск по ID имеет приоритет
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1", telegram_id
            )
            return dict(row) if row else None
        elif username is not None:
            # Поиск по username (case-insensitive)
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE LOWER(username) = LOWER($1)", username
            )
            return dict(row) if row else None
        else:
            return None


async def search_users_dashboard(query: str, limit: int = 25) -> list:
    """Substring search across all users for the admin dashboard.

    Matches either telegram_id (treated as text, so prefix typing
    works) or username (case-insensitive substring). Returns up to
    `limit` rows ranked by relevance:
        1. exact telegram_id  → top
        2. exact username (case-insensitive)
        3. telegram_id / username starting with `q`
        4. anywhere-substring
    Tie-break by newest first so freshly registered users surface
    above stale ghosts.

    Each row carries the minimum the UI needs to render a result
    list: telegram_id, username, language, created_at, and a
    has_active_sub flag so the admin sees "paying / not paying"
    at a glance before opening the full card."""
    pool = await get_pool()
    if pool is None:
        return []
    q = (query or "").strip().lstrip("@")
    if not q:
        return []
    pattern_any = f"%{q}%"
    pattern_prefix = f"{q}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT
                   u.telegram_id,
                   u.username,
                   u.language,
                   u.created_at,
                   EXISTS (
                       SELECT 1 FROM subscriptions s
                       WHERE s.telegram_id = u.telegram_id
                         AND s.status = 'active'
                         AND s.expires_at > NOW()
                   ) AS has_active_sub
               FROM users u
               WHERE CAST(u.telegram_id AS TEXT) ILIKE $1
                  OR u.username ILIKE $1
               ORDER BY
                   CASE
                       WHEN CAST(u.telegram_id AS TEXT) = $2 THEN 0
                       WHEN LOWER(u.username) = LOWER($2) THEN 1
                       WHEN CAST(u.telegram_id AS TEXT) ILIKE $3 THEN 2
                       WHEN u.username ILIKE $3 THEN 3
                       ELSE 4
                   END,
                   u.created_at DESC NULLS LAST
               LIMIT $4""",
            pattern_any, q, pattern_prefix, limit,
        )
    return [dict(r) for r in rows]


async def update_user_language(telegram_id: int, language: str):
    """Обновить язык пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET language = $1 WHERE telegram_id = $2",
            language, telegram_id
        )


async def update_username(telegram_id: int, username: Optional[str]):
    """Обновить username пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET username = $1 WHERE telegram_id = $2",
            username, telegram_id
        )


