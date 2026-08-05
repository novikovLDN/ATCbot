"""Предложение из рассылки: −30% на 3 месяца.

ЧТО ЗДЕСЬ
    Экран выбора тарифа, экран «О тарифах» и переход к оплате. Цены
    считаются здесь же из config.TARIFFS / COMBO_TARIFFS.

ПОЧЕМУ ВЫДЕЛЕНО
    Отдельное предложение со своим процентом, периодом и префиксом
    callback'ов (bcg3m:*). Правится независимо от соседей.

ЧТО ЛЕГКО СЛОМАТЬ
    Скидка реализована как подмена final_price_kopecks в FSM, а не
    записью в user_discounts. Это осознанно: предложение не может
    протечь на другие периоды и не может «застрять» в базе просроченной
    строкой. Переделаете на user_discounts — получите оба этих эффекта.

    Экран меню и экран «О тарифах» показывают цены, посчитанные одними и
    теми же функциями, что и кнопка покупки. Разъедутся — человек увидит
    одну цену, а заплатит другую.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
from app.handlers.common.utils import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)


_GIFT3M_DISCOUNT_PERCENT = 30
_GIFT3M_PERIOD_DAYS = 90


def _gift3m_price_rubles(tariff: str) -> int | None:
    """Discounted 3-month price in rubles for the four eligible tariffs."""
    if tariff in ("basic", "plus"):
        base = config.TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    elif tariff in ("combo_basic", "combo_plus"):
        base = config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    else:
        return None
    if not base:
        return None
    return round(base * (100 - _GIFT3M_DISCOUNT_PERCENT) / 100)


def _gift3m_base_price_rubles(tariff: str) -> int | None:
    if tariff in ("basic", "plus"):
        return config.TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    if tariff in ("combo_basic", "combo_plus"):
        return config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {}).get("price")
    return None


def _gift3m_menu_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🎁 <b>Подарок: −{_GIFT3M_DISCOUNT_PERCENT}% на 3 месяца</b>",
        "",
    ]
    rows = []
    for tariff, label in (
        ("basic", "🌟 Basic"),
        ("plus", "⚡ Plus"),
        ("combo_basic", "🚀 Combo Basic"),
        ("combo_plus", "🚀 Combo Plus"),
    ):
        base = _gift3m_base_price_rubles(tariff)
        disc = _gift3m_price_rubles(tariff)
        if base is None or disc is None:
            continue
        lines.append(f"{label} 3м — было {base} ₽, стало <b>{disc} ₽</b>")
        rows.append([InlineKeyboardButton(
            text=f"🎁 {label} 3м · {disc} ₽",
            callback_data=f"bcg3m:buy:{tariff}",
        )])

    lines.append("")
    lines.append("⏰ Скидка действует здесь и сейчас.")
    rows.append([InlineKeyboardButton(text="ℹ️ О тарифах", callback_data="bcg3m:info")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _gift3m_info_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    combo_basic_gb = config.COMBO_TARIFFS.get("combo_basic", {}).get(_GIFT3M_PERIOD_DAYS, {}).get("gb", 0)
    combo_plus_gb = config.COMBO_TARIFFS.get("combo_plus", {}).get(_GIFT3M_PERIOD_DAYS, {}).get("gb", 0)

    basic_disc = _gift3m_price_rubles("basic")
    plus_disc = _gift3m_price_rubles("plus")
    cbasic_disc = _gift3m_price_rubles("combo_basic")
    cplus_disc = _gift3m_price_rubles("combo_plus")

    text = (
        "📦 <b>О тарифах · 3 месяца</b>\n\n"

        f"🌟 <b>Basic — {basic_disc} ₽</b>\n"
        "<blockquote>🚀 Канал до 25 Гбит/с — YouTube 4K без тормозов\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "➕ Подключение в одно нажатие</blockquote>\n\n"

        f"⚡ <b>Plus — {plus_disc} ₽</b>\n"
        "<blockquote>⚡️ Канал до 75 Гбит/с — стримы и игры без лагов\n"
        "🔄 Резервные каналы — соединение работает всегда\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно</blockquote>\n\n"

        f"🚀 <b>Combo Basic — {cbasic_disc} ₽</b>\n"
        "<blockquote>🌐 Безлимит на основных серверах · до 25 Гбит/с\n"
        f"📊 <b>{combo_basic_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>\n\n"

        f"🚀 <b>Combo Plus — {cplus_disc} ₽</b>\n"
        "<blockquote>🌐 Безлимит на приоритетных серверах · до 75 Гбит/с\n"
        "🔄 Резервные каналы — всегда онлайн\n"
        f"📊 <b>{combo_plus_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад к скидке", callback_data="bcg3m:menu")],
    ])
    return text, keyboard


@router.callback_query(F.data == "broadcast_gift_3m")
async def callback_broadcast_gift_3m(callback: CallbackQuery, state: FSMContext):
    """User clicked the "🎁 Скидка 30% на 3 месяца" CTA in a broadcast.

    Shows a dedicated screen with 4 pre-discounted 3-month buttons
    (Basic, Plus, Combo Basic, Combo Plus). The discount is realised
    purely as a final_price_kopecks override carried in FSM into the
    standard payment-method screen — no personal_discount row is
    created, so the offer cannot leak to other periods or expire as
    stale DB state.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift3m_menu_text_and_keyboard()

    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        await callback.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("BROADCAST_GIFT3M_RENDER_FAIL user=%s err=%s", callback.from_user.id, e)

    logger.info("BROADCAST_GIFT3M_SHOWN user=%s", callback.from_user.id)


@router.callback_query(F.data == "bcg3m:menu")
async def callback_broadcast_gift_3m_menu(callback: CallbackQuery, state: FSMContext):
    """Re-render the gift menu (used as 'back' from the info screen)."""
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift3m_menu_text_and_keyboard()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT3M_MENU_FAIL user=%s err=%s", callback.from_user.id, e)


@router.callback_query(F.data == "bcg3m:info")
async def callback_broadcast_gift_3m_info(callback: CallbackQuery, state: FSMContext):
    """Show full descriptions of all four 3-month gift tariffs."""
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift3m_info_text_and_keyboard()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT3M_INFO_FAIL user=%s err=%s", callback.from_user.id, e)


@router.callback_query(F.data.startswith("bcg3m:buy:"))
async def callback_broadcast_gift_3m_buy(callback: CallbackQuery, state: FSMContext):
    """User picked one of the four 3-month gift tariffs — jump straight to payment-method selection."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        tariff = callback.data.split(":", 2)[2]
    except IndexError:
        await callback.answer("Ошибка", show_alert=True)
        return

    price_rubles = _gift3m_price_rubles(tariff)
    if price_rubles is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    price_kopecks = price_rubles * 100
    if tariff in ("combo_basic", "combo_plus"):
        combo_info = config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT3M_PERIOD_DAYS, {})
        base_tariff = combo_info.get("base_tariff")
        gb = combo_info.get("gb", 0)
    else:
        base_tariff = tariff
        gb = 0

    if base_tariff not in config.TARIFFS:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    from app.handlers.common.states import PurchaseState
    await state.update_data(
        tariff_type=base_tariff,
        period_days=_GIFT3M_PERIOD_DAYS,
        final_price_kopecks=price_kopecks,
        discount_percent=_GIFT3M_DISCOUNT_PERCENT,
        combo_bypass_gb=gb,
    )
    await state.set_state(PurchaseState.choose_payment_method)

    logger.info(
        "BROADCAST_GIFT3M_BUY user=%s tariff=%s base=%s combo_gb=%s price_kopecks=%s",
        telegram_id, tariff, base_tariff, gb, price_kopecks,
    )

    from app.handlers.payments.method_select import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, _GIFT3M_PERIOD_DAYS, price_kopecks)
