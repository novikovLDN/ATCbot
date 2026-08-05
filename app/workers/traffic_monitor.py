"""
Background worker: check traffic usage and send threshold notifications.

Runs every 5 minutes. Gated by REMNAWAVE_ENABLED and DB_READY.
"""
import asyncio
import logging
import os
import time
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.services import remnawave_api
from app.core.feature_flags import background_workers_paused
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # 5 minutes

# Потолок на один проход. Меньше интервала, чтобы проходы не наезжали
# друг на друга: следующий не должен стартовать, пока идёт предыдущий.
MAX_ITERATION_SECONDS = int(os.getenv("TRAFFIC_MONITOR_MAX_ITERATION_SECONDS", "240"))


def _format_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


async def _check_user_traffic(bot: Bot, telegram_id: int, rmn_uuid: str) -> None:
    """Check traffic thresholds and send one-shot notifications."""
    try:
        traffic = await remnawave_api.get_user_traffic(rmn_uuid)
        if not traffic:
            logger.warning("TRAFFIC_CHECK_NO_DATA: tg=%s uuid=%s", telegram_id, rmn_uuid[:8] if rmn_uuid else "N/A")
            return

        used = traffic["usedTrafficBytes"]
        limit = traffic["trafficLimitBytes"]
        if limit <= 0:
            return

        remaining = max(0, limit - used)

        flags = await database.get_traffic_notification_flags(telegram_id)
        if not flags:
            return

        for threshold_bytes, flag_key in config.TRAFFIC_NOTIFY_THRESHOLDS:
            # Порог должен быть строго меньше лимита юзера — иначе он
            # триггерится СРАЗУ после активации: у trial'а лимит 500 МБ,
            # но пороги 8/5/3/1 ГБ все ≥ 500 МБ, поэтому за первые
            # 30 минут летели 6 уведомлений подряд с текстом «купите
            # дополнительный трафик». Строгое неравенство также
            # гарантирует что порог 500 МБ не сработает для юзера
            # с лимитом ровно 500 МБ на моменте активации.
            # Особый случай: порог 0 ГБ (закончился трафик) — всегда
            # актуален, любой юзер должен узнать что доступ отключился.
            if threshold_bytes > 0 and threshold_bytes >= limit:
                continue
            if remaining <= threshold_bytes and not flags.get(flag_key, False):
                delivered = await _send_traffic_notification(
                    bot, telegram_id, remaining, flag_key,
                )
                await database.set_traffic_notification_flag(telegram_id, flag_key)
                if not delivered:
                    # Флаг однократности ставится независимо от исхода
                    # отправки, поэтому неудача здесь окончательна: повтора
                    # не будет никогда. Раньше это было видно только как
                    # TRAFFIC_NOTIFICATION_FAIL уровня warning, из которого
                    # не следовало, что уведомление потеряно навсегда.
                    #
                    # Порог traffic_notified_0 — это «трафик кончился, доступ
                    # отключился». Человек об этом не узнает, а по флагу в
                    # базе система считает, что узнал: для разбора обращения
                    # «внезапно перестал работать VPN» запись обязана быть
                    # заметной.
                    _level = logger.error if flag_key == "traffic_notified_0" else logger.warning
                    _level(
                        "TRAFFIC_NOTIFICATION_LOST: tg=%s flag=%s remaining=%d — "
                        "уведомление не доставлено, флаг однократности проставлен, "
                        "повтора не будет",
                        telegram_id, flag_key, remaining,
                    )
                break  # One notification per iteration

    except Exception as e:
        logger.warning("TRAFFIC_CHECK_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


async def _send_traffic_notification(
    bot: Bot,
    telegram_id: int,
    remaining_bytes: int,
    flag_key: str,
) -> bool:
    """Отправить предупреждение об остатке трафика.

    Возвращает True, только если Telegram принял сообщение. Раньше функция
    возвращала None при любом исходе, и вызывающий код не мог отличить
    доставленное уведомление от съеденного исключением — а флаг однократности
    ставился в обоих случаях.
    """
    try:
        language = await resolve_user_language(telegram_id)

        if flag_key == "traffic_notified_0":
            text = i18n_get_text(language, "traffic.notify_zero")
        elif flag_key == "traffic_notified_500mb":
            text = i18n_get_text(language, "traffic.notify_500mb", remaining=_format_bytes(remaining_bytes))
        elif flag_key == "traffic_notified_1gb":
            text = i18n_get_text(language, "traffic.notify_1gb")
        elif flag_key == "traffic_notified_3gb":
            text = i18n_get_text(language, "traffic.notify_3gb", remaining=_format_bytes(remaining_bytes))
        elif flag_key == "traffic_notified_5gb":
            text = i18n_get_text(language, "traffic.notify_5gb", remaining=_format_bytes(remaining_bytes))
        else:
            text = i18n_get_text(language, "traffic.notify_8gb", remaining=_format_bytes(remaining_bytes))

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_traffic_btn"),
                callback_data="buy_traffic",
            )],
        ])
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode="HTML")
        # Запись строго после ответа Telegram: до неё дойдёт только реально
        # принятое сообщение.
        logger.info("TRAFFIC_NOTIFICATION_SENT: tg=%s flag=%s remaining=%d", telegram_id, flag_key, remaining_bytes)
        return True
    except Exception as e:
        logger.warning("TRAFFIC_NOTIFICATION_FAIL: tg=%s flag=%s %s: %s", telegram_id, flag_key, type(e).__name__, e)
        return False


async def traffic_monitor_iteration(bot: Bot) -> None:
    """Один проход: проверить остаток трафика у активных пользователей.

    ПОЧЕМУ ЗДЕСЬ ЛИМИТ ПО ВРЕМЕНИ

        На каждого человека идёт запрос к панели плюс пауза 0.2 секунды
        под её лимиты — то есть проход по десяти тысячам записей займёт
        больше получаса. Интервал воркера — пять минут, и без ограничения
        проходы начали бы накладываться друг на друга: каждый следующий
        стартует, пока предыдущий ещё идёт.

        Дойдя до лимита, честно записываем, сколько успели. Следующий
        проход начнётся с начала списка — по остатку трафика это не
        страшно: у тех, кого не успели проверить, порог никуда не денется,
        а уведомление всё равно однократное (флаг в базе).
    """
    users = await database.get_active_remnawave_users()
    if not users:
        # Пустой проход раньше завершался вообще без записи, и «воркер жив,
        # проверять некого» было неотличимо от «воркер не запускался».
        logger.info("TRAFFIC_MONITOR_ITERATION_DONE: проверять некого (0 активных)")
        return

    started = time.monotonic()
    checked = 0

    for user in users:
        if time.monotonic() - started > MAX_ITERATION_SECONDS:
            logger.warning(
                "TRAFFIC_MONITOR_ITERATION_CAPPED: проверено %s из %s за %.0f с — "
                "остальные попадут в следующий проход",
                checked, len(users), MAX_ITERATION_SECONDS,
            )
            break

        telegram_id = user["telegram_id"]
        rmn_uuid = user["remnawave_uuid"]
        await _check_user_traffic(bot, telegram_id, rmn_uuid)
        checked += 1
        await asyncio.sleep(0.2)  # Rate limit API calls

    logger.info(
        "TRAFFIC_MONITOR_ITERATION_DONE: проверено %s из %s за %.0f с",
        checked, len(users), time.monotonic() - started,
    )


async def traffic_monitor_task(bot: Bot) -> None:
    """Main loop — runs every INTERVAL_SECONDS."""
    logger.info("TRAFFIC_MONITOR: starting (interval=%ds)", INTERVAL_SECONDS)
    await asyncio.sleep(30)  # Initial delay

    while True:
        # Аварийный рубильник фоновых воркеров. Проверяем внутри цикла:
        # флаг читается из окружения и может смениться без рестарта.
        if background_workers_paused("traffic_monitor"):
            await asyncio.sleep(300)
            continue
        try:
            if not database.DB_READY or not config.REMNAWAVE_ENABLED:
                await asyncio.sleep(INTERVAL_SECONDS)
                continue

            await traffic_monitor_iteration(bot)
        except asyncio.CancelledError:
            logger.info("TRAFFIC_MONITOR: cancelled")
            break
        except Exception as e:
            logger.error("TRAFFIC_MONITOR_ERROR: %s: %s", type(e).__name__, e)

        await asyncio.sleep(INTERVAL_SECONDS)
