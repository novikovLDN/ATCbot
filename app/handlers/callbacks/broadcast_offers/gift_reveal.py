"""Кнопка «Посмотреть подарок» из рассылки и возврат к тарифам.

ЧТО ЗДЕСЬ
    Reveal-сценка (👀 → пауза → 🎁), выдача персональной скидки на 48
    часов и экран тарифов с уже применённой скидкой. Плюс «Назад» с
    экрана периода обратно к выбору тарифа внутри этого сценария.

ПОЧЕМУ ВЫДЕЛЕНО
    Единственное предложение, где процент приходит из настроек рассылки,
    а сценка занимает больше кода, чем сама логика.

ЧТО ЛЕГКО СЛОМАТЬ
    Процент берётся из broadcast_discounts.gift_reveal_percent, и есть
    fallback на 20%. Fallback нужен для старых рассылок; убрав его,
    человек по клику не получит ничего — и в логах это будет выглядеть
    как «просто не сработало».

    Маркер from_broadcast=True в FSM ставится здесь, а читает его кнопка
    «Назад» на экране периода. Без маркера «Назад» уводит владельцев
    активной подписки в «Управление подпиской» — то есть из акции.

    Скидка выдаётся ПОСЛЕ сценки, но ДО экрана тарифов: экран считает
    цены через get_user_discount, и переставив вызовы, вы покажете цены
    без скидки.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

import config
import database

router = Router()
logger = logging.getLogger(__name__)

_GIFT_REVEAL_PERCENT_DEFAULT = 20  # fallback для рассылок без gift_reveal_percent в DB


# ──────────────────────────────────────────────────────────────────
# Gift Reveal — кнопка «Посмотреть подарок» в рассылке.
#
# UX flow:
#  1. Юзер кликает красную кнопку «Посмотреть подарок» в сообщении
#     рассылки.
#  2. В чат отправляется premium-эмодзи 👀 (id 5210956306952758910) —
#     один символ, эффект интриги.
#  3. Через 2 секунды — текст «Для тебя подарок 20% скидка на любую
#     подписку!» с premium 🎁 (id 5449800250032143374).
#  4. Через ~30 ms (просто отделить от reveal-сообщения, не моргание) —
#     экран выбора тарифов с уже применённой скидкой.
#
# Скидка: 20%, 48 часов. Параметры зафиксированы (не из dashboard-
# конфига broadcast'а) — это тематический подарок, единый для всех
# таких кнопок.
#
# Скидка применяется через стандартную `create_user_discount` — она
# работает на все основные тарифы (basic / plus / combo_basic /
# combo_plus) автоматически на экране тарифов через `get_user_discount`.

# Набор допустимых процентов (20/25/30/35/40) здесь не нужен: выбор
# делает админ в дашборде, и проверяет его дашборд же —
# app/api/dashboard/routes/broadcasts/send.py. Экран принимает любое
# число из БД, у него другая забота — fallback, когда там пусто.
_GIFT_REVEAL_HOURS = 48
_GIFT_REVEAL_EMOJI = '<tg-emoji emoji-id="5210956306952758910">👀</tg-emoji>'
_GIFT_REVEAL_PRESENT = '<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji>'


@router.callback_query(F.data.startswith("broadcast_gift_reveal:"))
async def callback_broadcast_gift_reveal(callback: CallbackQuery, state: FSMContext):
    """Кликнули «Посмотреть подарок» в рассылке — играем reveal-сценку
    и применяем скидку на 48ч, открываем экран тарифов.

    Процент скидки берётся из `broadcast_discounts.gift_reveal_percent`
    (админ выбрал в визарде: 20/25/30/35/40). Если по какой-то причине
    там пусто (старая рассылка до миграции 063, DB-ошибка) — fallback
    на legacy 20%, чтобы не оставлять юзера ни с чем.
    """
    await callback.answer()

    telegram_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else telegram_id

    # Определяем процент из БД. broadcast_id — второй элемент callback_data.
    percent = _GIFT_REVEAL_PERCENT_DEFAULT
    broadcast_id = None
    try:
        broadcast_id = int(callback.data.split(":", 1)[1])
        discount_row = await database.get_broadcast_discount(broadcast_id)
        gr_pct = (discount_row or {}).get("gift_reveal_percent")
        if gr_pct:
            percent = int(gr_pct)
            logger.info(
                "GIFT_REVEAL_CLICK broadcast_id=%s user=%s pct=%s (from DB)",
                broadcast_id, telegram_id, percent,
            )
        else:
            logger.warning(
                "GIFT_REVEAL_CLICK broadcast_id=%s user=%s pct=%s (FALLBACK — "
                "discount_row=%s, gift_reveal_percent=%s). Возможно: миграция 063 "
                "ещё не накатана / save упал при create /рассылка создана до фичи.",
                broadcast_id, telegram_id, percent,
                discount_row is not None, gr_pct,
            )
    except Exception as e:
        logger.warning(
            "GIFT_REVEAL_LOOKUP_FAIL broadcast_id=%s callback=%s err=%s — using default %s%%",
            broadcast_id, callback.data, e, _GIFT_REVEAL_PERCENT_DEFAULT,
        )

    try:
        # 1) эмодзи 👀 — интрига. Сохраняем message_id, чтобы удалить
        # его одновременно с появлением reveal-сообщения через 2 сек.
        eyes_msg = await callback.bot.send_message(
            chat_id,
            _GIFT_REVEAL_EMOJI,
            parse_mode="HTML",
        )

        # 2) держим паузу 2 секунды для эффекта
        await asyncio.sleep(2.0)

        # 3) удаляем «👀» (исчезает) и тут же шлём reveal — визуально
        # одно сменяется другим. Если delete упал (юзер сам удалил
        # сообщение или Telegram отказал) — это не критично, продолжаем.
        try:
            await callback.bot.delete_message(chat_id, eyes_msg.message_id)
        except Exception:
            pass

        # 4) применяем скидку %/48ч — ДО обещания. Порядок был обратный:
        # человек читал «для тебя подарок N%», а create_user_discount
        # возвращала False (глотает исключение внутри, отдаёт False при
        # неготовой базе) — и подарка не существовало ни в базе, ни в логе.
        expires_at = datetime.now(timezone.utc) + timedelta(hours=_GIFT_REVEAL_HOURS)
        created = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )

        # 5) reveal-сообщение с динамическим процентом — только если подарок
        # действительно лёг в базу.
        if created:
            logger.info(
                "GIFT_REVEAL_DISCOUNT_APPLIED broadcast_id=%s user=%s pct=%s hours=%s",
                broadcast_id, telegram_id, percent, _GIFT_REVEAL_HOURS,
            )
            await callback.bot.send_message(
                chat_id,
                f"<b>Для тебя подарок {percent}% скидка на любую подписку!</b> {_GIFT_REVEAL_PRESENT}",
                parse_mode="HTML",
            )
        else:
            logger.error(
                "GIFT_REVEAL_DISCOUNT_NOT_CREATED broadcast_id=%s user=%s pct=%s — "
                "скидки в базе нет, экран тарифов покажет полную цену",
                broadcast_id, telegram_id, percent,
            )
            await callback.bot.send_message(
                chat_id,
                "Подарок не удалось применить, попробуйте позже.",
                parse_mode="HTML",
            )

        # 6) короткая пауза перед экраном тарифов — отделить визуально
        await asyncio.sleep(0.03)

        # 7) показываем экран выбора тарифов — get_user_discount внутри
        # автоматически подставит -20% на basic / plus / combo_basic /
        # combo_plus. Маркер `from_broadcast=True` нужен, чтобы кнопка
        # «Назад» с экрана выбора периода возвращала на этот же экран
        # выбора тарифов (а не на «Управление подпиской», куда
        # `menu_buy_vpn` уводит юзеров с активной подпиской).
        await state.update_data(from_broadcast=True)
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state)

    except Exception as e:
        logger.exception(f"Error in broadcast_gift_reveal: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@router.callback_query(F.data == "broadcast_back_to_tariffs")
async def callback_broadcast_back_to_tariffs(callback: CallbackQuery, state: FSMContext):
    """«Назад» с экрана выбора периода → обратно на экран выбора тарифа.

    Используется только в broadcast-flow (gift_reveal и подобных), где
    юзер ходит между «выбрать тариф → посмотреть период → назад». В
    обычном flow «Назад» по-прежнему ведёт на menu_buy_vpn («Управление
    подпиской»), это поведение не меняется.

    Маркер `from_broadcast=True` НЕ снимаем — юзер ещё внутри flow и
    может зайти в другой тариф. Снимется естественно при выходе из
    state (main menu, cabinet и т.п.).
    """
    try:
        await callback.answer()
    except Exception:
        pass
    from app.handlers.common.screens import show_tariffs_main_screen
    await show_tariffs_main_screen(callback, state)
