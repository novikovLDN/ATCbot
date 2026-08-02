"""Рассылки: создание, сегменты получателей, отправка, аналитика.

ЧТО ЗДЕСЬ ЕСТЬ
    Всё, что касается массовых сообщений: запись о рассылке, подбор
    получателей по сегменту, журнал отправок, статистика доставки,
    A/B-тесты и режим инцидента.

СЕГМЕНТЫ ПОЛУЧАТЕЛЕЙ
    get_users_by_segment — самая тяжёлая функция файла. Каждый сегмент это
    отдельный SQL-запрос со своими условиями (есть подписка, нет подписки,
    истекает, триальщики и так далее). Добавляя сегмент, обязательно
    исключайте пользователей, помеченных недоступными: писать в заблокированный
    чат бессмысленно и портит статистику доставки.

A/B-ТЕСТЫ
    У рассылки может быть два варианта текста. Вариант, который получил
    пользователь, записывается в журнал отправок — без этого посчитать
    конверсию по вариантам невозможно.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из database/admin.py, который разросся до 4900 строк и держал
    в себе одновременно рассылки, деньги, аналитику и админские действия
    над подписками.
"""
import asyncpg
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc, safe_int

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
            # Исключаем только тех, у кого есть активная **платная**
            # подписка — это юзеры, успешно конвертнувшиеся, им пуш
            # «триал истёк, купи подписку» уже не нужен. Активные
            # bypass-only/gift/admin_grant не считаем — у них нет
            # основной подписки, и наш пуш им релевантен.
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
                            AND s.source = 'payment'
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "paid_expired_1d":
            # Платная подписка (source='payment') истекла ровно
            # 1 сутки назад (бакет [NOW-2d, NOW-1d)). И сейчас нет
            # активной ПЛАТНОЙ — это churn-окно, классическая точка
            # реактивации. (Активный bypass/gift тут не считаем —
            # юзер всё равно без основной подписки.)
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
                         AND s2.source = 'payment'
                         AND s2.expires_at > (NOW() AT TIME ZONE 'UTC')
                   )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_expired_30d", "paid_lapsed_any"):
            # Реактивационные сегменты по subscription_history:
            #   paid_expired_30d → последний end_date платной транзакции
            #                      попал в [NOW-30d, NOW-1d], и сейчас
            #                      нет активной подписки в subscriptions.
            #   paid_lapsed_any  → когда-либо платил (purchase / renewal /
            #                      auto_renew) и сейчас неактивен —
            #                      максимальная реактивационная аудитория.
            #
            # Почему через subscription_history, а не subscriptions:
            # в subscriptions хранится ТЕКУЩЕЕ состояние подписки;
            # при renewal expires_at UPDATEится в будущее, а старое
            # значение не сохраняется. История истёкших — только в
            # subscription_history (см. column end_date).
            #
            # action_type для платных: purchase, renewal, auto_renew
            # (не 'payment' — то поле в subscriptions.source).
            window_clause = (
                "AND last_paid_end BETWEEN "
                "(NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days' "
                "AND (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'"
                if segment == "paid_expired_30d"
                else ""
            )
            rows = await conn.fetch(
                f"""WITH paid_history AS (
                       SELECT telegram_id, MAX(end_date) AS last_paid_end
                       FROM subscription_history
                       WHERE action_type IN ('purchase', 'renewal', 'auto_renew')
                       GROUP BY telegram_id
                   )
                   SELECT p.telegram_id FROM paid_history p
                   WHERE 1=1 {window_clause}
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = p.telegram_id
                           AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_bought_within_7d", "paid_bought_within_14d",
                         "paid_bought_within_30d"):
            # Юзер оформил платную подписку в течение последних N дней.
            # Читаем историю успешных платежей (status IN 'paid','approved').
            # Кумулятивное окно (NOT ровно-N-суток бакет) — все, кто
            # покупал хотя бы раз за N дней. Дубли по telegram_id
            # убираются через DISTINCT.
            days = int(segment.split("_")[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT DISTINCT p.telegram_id
                    FROM payments p
                    WHERE p.status IN ('paid', 'approved')
                      AND p.created_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_active_any":
            # Все юзеры у которых СЕЙЧАС идёт триал (не истёк, платной ещё нет).
            # Целевая аудитория для мидл-триал коммуникаций (день 2 из 3 и т.п.).
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.trial_used_at IS NOT NULL
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           > (NOW() AT TIME ZONE 'UTC')
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND s.source = 'payment'
                           AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_activated_today":
            # Активировали триал в течение последних 24 часов. Свежая ЦА
            # для welcome-серии, объяснения features и т.п.
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE trial_used_at IS NOT NULL
                     AND trial_used_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("trial_active_day1", "trial_active_day2",
                         "trial_active_day3"):
            # Триал активен И его активировали N-1..N дней назад.
            # Классические welcome-day2/day3 коммуникации:
            #   day1 → [NOW-24h, NOW]                → «первый день»
            #   day2 → [NOW-48h, NOW-24h)            → «уже 2 дня с нами»
            #   day3 → [NOW-72h, NOW-48h)            → «завтра закончится»
            # Ограничение trial_expires_at > NOW отсеивает истекшие триалы.
            day = int(segment.split("_")[-1].replace("day", ""))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.trial_used_at IS NOT NULL
                      AND u.trial_used_at <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{day - 1} hours' * 24
                      AND u.trial_used_at >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '{day} hours' * 24
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            > (NOW() AT TIME ZONE 'UTC')
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.source = 'payment'
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_expires_in_1d", "paid_expires_in_3d",
                         "paid_expires_in_7d", "paid_expires_in_14d"):
            # Платная подписка сейчас активна, кончается в течение N суток.
            # Точка renewal-подсказки — юзер ещё внутри, есть время оформить.
            # source='payment' — исключаем trial/admin_grant/gift (у них другой
            # renewal-flow).
            days = int(segment.rsplit("_", 1)[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT DISTINCT s.telegram_id FROM subscriptions s
                    WHERE s.source = 'payment'
                      AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '{days} days'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "trial_expired_within_6m":
            # КУМУЛЯТИВНОЕ окно: юзер активировал триал, тот истёк В ЛЮБОЙ
            # момент последних 180 дней (не exact-day bucket, а всё окно),
            # и с тех пор так и не купил → сейчас нет активной подписки.
            #
            # Смысл: покрывает всех «отвалившихся после триала за полгода».
            # Обычные trial_expired_Nd таргетируют точечно (N-ый день),
            # а этот — «все, кто когда-либо за полгода не сконвертился».
            #
            # Условия:
            #   trial_used_at IS NOT NULL
            #   AND trial_expires_at ∈ [NOW-180d, NOW]  (истёк за полгода)
            #   AND нет ни одной s.source='payment' (никогда не покупал)
            #   AND нет ни одной активной подписки
            rows = await conn.fetch(
                """SELECT u.telegram_id FROM users u
                   WHERE u.trial_used_at IS NOT NULL
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           <= (NOW() AT TIME ZONE 'UTC')
                     AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                           >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '180 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND s.source = 'payment'
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.telegram_id = u.telegram_id
                           AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("trial_expired_7d", "trial_expired_14d",
                         "trial_expired_30d", "trial_expired_60d",
                         "trial_expired_90d", "trial_expired_180d",
                         "trial_expired_365d"):
            # Триал истёк N дней назад — И пользователь никогда не покупал
            # (нет ни одной строки в subscriptions с source='payment').
            # Это чистая «холодная реактивация» — прошло много времени,
            # человек не сконвертился, шлём ему повторный оффер.
            # Бакеты 24-часовые, окно вокруг ровно N-дневной точки:
            #   trial_expired_7d  → (NOW-8d,  NOW-7d]
            #   trial_expired_14d → (NOW-15d, NOW-14d]
            #   trial_expired_30d → (NOW-31d, NOW-30d]
            #   trial_expired_90d → (NOW-91d, NOW-90d]
            # См. коммент про tz в trial_ends_in_1d.
            days = int(segment.split("_")[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.trial_used_at IS NOT NULL
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'
                      AND COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')
                            >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days + 1} days'
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.source = 'payment'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND s.expires_at > (NOW() AT TIME ZONE 'UTC')
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("started_1d_cold", "started_3d_cold",
                         "started_14d_cold", "started_30d_cold"):
            # Холодные лиды — старт был не позднее N суток назад,
            # и до сих пор ноль активности (нет подписки, нет ключей,
            # нет триала). Cumulative-окно: включает всех, кто нажал
            # /start в диапазоне [NOW-N days, NOW]. Смысл — «свежие
            # молчуны» для прогрева. started_1d_cold = сегодняшние.
            days = int(segment.split("_")[1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE u.created_at >= NOW() - INTERVAL '{days} days'
                      AND u.trial_used_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM subscriptions s
                          WHERE s.telegram_id = u.telegram_id
                            AND (
                                s.expires_at > (NOW() AT TIME ZONE 'UTC')
                                OR s.remnawave_uuid IS NOT NULL
                                OR s.remnawave_premium_uuid IS NOT NULL
                            )
                      )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment in ("paid_expired_7d", "paid_expired_14d",
                         "paid_expired_60d", "paid_expired_90d",
                         "paid_expired_180d", "paid_expired_365d",
                         "paid_expired_730d"):
            # Платная (source='payment') истекла ровно N суток назад
            # (24-час бакет [NOW-(N+1)d, NOW-Nd)) — сейчас нет активной
            # ПЛАТНОЙ. Классическая точка реактивации, аналог paid_expired_1d
            # для более далёких окон.
            # См. tz-коммент в trial_ends_in_1d.
            days = int(segment.split("_")[-1].rstrip("d"))
            rows = await conn.fetch(
                f"""SELECT u.telegram_id FROM users u
                    WHERE EXISTS (
                        SELECT 1 FROM subscriptions s
                        WHERE s.telegram_id = u.telegram_id
                          AND s.source = 'payment'
                          AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days} days'
                          AND s.expires_at >  (NOW() AT TIME ZONE 'UTC') - INTERVAL '{days + 1} days'
                    )
                      AND NOT EXISTS (
                        SELECT 1 FROM subscriptions s2
                        WHERE s2.telegram_id = u.telegram_id
                          AND s2.source = 'payment'
                          AND s2.expires_at > (NOW() AT TIME ZONE 'UTC')
                    )"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "vip_active":
            # VIP-пользователи (users.is_vip=TRUE) — для эксклюзивных
            # приглашений/апселлов/фидбека.
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE is_vip = TRUE"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "combo_active":
            # Активные подписки типа combo_basic / combo_plus — целевая
            # для апселла на большие GB-паки обхода / доп. устройств.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id
                   FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.subscription_type IN ('combo_basic','combo_plus')"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "basic_active":
            # Активные Basic — целевая для апселла на Plus/Combo.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id
                   FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.subscription_type = 'basic'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "plus_active":
            # Активные Plus — целевая для upsell на Combo или продление на 1 год.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id
                   FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.subscription_type = 'plus'"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "discount_active":
            # У пользователя действует персональная скидка
            # (user_discounts) — стоит напомнить использовать её.
            rows = await conn.fetch(
                """SELECT DISTINCT ud.telegram_id
                   FROM user_discounts ud
                   WHERE (ud.expires_at IS NULL
                          OR ud.expires_at > (NOW() AT TIME ZONE 'UTC'))"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "has_balance_50plus":
            # На балансе > 50₽. Напомнить использовать балансовый чекаут.
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE COALESCE(balance_kopecks, 0) >= 5000"""
            )
            return [row["telegram_id"] for row in rows]
        elif segment == "expires_in_3d":
            # Активная подписка (любого типа) закончится в ближайшие
            # 72 часа — точка «продли/переоформи». Мощная реактивационная
            # аудитория, пока люди ещё внутри.
            rows = await conn.fetch(
                """SELECT DISTINCT s.telegram_id FROM subscriptions s
                   WHERE s.expires_at > (NOW() AT TIME ZONE 'UTC')
                     AND s.expires_at <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '3 days'
                     AND s.source = 'payment'"""
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


async def get_broadcast_analytics(broadcast_id: int) -> Dict[str, Any]:
    """Расширенная статистика по одной рассылке.

    Возвращает:
      - total_recipients   — сколько было в аудитории (sent + failed + deleted)
      - sent               — успешно доставлено
      - failed             — не доставлено (включая blocked)
      - deleted            — сообщение удалено пост-фактум (bulk-delete)
      - converted_1d/3d/7d — уникальные юзеры, купившие в течение окна
                             от sent_at (по любой успешной оплате в payments)
      - revenue_kop_1d/3d/7d — суммарный доход от этих оплат в копейках
      - conversion_rate_7d — converted_7d / sent (0..1)
      - blocked_estimate   — эвристика: доля failed от общего (0..1)

    Все окна считаются от `sent_at` каждого получателя (не от одной точки
    рассылки) — на случай ретаргетинг-рассылок, где отправка растянута.

    Совместимость статусов оплаты: как 'paid', так и 'approved' (в
    разных провайдерах прижилось по-разному).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Suммарные счётчики по статусам broadcast_log
        counts_row = await conn.fetchrow(
            """SELECT
                   COUNT(*) FILTER (WHERE status = 'sent')     AS sent,
                   COUNT(*) FILTER (WHERE status = 'failed')   AS failed,
                   COUNT(*) FILTER (WHERE status = 'deleted')  AS deleted,
                   COUNT(*)                                     AS total
               FROM broadcast_log
               WHERE broadcast_id = $1""",
            broadcast_id,
        )
        sent = int(counts_row["sent"] or 0)
        failed = int(counts_row["failed"] or 0)
        deleted = int(counts_row["deleted"] or 0)
        total = int(counts_row["total"] or 0)
        # Считаем deleted как «доставленных ранее» — они конвертились
        # ДО удаления, факт удаления сообщения не отменяет продажу.
        delivered = sent + deleted

        async def _conv_and_rev(hours: int) -> tuple[int, int]:
            row = await conn.fetchrow(
                f"""SELECT
                        COUNT(DISTINCT bl.telegram_id) AS conv,
                        COALESCE(SUM(p.amount), 0)     AS rev
                    FROM broadcast_log bl
                    JOIN payments p
                      ON p.telegram_id = bl.telegram_id
                     AND p.status IN ('paid', 'approved')
                     AND p.created_at BETWEEN bl.sent_at
                                          AND bl.sent_at + INTERVAL '{hours} hours'
                    WHERE bl.broadcast_id = $1
                      AND bl.status IN ('sent', 'deleted')""",
                broadcast_id,
            )
            return int(row["conv"] or 0), int(row["rev"] or 0)

        converted_1d, rev_1d = await _conv_and_rev(24)
        converted_3d, rev_3d = await _conv_and_rev(72)
        converted_7d, rev_7d = await _conv_and_rev(24 * 7)

        conversion_rate_7d = (converted_7d / delivered) if delivered > 0 else 0.0
        blocked_estimate = (failed / total) if total > 0 else 0.0

    return {
        "total_recipients": total,
        "sent": sent,
        "failed": failed,
        "deleted": deleted,
        "delivered": delivered,
        "converted_1d": converted_1d,
        "converted_3d": converted_3d,
        "converted_7d": converted_7d,
        "revenue_kop_1d": rev_1d,
        "revenue_kop_3d": rev_3d,
        "revenue_kop_7d": rev_7d,
        "conversion_rate_7d": round(conversion_rate_7d, 4),
        "blocked_estimate": round(blocked_estimate, 4),
    }


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
