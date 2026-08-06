"""Предложения из рассылки, которые пишут скидку в базу.

ЧТО ЗДЕСЬ
    Два экрана: «Купить со скидкой» (скидка на подписку) и «Купить ГБ со
    скидкой» (скидка на трафик). Оба берут процент и срок из настроек
    самой рассылки и создают персональную скидку пользователю.

ПОЧЕМУ ВЫДЕЛЕНО ОТДЕЛЬНО ОТ ПОДАРКОВ
    Это единственные предложения, которые оставляют СЛЕД В БАЗЕ. Подарки
    (gift_1m / gift_3m / gift_1y40) живут одноразовой подменой цены в FSM
    и сгорают вместе с экраном, а здесь скидка переживает и экран, и
    перезапуск бота.

ЧТО ЛЕГКО СЛОМАТЬ
    Срок скидки берётся из рассылки (discount_hours), у трафика он
    зафиксирован сутками. Перепутать — раздать месячную скидку вместо
    суточной, и отменить это можно будет только руками в базе.

    Экран тарифов открывается с force_new_message=True: сообщение
    рассылки должно остаться в чате, иначе человек не поймёт, на какую
    акцию он кликнул.
"""
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("broadcast_promo_buy:"))
async def callback_broadcast_promo_buy(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Купить со скидкой' в уведомлении — автоматически применяем скидку"""
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        # Get discount from DB
        discount = await database.get_broadcast_discount(broadcast_id)
        if not discount:
            # No discount found, just redirect to tariff selection.
            # force_new_message=True — сохраняем оригинал рассылки в чате,
            # экран тарифов уходит свежим сообщением сверху.
            from app.handlers.common.screens import show_tariffs_main_screen
            await show_tariffs_main_screen(callback, state, force_new_message=True)
            return

        discount_percent = discount.get("discount_percent", 0)
        discount_hours = discount.get("discount_hours", 168)  # default 7 days
        discount_label = discount.get("discount_label", "7 дней")

        # Auto-apply discount to user with configured duration
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=discount_hours)
        # create_user_discount возвращает bool и глотает исключение внутри
        # (database/discounts.py), а при неготовой базе молча отдаёт False.
        # Результат не читался вовсе: человеку уходило «скидка применена»
        # при нулевом результате, и в логе не оставалось ни строки.
        created = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )

        # Redirect to tariff screen. force_new_message=True — рассылка
        # остаётся (юзер видит, на какой именно акции кликнул).
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state, force_new_message=True)

        if not created:
            logger.error(
                "BROADCAST_DISCOUNT_NOT_CREATED user=%s broadcast_id=%s pct=%s hours=%s — "
                "скидки в базе нет, экран тарифов покажет полную цену",
                telegram_id, broadcast_id, discount_percent, discount_hours,
            )
            await callback.answer(
                "Скидку применить не удалось, попробуйте позже",
                show_alert=True,
            )
            return

        logger.info(
            "BROADCAST_DISCOUNT_APPLIED user=%s broadcast_id=%s pct=%s hours=%s",
            telegram_id, broadcast_id, discount_percent, discount_hours,
        )

        # ДОЛГ: подтверждение захардкожено по-русски, а экран уходит
        # живому человеку — казахо- и таджикоязычные читают его чужим
        # языком. Ключа i18n под этот текст нет, и завести его мало:
        # discount_label — свободная строка, которую админ вбил в
        # дашборде по-русски («7 дней»), так что переводить придётся
        # вместе с ней. Это работа владельца по переписыванию текстов
        # экранов. До неё resolve_user_language здесь только лишний
        # запрос в БД на каждый клик по рассылке — язык всё равно
        # некуда подставить.
        await callback.message.answer(
            f"🎁 Скидка {discount_percent}% автоматически применена! Действует {discount_label}.",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@router.callback_query(F.data.startswith("broadcast_promo_traffic:"))
async def callback_broadcast_promo_traffic(callback: CallbackQuery):
    """User clicked 'Купить трафик промо' in broadcast — apply 1-day traffic discount."""
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        discount = await database.get_broadcast_discount(broadcast_id)
        discount_percent = discount.get("discount_percent", 0) if discount else 0

        if discount_percent > 0:
            # Apply 1-day traffic discount
            from datetime import timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(days=1)
            # create_user_traffic_discount отдаёт False при неготовой базе и
            # при отсутствующем пуле — молча, без записи. Результат не
            # читался: ниже рисовались зачёркнутые цены и текст «скидка
            # применена», а оплата шла по полному прайсу. Обнуляем процент,
            # чтобы экран показывал ту цену, которую человек и заплатит.
            created = await database.create_user_traffic_discount(
                telegram_id=telegram_id,
                discount_percent=discount_percent,
                expires_at=expires_at,
                created_by=config.ADMIN_TELEGRAM_ID,
            )
            if not created:
                logger.error(
                    "BROADCAST_TRAFFIC_DISCOUNT_NOT_CREATED user=%s broadcast_id=%s pct=%s — "
                    "скидки в базе нет, показываем полные цены",
                    telegram_id, broadcast_id, discount_percent,
                )
                discount_percent = 0
            else:
                logger.info(
                    "BROADCAST_TRAFFIC_DISCOUNT_APPLIED user=%s broadcast_id=%s pct=%s hours=24",
                    telegram_id, broadcast_id, discount_percent,
                )

        # Build traffic packs message with discount applied
        language = await resolve_user_language(telegram_id)

        subscription = await database.get_subscription(telegram_id)
        if not subscription:
            await callback.message.answer(
                i18n_get_text(language, "traffic.no_subscription"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "traffic.buy_subscription"),
                        callback_data="menu_buy_vpn",
                    )],
                ]),
                parse_mode="HTML",
            )
            return

        import math

        def _strikethrough(text: str) -> str:
            return "".join(ch + "\u0336" for ch in str(text))

        buttons = []
        for gb, pack in config.TRAFFIC_PACKS.items():
            base_price = pack["price"]
            if discount_percent > 0:
                final_price = math.ceil(base_price * (1 - discount_percent / 100))
                label = f"{gb} ГБ — {final_price} ₽  {_strikethrough(str(base_price))} ₽  (−{discount_percent}%)"
            else:
                label = f"{gb} ГБ — {base_price} ₽"
                if pack.get("discount"):
                    label += f"  {pack['discount']}"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"buy_traffic_pack:{gb}",
            )])

        # Расширенные пакеты (300+ ГБ) — это существующий экран
        # traffic.packs.callback_buy_traffic_extended. Здесь стояло
        # broadcast_promo_traffic_ext:{id}, под которое обработчика нет
        # ни одного: человек жал «Больше объёма» и не получал ничего —
        # ни экрана, ни ошибки, ни строчки в логе.
        #
        # broadcast_id в адресе не нужен: скидка на трафик уже записана
        # в базу выше (create_user_traffic_discount), а buy_traffic_extended
        # читает её сам через get_user_traffic_discount. Передавать сюда
        # id рассылки значило бы завести второй источник правды о скидке.
        buttons.append([InlineKeyboardButton(
            text="📦 Больше объёма →",
            callback_data="buy_traffic_extended",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="traffic_info",
        )])

        text = i18n_get_text(language, "traffic.buy_title")
        if discount_percent > 0:
            text = f"🎁 Скидка {discount_percent}% на трафик применена! Действует 24 часа.\n\n" + text

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast traffic promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)
