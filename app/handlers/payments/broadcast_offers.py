"""
User-facing buttons of broadcasts sent from the web dashboard
(app/api/dashboard/routes/broadcasts.py::_build_reply_markup): discount /
gift offers (broadcast_promo_buy:, broadcast_gift_combo:, broadcast_gift_1m,
bcg1m:*, broadcast_gift_3m, bcg3m:*, broadcast_gift_1y_40, bcg1y40:*,
broadcast_gift_reveal:), promo traffic packs (broadcast_promo_traffic[_ext]:)
and broadcast_back_to_tariffs.

Moved verbatim from app/handlers/admin/broadcast.py when the old in-bot
admin panel (and its broadcast wizard) was removed (owner 2026-09-14).
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text


broadcast_offers_router = Router()


logger = logging.getLogger(__name__)


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_promo_buy:"))
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
            keep_max=True,  # never lower a bigger active discount
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


# === gift_combo: персональный подарок Combo Basic 1 мес со скидкой ===
# Кнопка «🎁 Забрать подарок» в рассылке. Скидка (% + часы жизни) —
# из полей самой рассылки. Тариф зашит: Combo Basic 30 дней.
_GIFT_COMBO_TARIFF = "combo_basic"


_GIFT_COMBO_PERIOD_DAYS = 30


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_gift_combo:"))
async def callback_broadcast_gift_combo(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Забрать подарок' в рассылке — активируем
    персональную скидку на Combo Basic 1 мес + отправляем экран выбора
    способа оплаты с готовой ценой и custom-текстом."""
    try:
        await callback.answer()
    except Exception:
        pass

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        # Тянем скидку из рассылки (% + часы жизни задал админ в wizard).
        discount = await database.get_broadcast_discount(broadcast_id)
        if not discount:
            await callback.message.answer(
                "❌ Скидка не найдена. Попробуй позже или напиши в поддержку.",
                parse_mode="HTML",
            )
            return

        discount_percent = int(discount.get("discount_percent") or 0)
        discount_hours = int(discount.get("discount_hours") or 24)

        # Читаем Combo Basic 30 дней: цена + GB бонус + базовый тариф.
        combo_info = config.COMBO_TARIFFS.get(_GIFT_COMBO_TARIFF, {}).get(_GIFT_COMBO_PERIOD_DAYS, {})
        base_price = combo_info.get("price") or 0
        combo_gb = combo_info.get("gb") or 0
        base_tariff = combo_info.get("base_tariff") or "basic"

        if not base_price or base_tariff not in config.TARIFFS:
            await callback.message.answer(
                "❌ Тариф Combo Basic сейчас недоступен.", parse_mode="HTML",
            )
            return

        # Применяем скидку глобально к юзеру (create_user_discount) —
        # чтобы срабатывала в любом покупательском flow, не только здесь.
        # Плюс явно посчитаем цену для этого экрана.
        final_price_rubles = round(base_price * (100 - discount_percent) / 100)
        final_price_kopecks = final_price_rubles * 100

        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=discount_hours)
        try:
            await database.create_user_discount(
                telegram_id=telegram_id,
                discount_percent=discount_percent,
                expires_at=expires_at,
                created_by=config.ADMIN_TELEGRAM_ID,
                keep_max=True,  # never lower a bigger active discount
            )
        except Exception as e:
            logger.warning("BROADCAST_GIFT_COMBO discount_create failed user=%s: %s", telegram_id, e)

        # FSM state — как в gift_1m: указываем что покупается Combo (базовый
        # тариф + comboBypassGB) с уже посчитанной ценой.
        from app.handlers.common.states import PurchaseState
        await state.update_data(
            tariff_type=base_tariff,
            period_days=_GIFT_COMBO_PERIOD_DAYS,
            final_price_kopecks=final_price_kopecks,
            discount_percent=discount_percent,
            combo_bypass_gb=combo_gb,
        )
        await state.set_state(PurchaseState.choose_payment_method)

        # Custom-инфо для юзера ПЕРЕД экраном оплаты.
        info_text = (
            f"🎁 <b>Вы выбрали Combo Basic · {discount_percent}% скидки</b>\n\n"
            f"Вам будут доступны безлимитные сервера на срок 1 месяц "
            f"и также дополнительно <b>{combo_gb} ГБ</b> обхода белых списков!\n\n"
            f"💎 Это спец-цена специально для тебя."
        )
        await callback.message.answer(info_text, parse_mode="HTML")

        # Показываем экран выбора способа оплаты с уже посчитанной ценой.
        from app.handlers.payments.payment_method_selection import show_payment_method_selection
        await show_payment_method_selection(
            callback, base_tariff, _GIFT_COMBO_PERIOD_DAYS, final_price_kopecks,
        )

        logger.info(
            "BROADCAST_GIFT_COMBO_ACTIVATED user=%s broadcast=%s disc=%s%% "
            "hours=%s base_price=%s final=%s combo_gb=%s",
            telegram_id, broadcast_id, discount_percent, discount_hours,
            base_price, final_price_rubles, combo_gb,
        )

    except Exception as e:
        logger.exception(f"Error activating gift_combo: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


_GIFT3M_DISCOUNT_PERCENT = 30


_GIFT3M_PERIOD_DAYS = 90


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


@broadcast_offers_router.callback_query(F.data == "broadcast_gift_1m")
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


@broadcast_offers_router.callback_query(F.data.startswith("bcg1m:buy:"))
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

    from app.handlers.payments.payment_method_selection import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, _GIFT1M_PERIOD_DAYS, price_kopecks)


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


@broadcast_offers_router.callback_query(F.data == "broadcast_gift_3m")
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


@broadcast_offers_router.callback_query(F.data == "bcg3m:menu")
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


@broadcast_offers_router.callback_query(F.data == "bcg3m:info")
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


@broadcast_offers_router.callback_query(F.data.startswith("bcg3m:buy:"))
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

    from app.handlers.payments.payment_method_selection import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, _GIFT3M_PERIOD_DAYS, price_kopecks)


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


@broadcast_offers_router.callback_query(F.data == "broadcast_gift_1y_40")
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


@broadcast_offers_router.callback_query(F.data == "bcg1y40:menu")
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


@broadcast_offers_router.callback_query(F.data == "bcg1y40:info")
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


@broadcast_offers_router.callback_query(F.data.startswith("bcg1y40:tariff:"))
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


@broadcast_offers_router.callback_query(F.data.startswith("bcg1y40:buy:"))
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

    from app.handlers.payments.payment_method_selection import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, period_days, price_kopecks)


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_promo_traffic:"))
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


_GIFT_REVEAL_PERCENT_DEFAULT = 20  # fallback для рассылок без gift_reveal_percent в DB


_GIFT_REVEAL_HOURS = 48


_GIFT_REVEAL_EMOJI = '<tg-emoji emoji-id="5210956306952758910">👀</tg-emoji>'


_GIFT_REVEAL_PRESENT = '<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji>'


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_gift_reveal:"))
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

        # 4) reveal-сообщение с динамическим процентом
        await callback.bot.send_message(
            chat_id,
            f"<b>Для тебя подарок {percent}% скидка на любую подписку!</b> {_GIFT_REVEAL_PRESENT}",
            parse_mode="HTML",
        )

        # 5) применяем скидку %/48ч
        expires_at = datetime.now(timezone.utc) + timedelta(hours=_GIFT_REVEAL_HOURS)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
            keep_max=True,  # never lower a bigger active discount
        )

        # 5) короткая пауза перед экраном тарифов — отделить визуально
        await asyncio.sleep(0.03)

        # 6) показываем экран выбора тарифов — get_user_discount внутри
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


@broadcast_offers_router.callback_query(F.data == "broadcast_back_to_tariffs")
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


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_promo_traffic_ext:"))
async def callback_broadcast_promo_traffic_ext(callback: CallbackQuery):
    """Расширенные паки трафика (300+ ГБ) со скидкой из broadcast.

    Юзер нажимает «📦 Больше объёма →» на экране промо-трафика — попадает
    сюда. Скидка уже применена при первом клике (broadcast_promo_traffic),
    здесь только рендерим экран с extended-паками.
    """
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        discount = await database.get_broadcast_discount(broadcast_id)
        discount_percent = discount.get("discount_percent", 0) if discount else 0

        import math

        def _strikethrough(text: str) -> str:
            return "".join(ch + "̶" for ch in str(text))

        buttons = []
        for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
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
            text="← Основные паки",
            callback_data=f"broadcast_promo_traffic:{broadcast_id}",
        )])

        text = "📦 <b>Большие паки трафика</b>\n\nЧем больше пак — тем дешевле каждый гигабайт."
        if discount_percent > 0:
            text = f"🎁 Скидка {discount_percent}% активна 24 часа.\n\n" + text

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error rendering extended traffic packs in broadcast: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)
