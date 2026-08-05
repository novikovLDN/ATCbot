"""Предложение из рассылки: −30% на 1 месяц.

ЧТО ЗДЕСЬ
    Экран выбора тарифа со сразу посчитанной ценой и переход к выбору
    способа оплаты.

ПОЧЕМУ ВЫДЕЛЕНО
    Каждое предложение — своя формула цены, свой набор callback'ов и свой
    срок жизни. Раньше четыре предложения лежали одним файлом, и правка
    процента в одном требовала не задеть три соседних.

ЧТО ЛЕГКО СЛОМАТЬ
    У предложения свой префикс callback'ов (bcg1m:*) именно затем, чтобы
    FSM-подмена цены не пересеклась с трёхмесячным сценарием. Совпадут
    префиксы — человек нажмёт «1 месяц», а купит три.

    Скидка живёт ТОЛЬКО как final_price_kopecks в FSM, записи в
    user_discounts не создаётся. Поэтому закрытый экран = сгоревшее
    предложение, и оно не протечёт на другие периоды.

    Комбо-тарифы кладут в FSM базовый тариф плюс combo_bypass_gb: пакет
    ГБ обхода едет отдельным полем. Потеряете поле — человек заплатит за
    комбо, а получит обычную подписку.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config

router = Router()
logger = logging.getLogger(__name__)


# gift_1m — та же механика, что gift_3m, но период 30 дней и −30%.
# Отдельный набор callback'ов (bcg1m:*), чтобы FSM-override не мешал
# 3-месячному сценарию.
_GIFT1M_DISCOUNT_PERCENT = 30
_GIFT1M_PERIOD_DAYS = 30


def _gift1m_base_price_rubles(tariff: str) -> int | None:
    if tariff in ("basic", "plus"):
        return config.TARIFFS.get(tariff, {}).get(_GIFT1M_PERIOD_DAYS, {}).get("price")
    if tariff in ("combo_basic", "combo_plus"):
        return config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT1M_PERIOD_DAYS, {}).get("price")
    return None


def _gift1m_price_rubles(tariff: str) -> int | None:
    base = _gift1m_base_price_rubles(tariff)
    if not base:
        return None
    return round(base * (100 - _GIFT1M_DISCOUNT_PERCENT) / 100)


def _gift1m_menu_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🎁 <b>Подарок: −{_GIFT1M_DISCOUNT_PERCENT}% на 1 месяц</b>",
        "",
    ]
    rows = []
    for tariff, label in (
        ("basic", "🌟 Basic"),
        ("plus", "⚡ Plus"),
        ("combo_basic", "🚀 Combo Basic"),
        ("combo_plus", "🚀 Combo Plus"),
    ):
        base = _gift1m_base_price_rubles(tariff)
        disc = _gift1m_price_rubles(tariff)
        if base is None or disc is None:
            continue
        lines.append(f"{label} 1м — было {base} ₽, стало <b>{disc} ₽</b>")
        rows.append([InlineKeyboardButton(
            text=f"🎁 {label} 1м · {disc} ₽",
            callback_data=f"bcg1m:buy:{tariff}",
        )])
    lines.append("")
    lines.append("⏰ Скидка действует здесь и сейчас.")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "broadcast_gift_1m")
async def callback_broadcast_gift_1m(callback: CallbackQuery, state: FSMContext):
    """User clicked «🎁 −30% на 1 месяц» → экран выбора тарифа."""
    try:
        await callback.answer()
    except Exception:
        pass

    text, keyboard = _gift1m_menu_text_and_keyboard()
    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
    try:
        await callback.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("BROADCAST_GIFT1M_RENDER_FAIL user=%s err=%s", callback.from_user.id, e)
    logger.info("BROADCAST_GIFT1M_SHOWN user=%s", callback.from_user.id)


@router.callback_query(F.data.startswith("bcg1m:buy:"))
async def callback_broadcast_gift_1m_buy(callback: CallbackQuery, state: FSMContext):
    """Выбран тариф → сразу к выбору способа оплаты с overriden ценой."""
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

    price_rubles = _gift1m_price_rubles(tariff)
    if price_rubles is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    price_kopecks = price_rubles * 100
    if tariff in ("combo_basic", "combo_plus"):
        combo_info = config.COMBO_TARIFFS.get(tariff, {}).get(_GIFT1M_PERIOD_DAYS, {})
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
        period_days=_GIFT1M_PERIOD_DAYS,
        final_price_kopecks=price_kopecks,
        discount_percent=_GIFT1M_DISCOUNT_PERCENT,
        combo_bypass_gb=gb,
    )
    await state.set_state(PurchaseState.choose_payment_method)

    logger.info(
        "BROADCAST_GIFT1M_BUY user=%s tariff=%s base=%s combo_gb=%s price_kopecks=%s",
        telegram_id, tariff, base_tariff, gb, price_kopecks,
    )

    from app.handlers.payments.method_select import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, _GIFT1M_PERIOD_DAYS, price_kopecks)
