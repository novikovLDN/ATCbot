"""
Scheduled broadcasts worker — раз в минуту проверяет БД на «пора запускать»
и создаёт обычные broadcast'ы через тот же путь, что и ручное создание.

Идея: не дублировать send-логику. Мы просто вставляем строку в broadcasts,
разрешаем сегмент, дёргаем send_broadcast — всё как admin вручную нажал бы
«Отправить».

Rescheduling для recurring: mark_ran_and_reschedule сам считает следующий
scheduled_at и деактивирует, если once или вышли за end_at.

Ошибки одного задания не роняют worker — логируем и идём дальше.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from aiogram import Bot

logger = logging.getLogger(__name__)


POLL_INTERVAL_SECONDS = 60          # раз в минуту
MAX_BATCH_PER_TICK = 5              # не больше N задач за один tick


async def _dispatch_one(bot: Bot, sched: Dict[str, Any]) -> None:
    """Запустить одно задание — создать broadcast + отправить."""
    import database
    from app.api.dashboard.routes.broadcasts import (
        normalize_premium_emoji, _build_reply_markup,
    )
    from app.services.broadcast_sender import send_broadcast
    from app.events import bus

    sched_id = int(sched["id"])
    try:
        user_ids = await database.get_users_by_segment(sched["segment"])
    except Exception as e:
        logger.exception("SCHED_BROADCAST_SEGMENT_FAIL sched=%s err=%s", sched_id, e)
        await database.mark_ran_and_reschedule(
            sched_id, last_broadcast_id=None,
            error=f"segment_resolve_failed: {e}"[:200],
        )
        return
    if not user_ids:
        logger.info("SCHED_BROADCAST_EMPTY_AUDIENCE sched=%s segment=%s",
                    sched_id, sched["segment"])
        await database.mark_ran_and_reschedule(
            sched_id, last_broadcast_id=None,
            error="empty_audience",
        )
        return

    message_html = normalize_premium_emoji(sched["message"])
    try:
        broadcast_id = await database.create_broadcast(
            title=sched["title"],
            message=message_html,
            broadcast_type="scheduled",
            segment=sched["segment"],
            sent_by=int(sched["created_by"]),
            photo_file_id=sched.get("photo_file_id"),
            animation_file_id=sched.get("animation_file_id"),
            buttons=list(sched.get("buttons") or []) or None,
        )
    except Exception as e:
        logger.exception("SCHED_BROADCAST_CREATE_FAIL sched=%s err=%s", sched_id, e)
        await database.mark_ran_and_reschedule(
            sched_id, last_broadcast_id=None,
            error=f"create_broadcast_failed: {e}"[:200],
        )
        return

    # Discount / gift_reveal metadata — если были у snapshot'а.
    try:
        if (
            (sched.get("discount_percent") or sched.get("discount_hours"))
            and (
                (sched.get("buttons") or [])
                and any(b in ("promo_buy", "promo_traffic") for b in sched["buttons"])
            )
        ):
            await database.save_broadcast_discount(
                broadcast_id,
                int(sched["discount_percent"] or 0),
                int(sched.get("discount_hours") or 168),
                sched.get("discount_label") or f"{sched.get('discount_hours') or 168} часов",
            )
    except Exception as e:
        logger.warning("SCHED_BROADCAST_DISC_FAIL sched=%s err=%s", sched_id, e)

    try:
        if (
            "gift_reveal" in (sched.get("buttons") or [])
            and sched.get("gift_reveal_percent")
        ):
            await database.save_broadcast_gift_reveal_percent(
                broadcast_id, int(sched["gift_reveal_percent"]),
            )
    except Exception as e:
        logger.warning("SCHED_BROADCAST_GIFTREV_FAIL sched=%s err=%s", sched_id, e)

    reply_markup = _build_reply_markup(
        list(sched.get("buttons") or []),
        broadcast_id,
        sched.get("discount_percent"),
    )

    # Отдельным таском, чтобы не блокировать полинг: send_broadcast может
    # длиться минуты для большой аудитории.
    asyncio.create_task(send_broadcast(
        bot=bot,
        broadcast_id=broadcast_id,
        user_ids=list(user_ids),
        message=message_html,
        reply_markup=reply_markup,
        photo_file_id=sched.get("photo_file_id"),
        animation_file_id=sched.get("animation_file_id"),
        admin_telegram_id=int(sched["created_by"]),
    ))

    bus.publish({
        "type": "broadcast:created",
        "broadcast_id": broadcast_id,
        "audience": len(user_ids),
        "by": sched["created_by"],
        "scheduled_id": sched_id,
    })

    try:
        await database.mark_ran_and_reschedule(
            sched_id, last_broadcast_id=broadcast_id, error=None,
        )
    except Exception as e:
        logger.exception("SCHED_BROADCAST_MARK_FAIL sched=%s err=%s", sched_id, e)

    logger.info(
        "SCHED_BROADCAST_DISPATCHED sched=%s broadcast=%s audience=%s",
        sched_id, broadcast_id, len(user_ids),
    )


async def run_scheduled_broadcasts_worker(bot: Bot) -> None:
    """Основной цикл. Запускается из main.py как asyncio.create_task."""
    logger.info(
        "SCHEDULED_BROADCASTS_WORKER started (poll=%ss, batch=%s)",
        POLL_INTERVAL_SECONDS, MAX_BATCH_PER_TICK,
    )
    while True:
        try:
            import database
            due = await database.fetch_due_scheduled(limit=MAX_BATCH_PER_TICK)
            for sched in due:
                try:
                    await _dispatch_one(bot, sched)
                except Exception as e:
                    logger.exception(
                        "SCHEDULED_BROADCASTS_WORKER_ITEM_ERR id=%s: %s",
                        sched.get("id"), e,
                    )
        except asyncio.CancelledError:
            logger.info("SCHEDULED_BROADCASTS_WORKER stopped")
            return
        except Exception as e:
            logger.exception("SCHEDULED_BROADCASTS_WORKER_TICK_ERR: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
