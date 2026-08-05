"""Предложение из рассылки: 1 год со скидкой 40%.

ЧТО ЗДЕСЬ
    Двухшаговый выбор: сначала тариф, потом период (30/90/180/365).
    Скидка действует ТОЛЬКО на 365 дней, остальные периоды идут по
    обычному прайсу — так задумано, экран периодов показывает и то и
    другое.

ПОЧЕМУ ВЫДЕЛЕНО
    Единственное предложение с двумя шагами и собственной reveal-сценкой
    перед экраном. Своя пачка callback'ов (bcg1y40:*), свои тексты.

ЧТО ЛЕГКО СЛОМАТЬ
    `discount_percent` пишется в FSM только для 365 дней. Написать его
    всегда — и чекаут покажет скидку там, где цена обычная.

    Скидка — одноразовый FSM-override, никаких записей в user_discounts:
    закрыл экран, не купив, — предложение сгорело. Это и есть защита от
    «скидка утекла на всё подряд».

    Reveal-сценка (эмодзи → пауза → удаление) не обязана удаться: любое
    падение Telegram здесь ловится и не мешает показать экран. Убрав
    try/except, получите молчащую кнопку из-за косметики.
"""
import asyncio
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
from app.handlers.common.utils import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
#  Gift 1 год −40% — скидка ТОЛЬКО на 365-дневный план
#
#  UX: рассылка → «🎁 1 год со скидкой 40%» → экран выбора тарифа
#  (Basic / Plus / Combo Basic / Combo Plus) → экран выбора периода
#  (30/90/180/365, где ТОЛЬКО 365 идёт со скидкой) → payment-method.
#
#  Скидка реализована как final_price_kopecks-override в FSM (одноразово,
#  как gift_3m). Никаких записей в user_discounts — если юзер закрыл
#  экран не купив, скидка «сгорает».
# ──────────────────────────────────────────────────────────────────────

_GIFT1Y40_DISCOUNT_PERCENT = 40
_GIFT1Y40_PERIOD_DAYS_DISCOUNTED = 365
_GIFT1Y40_PERIODS = (30, 90, 180, 365)
# Reveal-эмодзи (трофей) как у «Посмотреть подарок» — интригующая пауза
# перед экраном выбора тарифа. Кастомный emoji id принадлежит нашему
# premium-паку; клиенты без Telegram Premium увидят обычный 🏆.
_GIFT1Y40_REVEAL_EMOJI = '<tg-emoji emoji-id="5413566144986503832">🏆</tg-emoji>'
_GIFT1Y40_REVEAL_PAUSE_SECONDS = 2.0
_GIFT1Y40_PERIOD_LABELS = {
    30: "1 месяц",
    90: "3 месяца",
    180: "6 месяцев",
    365: "1 год",
}
_GIFT1Y40_TARIFFS = (
    ("basic", "🌟 Basic"),
    ("plus", "⚡ Plus"),
    ("combo_basic", "🚀 Combo Basic"),
    ("combo_plus", "🚀 Combo Plus"),
)


def _gift1y40_base_price(tariff: str, period_days: int) -> int | None:
    if tariff in ("basic", "plus"):
        return config.TARIFFS.get(tariff, {}).get(period_days, {}).get("price")
    if tariff in ("combo_basic", "combo_plus"):
        return config.COMBO_TARIFFS.get(tariff, {}).get(period_days, {}).get("price")
    return None


def _gift1y40_final_price(tariff: str, period_days: int) -> int | None:
    """Финальная цена с учётом акции: 40% скидка ТОЛЬКО на 365 дней,
    остальные периоды по обычному прайсу."""
    base = _gift1y40_base_price(tariff, period_days)
    if base is None:
        return None
    if period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED:
        return round(base * (100 - _GIFT1Y40_DISCOUNT_PERCENT) / 100)
    return base


def _gift1y40_tariff_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Первый экран: выбор тарифа."""
    lines = [
        f"🎁 <b>Скидка {_GIFT1Y40_DISCOUNT_PERCENT}% на 1 год</b>",
        "",
        "Годовой план — сразу с учётом скидки.",
        "Другие периоды доступны по обычной цене.",
        "",
        "<b>Выбери тариф ↓</b>",
    ]
    rows = []
    for tariff, label in _GIFT1Y40_TARIFFS:
        # Проверяем что тариф вообще существует в конфиге (защита от
        # рассинхрона config vs UI).
        if _gift1y40_base_price(tariff, _GIFT1Y40_PERIOD_DAYS_DISCOUNTED) is None:
            continue
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"bcg1y40:tariff:{tariff}",
        )])
    rows.append([InlineKeyboardButton(
        text="ℹ️ О тарифах",
        callback_data="bcg1y40:info",
    )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _gift1y40_period_menu(tariff: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """Второй экран: выбор периода для конкретного тарифа."""
    tariff_label = next(
        (label for t, label in _GIFT1Y40_TARIFFS if t == tariff),
        tariff.capitalize(),
    )
    lines = [
        f"{tariff_label}",
        "",
        "Выбери срок ↓",
        "",
    ]
    rows = []
    have_any = False
    for period_days in _GIFT1Y40_PERIODS:
        base = _gift1y40_base_price(tariff, period_days)
        final = _gift1y40_final_price(tariff, period_days)
        if base is None or final is None:
            continue
        have_any = True
        period_label = _GIFT1Y40_PERIOD_LABELS[period_days]
        if period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED:
            # 365 → с плашкой и зачёркнутой ценой
            lines.append(
                f"🎁 <b>{period_label}</b> — было <s>{base} ₽</s>, "
                f"стало <b>{final} ₽</b>  <i>−{_GIFT1Y40_DISCOUNT_PERCENT}%</i>"
            )
            btn_text = f"🎁 {period_label} · {final} ₽"
        else:
            lines.append(f"• {period_label} — {base} ₽")
            btn_text = f"{period_label} · {base} ₽"
        rows.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"bcg1y40:buy:{tariff}:{period_days}",
        )])
    if not have_any:
        return None
    rows.append([InlineKeyboardButton(
        text="← Назад к тарифам",
        callback_data="bcg1y40:menu",
    )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _gift1y40_info_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    combo_basic_gb = config.COMBO_TARIFFS.get("combo_basic", {}).get(
        _GIFT1Y40_PERIOD_DAYS_DISCOUNTED, {}).get("gb", 0)
    combo_plus_gb = config.COMBO_TARIFFS.get("combo_plus", {}).get(
        _GIFT1Y40_PERIOD_DAYS_DISCOUNTED, {}).get("gb", 0)

    basic_final = _gift1y40_final_price("basic", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)
    plus_final = _gift1y40_final_price("plus", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)
    cbasic_final = _gift1y40_final_price("combo_basic", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)
    cplus_final = _gift1y40_final_price("combo_plus", _GIFT1Y40_PERIOD_DAYS_DISCOUNTED)

    text = (
        "📦 <b>О тарифах · 1 год со скидкой 40%</b>\n\n"

        f"🌟 <b>Basic — {basic_final} ₽</b>\n"
        "<blockquote>🚀 Канал до 25 Гбит/с — YouTube 4K без тормозов\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "➕ Подключение в одно нажатие</blockquote>\n\n"

        f"⚡ <b>Plus — {plus_final} ₽</b>\n"
        "<blockquote>⚡️ Канал до 75 Гбит/с — стримы и игры без лагов\n"
        "🔄 Резервные каналы — соединение работает всегда\n"
        "🌐 10 ГБ обхода белых списков в подарок\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно</blockquote>\n\n"

        f"🚀 <b>Combo Basic — {cbasic_final} ₽</b>\n"
        "<blockquote>🌐 Безлимит на основных серверах · до 25 Гбит/с\n"
        f"📊 <b>{combo_basic_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 10 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>\n\n"

        f"🚀 <b>Combo Plus — {cplus_final} ₽</b>\n"
        "<blockquote>🌐 Безлимит на приоритетных серверах · до 75 Гбит/с\n"
        "🔄 Резервные каналы — всегда онлайн\n"
        f"📊 <b>{combo_plus_gb} ГБ</b> обхода белых списков (LTE) в пакете\n"
        "👨‍👩‍👧‍👦 До 14 устройств одновременно\n"
        "<i>Пакет ГБ не сгорает — тратится только на LTE-серверах</i></blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад к скидке", callback_data="bcg1y40:menu")],
    ])
    return text, keyboard


@router.callback_query(F.data == "broadcast_gift_1y_40")
async def callback_broadcast_gift_1y_40(callback: CallbackQuery, state: FSMContext):
    """User clicked «🎁 1 год со скидкой 40%» in a broadcast → tariff menu.

    Скидка одноразовая (FSM-override), не пишется в user_discounts.
    Реализация зеркальная callback_broadcast_gift_3m — тот же
    компактный, безопасный паттерн.

    Перед экраном тарифов проигрываем ту же reveal-сценку, что и у
    «Посмотреть подарок»: 🏆 → 2 сек → удалить → экран выбора тарифа.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id

    reveal_msg = None
    try:
        reveal_msg = await callback.bot.send_message(
            chat_id,
            _GIFT1Y40_REVEAL_EMOJI,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_REVEAL_SEND_FAIL user=%s err=%s", callback.from_user.id, e)

    if reveal_msg is not None:
        await asyncio.sleep(_GIFT1Y40_REVEAL_PAUSE_SECONDS)
        try:
            await callback.bot.delete_message(chat_id, reveal_msg.message_id)
        except Exception:
            # Юзер сам удалил / Telegram отказал — не критично, идём дальше.
            pass

    text, keyboard = _gift1y40_tariff_menu()
    try:
        await callback.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_RENDER_FAIL user=%s err=%s", callback.from_user.id, e)

    logger.info("BROADCAST_GIFT1Y40_SHOWN user=%s", callback.from_user.id)


@router.callback_query(F.data == "bcg1y40:menu")
async def callback_broadcast_gift_1y_40_menu(callback: CallbackQuery, state: FSMContext):
    """Re-render меню тарифов (used as «back» from info / period screens)."""
    try:
        await callback.answer()
    except Exception:
        pass
    text, keyboard = _gift1y40_tariff_menu()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_MENU_FAIL user=%s err=%s", callback.from_user.id, e)


@router.callback_query(F.data == "bcg1y40:info")
async def callback_broadcast_gift_1y_40_info(callback: CallbackQuery, state: FSMContext):
    """Full descriptions всех четырёх годовых тарифов со скидкой."""
    try:
        await callback.answer()
    except Exception:
        pass
    text, keyboard = _gift1y40_info_text_and_keyboard()
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_INFO_FAIL user=%s err=%s", callback.from_user.id, e)


@router.callback_query(F.data.startswith("bcg1y40:tariff:"))
async def callback_broadcast_gift_1y_40_tariff(callback: CallbackQuery, state: FSMContext):
    """Выбран тариф — показываем экран периодов (30/90/180/365)."""
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        tariff = callback.data.split(":", 2)[2]
    except IndexError:
        await callback.answer("Ошибка", show_alert=True)
        return
    menu = _gift1y40_period_menu(tariff)
    if menu is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    text, keyboard = menu
    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    except Exception as e:
        logger.warning("BROADCAST_GIFT1Y40_TARIFF_FAIL user=%s err=%s", callback.from_user.id, e)


@router.callback_query(F.data.startswith("bcg1y40:buy:"))
async def callback_broadcast_gift_1y_40_buy(callback: CallbackQuery, state: FSMContext):
    """User picked tariff + period — jump to payment-method selection.

    Скидка (40% на 365) закладывается в FSM `final_price_kopecks`.
    Остальные периоды летят по обычной цене. Никаких мутаций
    user_discounts.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    _, _, tariff, period_str = parts
    try:
        period_days = int(period_str)
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    if period_days not in _GIFT1Y40_PERIODS:
        await callback.answer("Неверный период", show_alert=True)
        return

    price_rubles = _gift1y40_final_price(tariff, period_days)
    if price_rubles is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    price_kopecks = price_rubles * 100

    if tariff in ("combo_basic", "combo_plus"):
        combo_info = config.COMBO_TARIFFS.get(tariff, {}).get(period_days, {})
        base_tariff = combo_info.get("base_tariff")
        gb = combo_info.get("gb", 0)
    else:
        base_tariff = tariff
        gb = 0

    if base_tariff not in config.TARIFFS:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    from app.handlers.common.states import PurchaseState
    fsm_update = dict(
        tariff_type=base_tariff,
        period_days=period_days,
        final_price_kopecks=price_kopecks,
        combo_bypass_gb=gb,
    )
    # discount_percent пишем только для 365 — на других периодах цена
    # обычная, discount-показ в чекауте не нужен.
    if period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED:
        fsm_update["discount_percent"] = _GIFT1Y40_DISCOUNT_PERCENT
    await state.update_data(**fsm_update)
    await state.set_state(PurchaseState.choose_payment_method)

    logger.info(
        "BROADCAST_GIFT1Y40_BUY user=%s tariff=%s base=%s period=%s "
        "combo_gb=%s price_kopecks=%s discounted=%s",
        telegram_id, tariff, base_tariff, period_days, gb, price_kopecks,
        period_days == _GIFT1Y40_PERIOD_DAYS_DISCOUNTED,
    )

    from app.handlers.payments.method_select import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, period_days, price_kopecks)
