"""
Payment-related callback handlers: buy, tariff selection, payment methods.
"""
import logging
import time

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.screens import _open_buy_screen, show_tariffs_main_screen
from app.handlers.payments.payment_method_selection import show_payment_method_selection
from app.handlers.common.utils import (
    safe_edit_text,
    get_promo_session,
    validate_callback_data,
)
from app.handlers.common.states import PromoCodeInput, PurchaseState
from app.handlers.common.emoji import CE
from app.core.structured_logger import log_event

payments_callbacks_router = Router()
logger = logging.getLogger(__name__)



_TARIFF_META = {
    "basic":       {"icon": "⚡️", "name": "Basic",       "desc_key": "buy.tariff_basic_desc"},
    "plus":        {"icon": "👑", "name": "Plus",        "desc_key": "buy.tariff_plus_desc"},
    "combo_basic": {"icon": "🚀", "name": "Комбо Basic", "desc_key": "combo.tariff_basic"},
    "combo_plus":  {"icon": "🚀", "name": "Комбо Plus",  "desc_key": "combo.tariff_plus"},
}


def _period_badge(period_days: int) -> str:
    """Emotional badge for period buttons: ⭐ for 3 mo (popular), 🔥 for 12+ mo (best deal)."""
    if period_days == 90:
        return "⭐"
    if period_days >= 365:
        return "🔥"
    return ""

def _current_tariff_key(sub) -> str:
    """Determine effective tariff key including combo flag."""
    if not sub:
        return ""
    sub_type = (sub.get("subscription_type") or "basic").strip().lower()
    is_combo = sub.get("is_combo", False)
    if is_combo:
        return f"combo_{sub_type}"  # combo_basic / combo_plus
    return sub_type  # basic / plus


def _tariff_name(language: str, key: str) -> str:
    """«Basic» / «Plus» / «Комбо Basic» / «Combo Plus» in the user's language."""
    return i18n_get_text(language, f"tariff.name_{key}")


def _management_screen(language: str, current_key: str):
    """(text, keyboard) of «Управление подпиской» — RU/EN (08 M9: was hardcoded RU)."""
    from app.handlers.common.keyboards import CE
    meta = _TARIFF_META[current_key]
    text = i18n_get_text(language, "buy.manage_title", tariff_desc=i18n_get_text(language, meta["desc_key"]))
    renew_cb = f"combo_tariff:{current_key}" if current_key.startswith("combo_") else f"tariff:{current_key}"
    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.manage_renew", name=_tariff_name(language, current_key)),
            callback_data=renew_cb,
            icon_custom_emoji_id=CE["renew"],
            style="success",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.manage_switch"),
            callback_data="switch_tariff_menu",
            icon_custom_emoji_id=CE["my_sub"],
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.manage_buy_gb"),
            callback_data="buy_traffic",
            icon_custom_emoji_id=CE["traffic"],
            style="success",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def open_buy_or_manage(event, bot, state: FSMContext) -> None:
    """ONE entry for «Продлить VPN» / «Купить VPN» and /buy (08 M13: /buy skipped
    «Управление подпиской»): a Basic/Plus/Combo subscriber gets the management
    screen, anyone else (no subscription, trial, bypass-only) the tariff screen."""
    telegram_id = event.from_user.id
    sub = await database.get_subscription(telegram_id)
    current_key = _current_tariff_key(sub)
    if not sub or sub.get("is_bypass_only") or current_key not in _TARIFF_META:
        await _open_buy_screen(event, bot, state)
        return
    language = await resolve_user_language(telegram_id)
    text, keyboard = _management_screen(language, current_key)
    # combo_bypass_gb=0: a Combo flag left from an abandoned Combo screen must not
    # turn the next Basic/Plus purchase into Combo at the Basic price (P0).
    await state.update_data(purchase_id=None, tariff_type=None, period_days=None, combo_bypass_gb=0)
    await state.set_state(PurchaseState.choose_tariff)
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
        await safe_edit_text(event.message, text, reply_markup=keyboard, bot=bot, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@payments_callbacks_router.callback_query(F.data == "menu_buy_vpn")
async def callback_buy_vpn(callback: CallbackQuery, state: FSMContext):
    """Управление подпиской: продлить текущий / сменить тарифный план."""
    if not await ensure_db_ready_callback(callback):
        return
    await open_buy_or_manage(callback, callback.bot, state)


@payments_callbacks_router.callback_query(
    F.data == "switch_tariff_menu",
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_period, default_state),
)
async def callback_switch_tariff_menu(callback: CallbackQuery, state: FSMContext):
    """Меню смены тарифа — показываем все доступные тарифы кроме текущего."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    sub = await database.get_subscription(telegram_id)
    current_key = _current_tariff_key(sub)

    # Owner 2026-09-14: the switch applies IMMEDIATELY (grant_access changes the
    # tariff for the whole remaining period) — the text says so (i18n RU/EN).
    text = (
        f"{i18n_get_text(language, 'tariff_switch.menu_title')}\n\n"
        f"{i18n_get_text(language, 'tariff_switch.applies_now')}\n\n"
        f"{i18n_get_text(language, 'tariff_switch.available')}"
    )

    from app.handlers.common.keyboards import CE
    buttons = []
    for key, meta in _TARIFF_META.items():
        if key == current_key:
            continue
        buttons.append([InlineKeyboardButton(
            text=f"{meta['icon']} {_tariff_name(language, key)}",
            callback_data=f"switch_tariff:{key}",
            style="success",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back", "Назад"),
        callback_data="menu_buy_vpn",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@payments_callbacks_router.callback_query(
    F.data.startswith("switch_tariff:"),
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_period, default_state),
)
async def callback_switch_tariff(callback: CallbackQuery, state: FSMContext):
    """Экран нового тарифа с описанием и выбором периода."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    new_tariff = callback.data.split(":")[1]
    logger.info(
        "SWITCH_TARIFF_CLICK: tg=%s tariff=%s state=%s",
        telegram_id, new_tariff, await state.get_state(),
    )
    if new_tariff not in _TARIFF_META:
        logger.warning(
            "SWITCH_TARIFF_UNKNOWN_META: tg=%s tariff=%s known=%s",
            telegram_id, new_tariff, list(_TARIFF_META.keys()),
        )
        return

    meta = _TARIFF_META[new_tariff]
    is_combo = new_tariff.startswith("combo_")

    desc_text = i18n_get_text(language, meta['desc_key'])
    applies_now = i18n_get_text(language, "tariff_switch.applies_now")  # owner: switch is immediate

    # RU/EN (08 M9: «Переход на …», the Combo benefits and «Выберите период:» were hardcoded RU).
    title = i18n_get_text(language, "tariff_switch.title", icon=meta["icon"], name=_tariff_name(language, new_tariff))
    choose = i18n_get_text(language, "tariff_switch.choose_period")
    if is_combo:
        # Для комбо — показываем преимущества комбо подписки
        combo_benefits = i18n_get_text(language, "tariff_switch.combo_benefits")
        text = f"{title}\n\n{desc_text}\n\n{combo_benefits}\n\n{applies_now}\n{choose}"
    else:
        text = f"{title}\n\n{desc_text}\n\n{applies_now}\n{choose}"

    buttons = []

    if is_combo:
        # Комбо-тариф: берём цены из COMBO_TARIFFS + применяем цепочку скидок
        tariff_data = config.COMBO_TARIFFS.get(new_tariff, {})
        period_keys = {30: "combo.period_1", 90: "combo.period_3", 180: "combo.period_6", 365: "combo.period_12", 730: "combo.period_24"}
        promo_session = await get_promo_session(state)
        promo_code = promo_session.get("promo_code") if promo_session else None
        for period_days, info in tariff_data.items():
            try:
                price_info = await subscription_service.calculate_price(
                    telegram_id=telegram_id,
                    tariff=info["base_tariff"],
                    period_days=period_days,
                    promo_code=promo_code,
                    base_price_override_rubles=info["price"],
                )
                price_rub = price_info["final_price_kopecks"] // 100
            except Exception:
                price_rub = info["price"]
            btn_text = i18n_get_text(language, period_keys.get(period_days, "combo.period_1"), gb=info["gb"], price=price_rub)
            buttons.append([InlineKeyboardButton(
                text=btn_text,
                callback_data=f"combo_period:{new_tariff}:{period_days}",
                style="primary",
            )])
    else:
        # Обычный тариф: берём цены из TARIFFS + calculate_price
        promo_session = await get_promo_session(state)
        promo_code = promo_session.get("promo_code") if promo_session else None

        await state.update_data(tariff_type=new_tariff, purchase_id=None, period_days=None, combo_bypass_gb=0)
        await state.set_state(PurchaseState.choose_period)

        periods = config.TARIFFS.get(new_tariff, {})
        for period_days, period_data in periods.items():
            try:
                price_info = await subscription_service.calculate_price(
                    telegram_id=telegram_id,
                    tariff=new_tariff,
                    period_days=period_days,
                    promo_code=promo_code
                )
            except Exception as e:
                logger.warning(
                    "TARIFF_PERIOD_PRICE_FAILED user=%s tariff=%s period=%s err=%s",
                    telegram_id, new_tariff, period_days, type(e).__name__,
                )
                continue

            base_price_rubles = price_info["base_price_kopecks"] / 100.0
            final_price_rubles = price_info["final_price_kopecks"] / 100.0
            has_discount = price_info["discount_percent"] > 0

            if period_days == 730:
                period_text = i18n_get_text(language, "buy.period_24_months")
            else:
                months = period_days // 30
                if months == 1:
                    period_text = i18n_get_text(language, "buy.period_1")
                elif months in [2, 3, 4]:
                    period_text = i18n_get_text(language, "buy.period_2_4", months=months)
                else:
                    period_text = i18n_get_text(language, "buy.period_5_plus", months=months)

            price_int = int(final_price_rubles)
            badge = _period_badge(period_days)
            # en texts carry "{gb} GB" (ru do not) — same kwarg as callback_tariff_type
            traffic_gb = config.TRAFFIC_LIMITS_GB.get(new_tariff, {}).get(period_days, 0)

            if has_discount:
                if badge:
                    button_text = i18n_get_text(
                        language, "buy.button_price_discount_badge",
                        base=int(base_price_rubles), final=price_int, period=period_text, badge=badge,
                    )
                else:
                    button_text = i18n_get_text(
                        language, "buy.button_price_discount",
                        base=int(base_price_rubles), final=price_int, period=period_text, gb=traffic_gb,
                    )
            else:
                if badge:
                    button_text = i18n_get_text(
                        language, "buy.button_price_badge",
                        price=price_int, period=period_text, badge=badge,
                    )
                else:
                    button_text = i18n_get_text(
                        language, "buy.button_price",
                        price=price_int, period=period_text, gb=traffic_gb,
                    )

            buttons.append([InlineKeyboardButton(
                text=button_text,
                callback_data=f"period:{new_tariff}:{period_days}",
                style="primary",
            )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="switch_tariff_menu",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@payments_callbacks_router.callback_query(
    F.data.startswith("tariff:"),
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_period, default_state),
)
async def callback_tariff_type(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 1 — Выбор тарифа (Basic/Plus)
    
    КРИТИЧНО:
    - НЕ создает pending_purchase
    - Только сохраняет tariff_type в FSM
    - Переводит в choose_period
    - Показывает экран выбора периода
    """
    try:
        await callback.answer()
    except Exception:
        pass

    if not validate_callback_data(callback.data):
        logger.warning(
            "Invalid callback_data from user %s: %s",
            callback.from_user.id,
            (callback.data or "")[:50],
        )
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # CRITICAL FIX: Очищаем PromoCodeInput state при переходе к выбору тарифа
    # Это закрывает ввод промокода если пользователь был в этом состоянии
    current_state = await state.get_state()
    if current_state == PromoCodeInput.waiting_for_promo.state:
        await state.set_state(None)
        current_state = None
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_tariff, choose_period (назад) или None
    valid_states = (PurchaseState.choose_tariff.state, PurchaseState.choose_period.state, None)
    if current_state not in valid_states:
        log_event(
            logger,
            component="payments",
            operation="fsm_transition",
            outcome="failed",
            reason="invalid_state_for_tariff",
            correlation_id=str(telegram_id),
            level="warning",
        )
        await state.clear()
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        await show_tariffs_main_screen(callback, state)
        return
    
    # Парсим callback_data безопасно (формат: "tariff:basic" или "tariff:plus")
    try:
        parts = callback.data.split(":")
        if len(parts) < 2:
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
            return
        tariff_type = parts[1]  # "basic" или "plus"
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid tariff callback_data: {callback.data}, error={e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    # Валидация тарифа
    if tariff_type not in config.TARIFFS:
        logger.error(f"Invalid tariff_type: {tariff_type}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    log_event(
        logger,
        component="payments",
        operation="fsm_transition",
        outcome="success",
        reason="tariff_selected",
        correlation_id=str(telegram_id),
    )
    # КРИТИЧНО: Сохраняем tariff_type в FSM state
    # Промо-сессия НЕ сбрасывается при выборе тарифа - она независима от покупки
    await state.update_data(tariff_type=tariff_type, combo_bypass_gb=0)
    
    # КРИТИЧНО: Получаем промо-сессию (проверяет срок действия автоматически)
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    # КРИТИЧНО: НЕ создаем pending_purchase - только показываем кнопки периодов
    # Определяем описание тарифа в зависимости от типа
    if tariff_type == "basic":
        text = i18n_get_text(language, "buy.tariff_basic_desc")
    else:
        text = i18n_get_text(language, "buy.tariff_plus_desc")

    buttons = []

    # Получаем цены для выбранного тарифа с учетом скидок
    periods = config.TARIFFS[tariff_type]
    
    # КРИТИЧНО: Логируем контекст промо-сессии для диагностики
    if promo_session:
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        logger.info(
            f"Price calculation with promo session: user={telegram_id}, tariff={tariff_type}, "
            f"promo_code={promo_code}, discount={promo_session.get('discount_percent')}%, "
            f"expires_in={expires_in}s"
        )
    
    for period_days, period_data in periods.items():
        # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены для отображения
        try:
            price_info = await subscription_service.calculate_price(
                telegram_id=telegram_id,
                tariff=tariff_type,
                period_days=period_days,
                promo_code=promo_code
            )
        except (subscription_service.InvalidTariffError, subscription_service.PriceCalculationError) as e:
            logger.error(f"Error calculating price: tariff={tariff_type}, period={period_days}, error={e}")
            continue  # Пропускаем этот период если ошибка расчета
        
        base_price_rubles = price_info["base_price_kopecks"] / 100.0
        final_price_rubles = price_info["final_price_kopecks"] / 100.0
        has_discount = price_info["discount_percent"] > 0

        # Admin-managed global-discount (migration 069): если оригинал
        # из config выше базы после нашего override — покажем страйк
        # от оригинала (юзеру видно «199 → 149»). Даже если у него
        # промо-кода нет.
        _orig_kop = price_info.get("original_config_price_kopecks")
        if _orig_kop and int(_orig_kop) > price_info["base_price_kopecks"]:
            base_price_rubles = int(_orig_kop) / 100.0
            has_discount = True

        # КРИТИЧНО: Логируем расчет цены для диагностики
        logger.debug(
            f"Price recalculated: tariff={tariff_type}, period={period_days}, "
            f"base={price_info['base_price_kopecks']}, discount={price_info['discount_percent']}%, "
            f"final={price_info['final_price_kopecks']}, promo_code={promo_code or 'none'}"
        )
        
        # Формируем правильное склонение периода
        if period_days == 730:
            period_text = i18n_get_text(language, "buy.period_24_months")
        else:
            months = period_days // 30
            if months == 1:
                period_text = i18n_get_text(language, "buy.period_1")
            elif months in [2, 3, 4]:
                period_text = i18n_get_text(language, "buy.period_2_4", months=months)
            else:
                period_text = i18n_get_text(language, "buy.period_5_plus", months=months)
        
        # Traffic GB for this period
        traffic_gb = config.TRAFFIC_LIMITS_GB.get(tariff_type, {}).get(period_days, 0)

        badge = _period_badge(period_days)

        # Формируем текст кнопки с зачеркнутой ценой (если есть скидка)
        if has_discount:
            key = "buy.button_price_discount_badge" if badge else "buy.button_price_discount"
            button_text = i18n_get_text(
                language, key,
                base=int(base_price_rubles), final=int(final_price_rubles), period=period_text, gb=traffic_gb, badge=badge,
            )
        else:
            key = "buy.button_price_badge" if badge else "buy.button_price"
            button_text = i18n_get_text(
                language, key,
                price=int(final_price_rubles), period=period_text, gb=traffic_gb, badge=badge,
            )

        # КРИТИЧНО: callback_data БЕЗ purchase_id - только tariff и period
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"period:{tariff_type}:{period_days}",
            style="primary",
        )])
    
    # Кнопка назад: по умолчанию `menu_buy_vpn` (показывает либо экран
    #   «Управление подпиской» если есть активная подписка, либо
    #   выбор тарифа). В flow из рассылки (gift_reveal etc.) юзер
    #   уже на экране выбора тарифа, и «Назад» должна возвращать
    #   ровно туда же — иначе он попадает на управление подпиской.
    #   Маркер `from_broadcast` ставится при заходе из broadcast-CTA
    #   (callback_broadcast_gift_reveal) и переживает все клики
    #   tariff/period пока FSM-state не сброшен.
    fsm_data = await state.get_data()
    from_broadcast = bool(fsm_data.get("from_broadcast"))

    if from_broadcast:
        back_callback = "broadcast_back_to_tariffs"
    else:
        back_callback = "menu_buy_vpn"

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=back_callback,
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    # Admin-managed global-discount notice (migration 069): если
    # активна глобальная скидка, добавим строку-подпись над кнопками.
    try:
        from app.services import pricing as _pricing
        _gd = await _pricing.get_global_discount()
        _pct = int(_gd.get("global_discount_percent") or 0)
        if _pct > 0:
            # Проверим что скидка не истекла
            _until_iso = _gd.get("discount_until_at")
            _active = True
            if _until_iso:
                try:
                    from datetime import datetime, timezone as _tz
                    _until_dt = datetime.fromisoformat(_until_iso.replace("Z", "+00:00"))
                    if _until_dt <= datetime.now(_tz.utc):
                        _active = False
                except Exception:
                    pass
            if _active:
                import html as _html
                # admin free text from the dashboard → escaped for the HTML screen
                _reason = _html.escape(_gd.get("discount_reason") or "") or i18n_get_text(language, "buy.global_discount_default_reason", "Спец-цены")
                _until_str = ""
                if _until_iso:
                    try:
                        from datetime import datetime as _dt
                        _until_dt2 = _dt.fromisoformat(_until_iso.replace("Z", "+00:00"))
                        _until_str = _until_dt2.strftime('%d.%m')
                    except Exception:
                        pass
                if _until_str:
                    _notice = i18n_get_text(
                        language, "buy.global_discount_notice_dated",
                        "\n\n🎁 <b>Скидка −{pct}%</b> · {reason} · до {date}",
                        pct=_pct, reason=_reason, date=_until_str,
                    )
                else:
                    _notice = i18n_get_text(
                        language, "buy.global_discount_notice",
                        "\n\n🎁 <b>Скидка −{pct}%</b> · {reason}",
                        pct=_pct, reason=_reason,
                    )
                text = (text or "") + _notice
    except Exception as _e:
        logger.warning("global-discount notice render failed: %s", _e)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    # Pass bot so that если предыдущий экран был photo (напр. invoice
    # Wata с картинкой) — safe_edit_text корректно удалит photo-message
    # и отправит текст вместо edit_caption поверх фотки.
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)

    # КРИТИЧНО: Переходим в состояние choose_period
    await state.set_state(PurchaseState.choose_period)


@payments_callbacks_router.callback_query(
    F.data.startswith("period:"),
    StateFilter(PurchaseState.choose_period),
)
async def callback_tariff_period(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 2 — Выбор периода тарифа
    
    КРИТИЧНО:
    - НЕ создает pending_purchase
    - НЕ создает invoice
    - Только сохраняет period_days и final_price_kopecks в FSM
    - Переводит в choose_payment_method
    - Открывает экран выбора способа оплаты
    """
    try:
        await callback.answer()
    except Exception:
        pass

    if not validate_callback_data(callback.data):
        logger.warning(
            "Invalid callback_data from user %s: %s",
            callback.from_user.id,
            (callback.data or "")[:50],
        )
        return

    telegram_id = callback.from_user.id
    
    # CRITICAL FIX: Очищаем PromoCodeInput state при переходе к выбору периода
    # Это закрывает ввод промокода если пользователь был в этом состоянии
    current_state = await state.get_state()
    if current_state == PromoCodeInput.waiting_for_promo.state:
        await state.set_state(None)
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Парсим callback_data безопасно (формат: "period:basic:30")
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            error_text = i18n_get_text(language, "errors.tariff")
            await callback.answer(error_text, show_alert=True)
            logger.error(f"Invalid period callback_data format: {callback.data}")
            return
        
        tariff_type = parts[1]  # "basic" или "plus"
        period_days = int(parts[2])
    except (IndexError, ValueError) as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid period callback_data: {callback.data}, error={e}")
        return
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_period
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_period.state:
        log_event(
            logger,
            component="payments",
            operation="fsm_transition",
            outcome="failed",
            reason="invalid_state_for_period",
            correlation_id=str(telegram_id),
            level="warning",
        )
        await state.clear()
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        await show_tariffs_main_screen(callback, state)
        return
    
    # Валидация тарифа и периода
    if tariff_type not in config.TARIFFS:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid tariff_type: {tariff_type}")
        return
    
    if period_days not in config.TARIFFS[tariff_type]:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid period_days: {period_days} for tariff {tariff_type}")
        return
    
    # КРИТИЧНО: Проверяем, что tariff_type в FSM соответствует выбранному
    fsm_data = await state.get_data()
    stored_tariff = fsm_data.get("tariff_type")
    if stored_tariff != tariff_type:
        logger.warning(f"Tariff mismatch: FSM={stored_tariff}, callback={tariff_type}, user={telegram_id}")
        # Обновляем tariff_type в FSM
        await state.update_data(tariff_type=tariff_type, combo_bypass_gb=0)
    
    # КРИТИЧНО: Получаем промо-сессию (проверяет срок действия автоматически)
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    # КРИТИЧНО: Логируем контекст промо-сессии для диагностики
    if promo_session:
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        discount_percent = promo_session.get("discount_percent", 0)
        logger.info(
            f"Period selection with promo session: user={telegram_id}, tariff={tariff_type}, "
            f"period={period_days}, promo_code={promo_code}, discount={discount_percent}%, "
            f"expires_in={expires_in}s"
        )
    
    # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены
    try:
        price_info = await subscription_service.calculate_price(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            promo_code=promo_code,
        )
    except (subscription_service.InvalidTariffError, subscription_service.PriceCalculationError) as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid tariff/period in calculate_price: user={telegram_id}, tariff={tariff_type}, period={period_days}, error={e}")
        return
    
    # Plus→Basic downgrade: show confirmation before proceeding
    if tariff_type == "basic":
        sub = await database.get_subscription(telegram_id)
        current_sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
        if sub and current_sub_type == "plus":
            await state.update_data(
                tariff_type=tariff_type,
                period_days=period_days,
                final_price_kopecks=price_info["final_price_kopecks"],
                discount_percent=price_info["discount_percent"],
                combo_bypass_gb=0,
                promo_applied=bool(price_info.get("promo_code")),
            )
            downgrade_text = i18n_get_text(
                language, "buy.downgrade_confirm_text",
                "⚠️ Вы переходите с Plus на Basic.\n\nКлюч будет ротирован с выделенного сервера на базовый.\n\nПодтвердить переход?",
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "buy.downgrade_confirm_yes", "⚡️ Да, перейти на Basic"), callback_data="downgrade_confirm_basic", style="primary")],
                [InlineKeyboardButton(text=i18n_get_text(language, "buy.downgrade_confirm_no", "❌ Отмена"), callback_data="tariff:basic", style="primary")]
            ])
            await safe_edit_text(callback.message, downgrade_text, reply_markup=keyboard)
            return
    
    # КРИТИЧНО: Сохраняем данные в FSM state (БЕЗ создания pending_purchase)
    # Промо-сессия НЕ сохраняется здесь - она уже в FSM и независима от покупки
    await state.update_data(
        tariff_type=tariff_type,
        period_days=period_days,
        final_price_kopecks=price_info["final_price_kopecks"],
        discount_percent=price_info["discount_percent"],
        combo_bypass_gb=0,  # regular period chosen → not Combo (P0: stale Combo flag)
        # the session promo code is stored / consumed only if it is the discount
        # that won (owner rule: the largest single discount) — get_applied_promo_code
        promo_applied=bool(price_info.get("promo_code")),
    )
    
    log_event(
        logger,
        component="payments",
        operation="fsm_transition",
        outcome="success",
        reason="period_selected",
        correlation_id=str(telegram_id),
    )
    logger.info(
        f"Period selected: user={telegram_id}, tariff={tariff_type}, period={period_days}, "
        f"base_price_kopecks={price_info['base_price_kopecks']}, final_price_kopecks={price_info['final_price_kopecks']}, "
        f"discount_percent={price_info['discount_percent']}%, discount_type={price_info['discount_type']}, "
        f"promo_code={promo_code or 'none'}"
    )
    
    # КРИТИЧНО: Переходим к выбору способа оплаты (НЕ создаем pending_purchase и invoice)
    await state.set_state(PurchaseState.choose_payment_method)
    await show_payment_method_selection(callback, tariff_type, period_days, price_info["final_price_kopecks"],
                                        back_callback=f"tariff:{tariff_type}")


@payments_callbacks_router.callback_query(
    F.data == "downgrade_confirm_basic",
    StateFilter(PurchaseState.choose_period),
)
async def callback_downgrade_confirm_basic(callback: CallbackQuery, state: FSMContext):
    """Подтверждение перехода Plus→Basic: продолжаем поток оплаты Basic."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type", "basic")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    if period_days is None or final_price_kopecks is None:
        error_text = i18n_get_text(language, "errors.session_expired")
        try:
            await callback.answer(error_text, show_alert=True)
        except Exception:
            pass
        await show_tariffs_main_screen(callback, state)
        return
    await state.update_data(confirmed_downgrade=True)
    await state.set_state(PurchaseState.choose_payment_method)
    await show_payment_method_selection(callback, tariff_type, period_days, final_price_kopecks,
                                        back_callback=f"tariff:{tariff_type}")


@payments_callbacks_router.callback_query(F.data == "enter_promo")
async def callback_enter_promo(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки ввода промокода"""
    try:
        await callback.answer()
    except Exception:
        pass

    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем активную промо-сессию
    promo_session = await get_promo_session(state)
    if promo_session:
        # Промокод уже применён - показываем сообщение
        text = i18n_get_text(language, "buy.promo_applied")
        await callback.message.answer(text, parse_mode="HTML")
        return

    # CRITICAL FIX: Очищаем предыдущие FSM состояния перед установкой нового
    # Это гарантирует, что пользователь не останется в "зависшем" состоянии
    await state.set_state(None)
    
    # Устанавливаем состояние ожидания промокода
    await state.set_state(PromoCodeInput.waiting_for_promo)

    text = i18n_get_text(language, "buy.enter_promo_text")
    # «Отмена» (08 M17): without it any next text was «неверный промокод».
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=i18n_get_text(language, "payment.btn_cancel"),
        callback_data="promo_back",
        style="primary",
    )]])
    await callback.message.answer(text, reply_markup=cancel_kb, parse_mode="HTML")


@payments_callbacks_router.callback_query(F.data == "promo_back")
async def callback_promo_back(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад' при ошибке промокода - возвращает на экран выбора тарифа"""
    # CRITICAL FIX: Очищаем FSM state при выходе с экрана ввода промокода
    await state.clear()
    
    # CRITICAL FIX: Используем каноничный экран тарифов вместо локального render
    await show_tariffs_main_screen(callback, state)


# Старый обработчик tariff_* удалён - теперь используется новый флоу tariff_type -> tariff_period
