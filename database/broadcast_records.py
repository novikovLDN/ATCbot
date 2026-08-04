"""Запись о рассылке и её настройки: текст, медиа, кнопки, скидки.

ЧТО ЗДЕСЬ
    Создание строки в broadcasts, чтение её по id и сохранение скидок,
    привязанных к кнопкам рассылки (broadcast_discounts).

ПОЧЕМУ ОТДЕЛЬНО
    Это «паспорт» рассылки — то, что заполняют один раз перед отправкой.
    Подбор получателей и отчёты по доставке живут в соседях и правятся по
    совсем другим поводам.

ЧТО ЛЕГКО СЛОМАТЬ
    create_broadcast и save_* содержат fallback на схему БЕЗ поздних
    миграций: INSERT с animation_file_id при ошибке повторяется без этой
    колонки, а нужные колонки досоздаются через ALTER ... IF NOT EXISTS
    прямо в рантайме. Это не красиво и не должно быть здесь, но убирать
    страховку нельзя, пока не подтверждено, что миграции 063 и 070
    накатаны везде: без неё создание рассылки просто падает.

    Две скидки лежат в ОДНОЙ строке broadcast_discounts, но в разных
    колонках: discount_percent (кнопки покупки) и gift_reveal_percent
    (кнопка «Посмотреть подарок»). Свести их в одну колонку значит
    заставить кнопки затирать друг друга внутри одной рассылки.
"""
import asyncpg
import logging
from typing import Any, Dict, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)


async def create_broadcast(
    title: str,
    message: str,
    broadcast_type: str,
    segment: str,
    sent_by: int,
    is_ab_test: bool = False,
    message_a: str = None,
    message_b: str = None,
    photo_file_id: Optional[str] = None,
    animation_file_id: Optional[str] = None,
    buttons: Optional[list] = None,
) -> int:
    """Создать новое уведомление.

    photo_file_id и animation_file_id мутуально-эксклюзивные — если
    заданы оба, animation имеет приоритет при отправке.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try new schema (migration 070 — animation_file_id).
        try:
            if is_ab_test:
                row = await conn.fetchrow(
                    """INSERT INTO broadcasts
                           (title, message_a, message_b, is_ab_test, type,
                            segment, sent_by, photo_file_id, animation_file_id, buttons)
                       VALUES ($1, $2, $3, TRUE, $4, $5, $6, $7, $8, $9)
                       RETURNING id""",
                    title, message_a, message_b, broadcast_type, segment, sent_by,
                    photo_file_id, animation_file_id, buttons,
                )
            else:
                row = await conn.fetchrow(
                    """INSERT INTO broadcasts
                           (title, message, is_ab_test, type, segment,
                            sent_by, photo_file_id, animation_file_id, buttons)
                       VALUES ($1, $2, FALSE, $3, $4, $5, $6, $7, $8)
                       RETURNING id""",
                    title, message, broadcast_type, segment, sent_by,
                    photo_file_id, animation_file_id, buttons,
                )
            return row["id"]
        except Exception as e:
            # Migration 070 не применена — колонки animation_file_id нет.
            # Fallback на pre-070 схему.
            if "animation_file_id" not in str(e):
                raise
            logger.warning(
                "broadcasts.animation_file_id missing (migration 070 not applied) "
                "— falling back to legacy INSERT",
            )
            if is_ab_test:
                row = await conn.fetchrow(
                    """INSERT INTO broadcasts
                           (title, message_a, message_b, is_ab_test, type,
                            segment, sent_by, photo_file_id, buttons)
                       VALUES ($1, $2, $3, TRUE, $4, $5, $6, $7, $8)
                       RETURNING id""",
                    title, message_a, message_b, broadcast_type, segment, sent_by,
                    photo_file_id, buttons,
                )
            else:
                row = await conn.fetchrow(
                    """INSERT INTO broadcasts
                           (title, message, is_ab_test, type, segment,
                            sent_by, photo_file_id, buttons)
                       VALUES ($1, $2, FALSE, $3, $4, $5, $6, $7)
                       RETURNING id""",
                    title, message, broadcast_type, segment, sent_by,
                    photo_file_id, buttons,
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


async def save_broadcast_gift_reveal_percent(broadcast_id: int, gift_reveal_percent: int) -> None:
    """Save the gift_reveal-скидка (%) chosen by the admin for this broadcast.

    Отдельная колонка от promo_buy/promo_traffic-скидки — в одной рассылке
    можно спокойно комбинировать «🎁 Посмотреть подарок» с другими скидочными
    кнопками, они не будут перезаписывать друг друга. Duration зашита в 48ч
    в коде callback'а, не варьируется.

    Идемпотентно: если строка broadcast_discounts уже есть (например от
    promo_buy), просто апдейтим колонку; если нет — вставляем с
    placeholder-нулём в discount_percent.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # На случай если миграция 063 ещё не накатана — гарантируем колонку.
        # Отдельная tx на ALTER, чтоб исключение не убило основную транзакцию.
        try:
            await conn.execute(
                "ALTER TABLE broadcast_discounts ADD COLUMN IF NOT EXISTS gift_reveal_percent INTEGER"
            )
        except Exception as alter_err:
            logger.warning(
                "SAVE_GIFT_REVEAL: ALTER TABLE failed (может уже есть): %s",
                alter_err,
            )
        # discount_hours / discount_label были добавлены в save_broadcast_discount'e
        # через ALTER IF NOT EXISTS — если строка ещё не создана и эти колонки
        # NOT NULL с DEFAULT'ами, INSERT должен пройти. Но на всякий случай
        # явно даём значения — чтоб не зависеть от порядка миграций.
        try:
            await conn.execute(
                """INSERT INTO broadcast_discounts (
                       broadcast_id, discount_percent, gift_reveal_percent,
                       discount_hours, discount_label
                   )
                   VALUES ($1, 0, $2, 48, '48 часов')
                   ON CONFLICT (broadcast_id) DO UPDATE
                   SET gift_reveal_percent = EXCLUDED.gift_reveal_percent""",
                broadcast_id, gift_reveal_percent,
            )
            logger.info(
                "SAVE_GIFT_REVEAL_OK broadcast_id=%s percent=%s",
                broadcast_id, gift_reveal_percent,
            )
        except Exception as ins_err:
            # Fallback: сначала попробуем чистый UPDATE (если строка уже есть).
            # Затем — INSERT без gift_reveal_percent-колонки (для DB, где ALTER
            # не прошёл) — записываем в discount_percent как последний рубеж,
            # чтобы juzер получил хоть какую-то скидку.
            logger.warning(
                "SAVE_GIFT_REVEAL: INSERT failed broadcast_id=%s err=%s "
                "— пробую UPDATE-only",
                broadcast_id, ins_err,
            )
            try:
                await conn.execute(
                    """UPDATE broadcast_discounts
                       SET gift_reveal_percent = $2
                       WHERE broadcast_id = $1""",
                    broadcast_id, gift_reveal_percent,
                )
                logger.info(
                    "SAVE_GIFT_REVEAL_UPDATE_OK broadcast_id=%s percent=%s",
                    broadcast_id, gift_reveal_percent,
                )
            except Exception as upd_err:
                logger.error(
                    "SAVE_GIFT_REVEAL_TOTAL_FAIL broadcast_id=%s: %s",
                    broadcast_id, upd_err,
                )
                raise


async def get_broadcast_discount(broadcast_id: int) -> Optional[Dict[str, Any]]:
    """Get discount info for a broadcast promo button."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM broadcast_discounts WHERE broadcast_id = $1",
            broadcast_id
        )
        return dict(row) if row else None


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
