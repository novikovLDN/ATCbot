"""Витрины выбора пакета гигабайтов — и обычного трафика, и обхода.

ЧТО ЗДЕСЬ
    Шесть экранов: список пакетов, список расширенных пакетов и экран
    подтверждения — отдельно для трафика (buy_traffic*) и отдельно для
    обхода белых списков (buy_bypass*). Дальше пользователь уходит в
    pay_traffic.py или pay_bypass.py.

ПОЧЕМУ ОТДЕЛЬНО ОТ ОПЛАТЫ
    Здесь только считается цена со скидкой и рисуется клавиатура: ни один
    экран не создаёт покупку и не трогает деньги. Правка вида витрины не
    должна ехать через модуль, который выставляет счета.

ЧТО ЛЕГКО СЛОМАТЬ
    Две линейки продуктов различаются ТОЛЬКО callback_data и кнопкой
    «назад»: buy_traffic_pack:N против buy_bypass_pack:N, а пакеты и цены
    берутся из одних и тех же config.TRAFFIC_PACKS. Свести экраны в один
    общий значит смешать линейки — пользователь купит обход вместо трафика.

    Набор способов оплаты на двух экранах подтверждения разный и это
    осознанно: у обхода есть Stars и CryptoBot, у трафика их нет. Кнопка,
    добавленная здесь без обработчика в соответствующем pay_*.py, будет
    молчать без единой ошибки в логах.
"""
import math

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from ._shared import _strikethrough

packs_router = Router()


@packs_router.callback_query(F.data == "buy_bypass_only")
async def callback_buy_bypass_only(callback: CallbackQuery):
    """Экран покупки только обхода белых списков (ГБ пакеты)."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    # Build pack buttons (2 per row)
    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_bypass_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text="📦 Больше объёма →",
        callback_data="buy_bypass_extended",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])

    text = i18n_get_text(language, "bypass.buy_title")
    # Add trial bonus text if trial is available
    from app.services.trials import service as trial_service
    trial_available = await trial_service.is_trial_available(telegram_id)
    if trial_available:
        text += i18n_get_text(language, "bypass.buy_title_trial")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    # Main screen may be a photo — delete and send new message
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")


@packs_router.callback_query(F.data == "buy_bypass_extended")
async def callback_buy_bypass_extended(callback: CallbackQuery):
    """Расширенные пакеты обхода (300+ ГБ)."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_bypass_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_bypass_only",
    )])

    text = i18n_get_text(language, "traffic.buy_title_extended")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@packs_router.callback_query(F.data.startswith("buy_bypass_pack:"))
async def callback_buy_bypass_pack(callback: CallbackQuery):
    """Подтверждение покупки bypass-only пакета."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    balance = await database.get_user_balance(telegram_id)
    base_price = pack["price"]
    if discount_pct > 0:
        final_price = math.ceil(base_price * (1 - discount_pct / 100))
    else:
        final_price = base_price

    text = i18n_get_text(language, "traffic.confirm_purchase", gb=gb, price=final_price, balance=balance)

    buttons = []

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.card"),
        callback_data=f"bypass_pay_card:{gb}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.sbp"),
        callback_data=f"bypass_pay_sbp:{gb}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.stars"),
        callback_data=f"bypass_pay_stars:{gb}",
    )])

    import cryptobot_service
    if cryptobot_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.crypto"),
            callback_data=f"bypass_pay_crypto:{gb}",
        )])

    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava"),
            callback_data=f"bypass_pay_lava:{gb}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_bypass_only",
    )])

    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@packs_router.callback_query(F.data == "buy_traffic")
async def callback_buy_traffic(callback: CallbackQuery):
    """Show traffic pack options."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "traffic.no_subscription")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    # Build pack buttons (2 per row)
    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_traffic_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text="📦 Больше объёма →",
        callback_data="buy_traffic_extended",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="traffic_info",
    )])

    text = i18n_get_text(language, "traffic.buy_title")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@packs_router.callback_query(F.data == "buy_traffic_extended")
async def callback_buy_traffic_extended(callback: CallbackQuery):
    """Show extended traffic packs (300+GB)."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_traffic_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_traffic",
    )])

    text = i18n_get_text(language, "traffic.buy_title_extended")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@packs_router.callback_query(F.data.startswith("buy_traffic_pack:"))
async def callback_buy_traffic_pack(callback: CallbackQuery):
    """Confirm traffic pack purchase."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    balance = await database.get_user_balance(telegram_id)
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price

    # SBP price with markup
    sbp_price_kopecks = math.ceil(price * 100 * (1 + config.SBP_MARKUP_PERCENT / 100.0))
    sbp_price = sbp_price_kopecks / 100.0

    text = i18n_get_text(
        language,
        "traffic.confirm_purchase",
        gb=gb,
        price=price,
        balance=f"{balance:.0f}",
    )
    if discount_pct > 0:
        text += f"\n🎁 Скидка {discount_pct}%: {_strikethrough(str(base_price))} ₽ → {price} ₽"

    buttons: list = []

    # Card (YooKassa) button — requires TG_PROVIDER_TOKEN
    if config.TG_PROVIDER_TOKEN:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_card", price=price),
            callback_data=f"traffic_pay_card:{gb}",
        )])

    # SBP (Platega) button
    import platega_service
    if platega_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_sbp", price=f"{sbp_price:.0f}"),
            callback_data=f"traffic_pay_sbp:{gb}",
        )])

    # Lava (card) button
    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_lava", price=price),
            callback_data=f"traffic_pay_lava:{gb}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_traffic",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
