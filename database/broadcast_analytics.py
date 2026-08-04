"""Журнал отправок и отчёты по рассылкам, включая A/B-тесты.

ЧТО ЗДЕСЬ
    Запись факта отправки в broadcast_log и всё чтение поверх него:
    доставлено/не доставлено, конверсия и выручка по окнам, список
    последних рассылок, id сообщений для массового удаления, разрез по
    вариантам A/B.

ПОЧЕМУ ОТДЕЛЬНО
    Отчёты правят, когда меняется вопрос к данным, а не когда меняется
    рассылка. Соседние модули (запись о рассылке, сегменты) при этом не
    должны шевелиться.

ЧТО ЛЕГКО СЛОМАТЬ
    Статус 'deleted' означает «доставлено, но потом сообщение удалили».
    Такие получатели считаются доставленными: продажа, случившаяся до
    удаления, никуда не делась. Выкинуть их из знаменателя значит завысить
    конверсию.

    Окна конверсии считаются от sent_at КАЖДОГО получателя, а не от одного
    момента рассылки: отправка растягивается, и общая точка отсчёта врёт.

    Оплата ищется по статусам ('paid', 'approved') — у разных провайдеров
    прижилось по-разному. Оставить один статус значит потерять часть
    выручки в отчёте.
"""
import logging
from typing import Any, Dict, Optional

from database.core import get_pool

logger = logging.getLogger(__name__)


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
