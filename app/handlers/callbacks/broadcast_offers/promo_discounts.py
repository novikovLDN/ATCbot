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


# ── Preset: maintenance broadcast with bypass key + 20% traffic discount ──
# Lives here as a literal so the admin can fire it from the dashboard
# without retyping. {bypass_key} is substituted per recipient in _send_one.
_PRESET_MAINTENANCE_TITLE = (
    "![🛠](tg://emoji?id=5462921117423384478) "
    "<b>Тех. работы на основных серверах</b>"
)
_PRESET_MAINTENANCE_TEXT = (
    "До <b>25.05</b> просим временно использовать наши <b>серверы обхода "
    "белых списков</b> — они работают стабильно.\n\n"
    "![🎁](tg://emoji?id=5384578448633129482) <b>Скидка 20% на ГБ обхода</b> "
    "— забрать по кнопке <b>«Купить трафик»</b> ниже.\n\n"
    "━━━━━━━━━━━━━━\n"
    "![🔑](tg://emoji?id=5465443379917629504) <b>Ваш ключ обхода</b>\n\n"
    "<code>{bypass_key}</code>\n\n"
    "<i>Нажмите, чтобы скопировать.</i>\n"
    "━━━━━━━━━━━━━━\n\n"
    "📲 <b>Подключение через Happ</b>\n"
    "<blockquote>"
    "![1️⃣](tg://emoji?id=5382322671679708881) Скопируйте ключ выше одним "
    "нажатием по нему\n"
    "![2️⃣](tg://emoji?id=5381990043642502553) Откройте приложение\n"
    "![3️⃣](tg://emoji?id=5381879959335738545) Справа сверху нажмите "
    "<b>«+»</b> → <b>«Вставить из буфера»</b>\n"
    "![4️⃣](tg://emoji?id=5382054253403577563) Выберите сервера с пометкой "
    "<b>LTE</b> и включите соединение"
    "</blockquote>\n\n"
    "По окончании работ всё вернётся автоматически — переключать обратно "
    "не нужно. Спасибо за понимание "
    "![🧩](tg://emoji?id=5265120027853481187)"
)


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
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )

        # Redirect to tariff screen. force_new_message=True — рассылка
        # остаётся (юзер видит, на какой именно акции кликнул).
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state, force_new_message=True)

        language = await resolve_user_language(telegram_id)
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
            await database.create_user_traffic_discount(
                telegram_id=telegram_id,
                discount_percent=discount_percent,
                expires_at=expires_at,
                created_by=config.ADMIN_TELEGRAM_ID,
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

        buttons.append([InlineKeyboardButton(
            text="📦 Больше объёма →",
            callback_data=f"broadcast_promo_traffic_ext:{broadcast_id}",
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
