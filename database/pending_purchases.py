"""Отложенные покупки: запись о намерении оплатить и её жизненный цикл.

ЧТО ТАКОЕ PENDING PURCHASE
    Строка в pending_purchases создаётся ДО оплаты — в момент, когда
    пользователь выбрал товар и получил ссылку на оплату. В ней зафиксированы
    цена, тариф, срок и промокод. Когда приходит вебхук от провайдера, сумма
    сверяется именно с этой записью: так пользователь не может оплатить
    меньше, чем стоил товар на момент выбора.

ЖИЗНЕННЫЙ ЦИКЛ
    pending → paid      обычный путь, оплата подтверждена
    pending → expired   истёк срок ожидания оплаты
    expired → paid      деньги всё же пришли; запись восстанавливается,
                        потому что платёж нельзя терять из-за таймаута

ПОЧЕМУ ЗДЕСЬ ЖИВУТ ПЕРЕЧНИ ТИПОВ
    PURCHASE_TYPES и TARIFF_* задают допустимые значения CHECK-констрейнтов
    таблицы. Они здесь, а не в тексте SQL, потому что при расхождении схемы
    констрейнт восстанавливается в рантайме: тип покупки, забытый в перечне,
    навсегда сделал бы соответствующую покупку невозможной.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/subscriptions.py. Здесь только учёт намерения
    оплатить; выдача товара и проводка денег — в finalize_purchase, которая
    осталась в subscriptions.py вместе с транзакционной логикой.
"""
import asyncpg
import logging
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


async def create_pending_balance_topup_purchase(
    telegram_id: int,
    amount_kopecks: int,
) -> str:
    """
    Create pending purchase for balance top-up only.
    No tariff, no period_days. Separate from subscription logic.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        await conn.execute(
            """INSERT INTO pending_purchases (purchase_id, telegram_id, purchase_type, price_kopecks, status, expires_at)
               VALUES ($1, $2, 'balance_topup', $3, 'pending', $4)""",
            purchase_id, telegram_id, amount_kopecks, _to_db_utc(expires_at)
        )
        logger.info(
            f"BALANCE_TOPUP_PURCHASE_CREATED purchase_id={purchase_id} telegram_id={telegram_id} "
            f"amount={amount_kopecks} kopecks"
        )
        return purchase_id


# Единый перечень типов покупок и тарифов для CHECK-констрейнтов
# pending_purchases. Держится здесь, а не в тексте SQL, потому что список
# восстанавливается в рантайме при расхождении схемы: пропущенный тип
# означает, что соответствующая покупка перестанет создаваться навсегда.
PURCHASE_TYPES = (
    "subscription", "balance_topup", "gift", "telegram_premium",
    "telegram_stars", "traffic_pack", "apple_id", "spotify",
    "steam", "proxy", "farm_effect",
)

TARIFF_VALUES = (
    "basic", "plus", "biz_starter", "biz_team", "biz_business",
    "biz_pro", "biz_enterprise", "biz_ultimate",
    "telegram_premium", "telegram_stars",
)

TARIFF_PREFIXES = ("traffic_", "apple_id_", "bypass_", "spotify_", "steam_")

_PURCHASE_TYPES_SQL = ", ".join(f"'{t}'" for t in PURCHASE_TYPES)
_TARIFF_VALUES_SQL = ", ".join(f"'{t}'" for t in TARIFF_VALUES)
_TARIFF_PREFIXES_SQL = " OR ".join(f"tariff LIKE '{p}%'" for p in TARIFF_PREFIXES)


async def create_pending_purchase(
    telegram_id: int,
    tariff: str,  # "basic", "plus", or "biz_*"
    period_days: int,
    price_kopecks: int,
    promo_code: Optional[str] = None,
    country: Optional[str] = None,
    purchase_type: str = "subscription",
    is_combo: bool = False,
    farm_plot_id: Optional[int] = None,
) -> str:
    """
    Создать pending покупку с уникальным purchase_id

    Args:
        telegram_id: Telegram ID пользователя
        tariff: Тип тарифа ("basic" или "plus")
        period_days: Период в днях (30, 90, 180, 365)
        price_kopecks: Цена в копейках
        promo_code: Промокод (опционально)
        purchase_type: Тип покупки ("subscription", "gift", "balance_topup")

    Returns:
        purchase_id: Уникальный ID покупки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Отменяем все предыдущие pending покупки этого пользователя
        await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )

        # Генерируем уникальный purchase_id
        purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"

        # Срок действия контекста покупки (30 минут)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        # Соз��аем запись о по��упке
        _insert_sql = """INSERT INTO pending_purchases (purchase_id, telegram_id, purchase_type, tariff, period_days, price_kopecks, promo_code, status, expires_at, country, is_combo, farm_plot_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"""
        _insert_args = (purchase_id, telegram_id, purchase_type, tariff, period_days, price_kopecks, promo_code, "pending", _to_db_utc(expires_at), country, is_combo, farm_plot_id)
        try:
            await conn.execute(_insert_sql, *_insert_args)
        except Exception as e:
            if "purchase_type_check" in str(e) or "tariff_check" in str(e):
                # Аварийная починка CHECK-констрейнтов.
                #
                # Опасность этого места: констрейнт пересоздаётся по списку,
                # записанному прямо здесь. Раньше в списке не было steam, proxy
                # и farm_effect, поэтому одно срабатывание навсегда делало эти
                # покупки невозможными — то есть «починка» ломала три рабочих
                # сценария. Списки вынесены в константы модуля, чтобы новый тип
                # покупки нельзя было забыть в одном из двух мест.
                logger.error(
                    "create_pending_purchase: схема расходится с кодом, "
                    "восстанавливаю CHECK-констрейнты (purchase_type=%s, tariff=%s)",
                    purchase_type, tariff,
                )
                await conn.execute("ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check")
                await conn.execute(
                    "ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check "
                    f"CHECK (purchase_type IN ({_PURCHASE_TYPES_SQL}))"
                )
                await conn.execute("ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check")
                await conn.execute(
                    "ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check "
                    f"CHECK (tariff IS NULL OR tariff IN ({_TARIFF_VALUES_SQL}) OR {_TARIFF_PREFIXES_SQL})"
                )
                await conn.execute(_insert_sql, *_insert_args)
            else:
                raise

        # country НЕ всегда страна.
        #
        # Spotify и Steam переиспользуют колонки этой таблицы под учётные
        # данные покупателя: country хранит email (Spotify) или логин (Steam),
        # а promo_code — пароль от аккаунта Spotify
        # (app/handlers/payments/spotify_purchase.py:_create_pending).
        # Строка ниже писала country как есть, поэтому на каждой покупке
        # Spotify в лог уровня INFO уходил email аккаунта рядом с telegram_id
        # покупателя. Пароль в лог не идёт и идти не должен — promo_code
        # здесь не логируется намеренно.
        _CREDENTIAL_PURCHASE_TYPES = ("spotify", "steam")
        _country_for_log = (
            "<redacted:account>"
            if country and (
                purchase_type in _CREDENTIAL_PURCHASE_TYPES
                or (tariff or "").startswith(("spotify_", "steam_"))
            )
            else country
        )
        logger.info(f"Pending purchase created: purchase_id={purchase_id}, telegram_id={telegram_id}, tariff={tariff}, period_days={period_days}, price={price_kopecks} kopecks, country={_country_for_log}")

        return purchase_id


async def get_pending_purchase(purchase_id: str, telegram_id: int, check_expiry: bool = True) -> Optional[Dict[str, Any]]:
    """
    Получить pending покупку по purchase_id с валидацией
    
    Args:
        purchase_id: ID покупки
        telegram_id: Telegram ID пользователя
        check_expiry: Проверять ли срок действия (по умолчанию True, False для оплаты)
    
    Returns:
        Словарь с данными покупки, если валидна, иначе None
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_pending_purchase skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_pending_purchase skipped")
        return None
    async with pool.acquire() as conn:
        if check_expiry:
            # При обычной проверке (создание покупки) проверяем срок действия
            purchase = await conn.fetchrow(
                """SELECT * FROM pending_purchases 
                   WHERE purchase_id = $1 AND telegram_id = $2 AND status = 'pending' AND expires_at > NOW()""",
                purchase_id, telegram_id
            )
        else:
            # При оплате (webhook) не проверяем срок - покупка может быть оплачена после expires_at
            purchase = await conn.fetchrow(
                """SELECT * FROM pending_purchases 
                   WHERE purchase_id = $1 AND telegram_id = $2 AND status = 'pending'""",
                purchase_id, telegram_id
            )
        
        if purchase:
            return dict(purchase)
        else:
            logger.warning(f"Invalid pending purchase: purchase_id={purchase_id}, telegram_id={telegram_id}, check_expiry={check_expiry}")
            return None


async def get_pending_purchase_by_id(purchase_id: str, check_expiry: bool = False) -> Optional[Dict[str, Any]]:
    """
    Get pending purchase by purchase_id only (for webhook when payload is "purchase:{id}").
    
    Args:
        purchase_id: ID покупки
        check_expiry: Проверять ли срок действия (по умолчанию False для webhook)
    
    Returns:
        Словарь с данными покупки или None
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        if check_expiry:
            row = await conn.fetchrow(
                """SELECT * FROM pending_purchases
                   WHERE purchase_id = $1 AND status = 'pending' AND expires_at > NOW()""",
                purchase_id
            )
        else:
            # For webhooks: accept both 'pending' and 'expired' — payment may arrive
            # after user created a new purchase (which expired the old one)
            row = await conn.fetchrow(
                """SELECT * FROM pending_purchases
                   WHERE purchase_id = $1 AND status IN ('pending', 'expired')""",
                purchase_id
            )
        return dict(row) if row else None


async def cancel_pending_purchases(telegram_id: int, reason: str = "user_action") -> None:
    """
    Отменить все pending покупки пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        reason: Причина отмены
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        
        if result != "UPDATE 0":
            logger.info(f"Pending purchases cancelled: telegram_id={telegram_id}, reason={reason}")


async def update_pending_purchase_invoice_id(purchase_id: str, invoice_id: str) -> bool:
    """
    Обновить provider_invoice_id для pending покупки
    
    Args:
        purchase_id: ID покупки
        invoice_id: Invoice ID от платежного провайдера
    
    Returns:
        True если успешно, False если покупка не найдена
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Для crypto purchases устанавливаем TTL = 30 минут с момента создания invoice
        now_utc = datetime.now(timezone.utc)
        expires_at_utc = now_utc + timedelta(minutes=30)
        
        result = await conn.execute(
            "UPDATE pending_purchases SET provider_invoice_id = $1, expires_at = $3 WHERE purchase_id = $2 AND status = 'pending'",
            invoice_id, purchase_id, _to_db_utc(expires_at_utc)
        )
        
        if result == "UPDATE 1":
            logger.info(f"Pending purchase invoice_id updated: purchase_id={purchase_id}, invoice_id={invoice_id}")
            return True
        else:
            logger.warning(f"Failed to update pending purchase invoice_id: purchase_id={purchase_id}, result={result}")
            return False


async def mark_pending_purchase_paid(purchase_id: str) -> bool:
    """
    Пометить pending покупку как оплаченную
    
    Args:
        purchase_id: ID покупки
    
    Returns:
        True если успешно, False если покупка не найдена или уже оплачена
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status IN ('pending', 'expired')",
            purchase_id
        )

        if result == "UPDATE 1":
            logger.info(f"Pending purchase marked as paid: purchase_id={purchase_id}")
            return True
        else:
            logger.warning(f"Failed to mark pending purchase as paid: purchase_id={purchase_id}, result={result}")
            return False


async def has_purchased_proxy(telegram_id: int) -> bool:
    """True if the user already owns the standalone Telegram-proxy product.

    Tolerates a missing proxy_purchased_at column (migration 051 not applied
    yet) by treating the user as a non-owner instead of raising.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT proxy_purchased_at FROM users WHERE telegram_id = $1",
                telegram_id,
            )
    except asyncpg.UndefinedColumnError:
        logger.warning(
            "has_purchased_proxy: users.proxy_purchased_at missing — migration 051 not applied"
        )
        return False
    return bool(row and row["proxy_purchased_at"] is not None)


async def mark_proxy_purchased(telegram_id: int) -> None:
    """Record that the user owns the Telegram-proxy product (idempotent).

    Keeps the first purchase timestamp — re-running never overwrites it.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, proxy_purchased_at)
            VALUES ($1, CURRENT_TIMESTAMP)
            ON CONFLICT (telegram_id) DO UPDATE
                SET proxy_purchased_at = COALESCE(
                    users.proxy_purchased_at, CURRENT_TIMESTAMP
                )
            """,
            telegram_id,
        )
