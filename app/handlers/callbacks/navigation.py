"""
Simple navigation callbacks: menu_main, back_to_main, settings, about, support, etc.
"""
import asyncio
import io
import logging
import os

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.filters import StateFilter

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.callbacks.language import MAIN_PHOTO_FILE_ID as _MAIN_PHOTO_ID
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.screens import show_profile, _open_help_screen
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_connect_keyboard,
)
router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery):
    """Decorative button — no action."""
    await callback.answer()


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    """Главное меню. Delete + answer to support navigation from photo message (loyalty screen)."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    # Clear all FSM state on navigation (withdrawal, promo, etc.)
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()

    try:
        await callback.message.delete()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)

    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, callback.from_user.id)

    # Всегда отправляем фото с текстом на главном экране
    await callback.bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=_MAIN_PHOTO_ID,
        caption=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню с экрана выдачи ключа"""
    try:
        await callback.answer()
    except Exception:
        pass

    await state.clear()
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=_MAIN_PHOTO_ID,
        caption=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _get_main_text(telegram_id: int, language: str) -> str:
    """Определяет текст главного экрана: обычный, бизнес, bypass-only или без подписки."""
    try:
        sub = await database.get_subscription(telegram_id)
        sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else None
        if sub and sub_type and config.is_biz_tariff(sub_type):
            return i18n_get_text(language, "biz.main_screen")
        if not sub:
            # Check if user ever had a subscription (expired vs new)
            user = await database.get_user(telegram_id)
            trial_used = user.get("trial_used_at") if user else None
            if trial_used:
                text = i18n_get_text(language, "main.welcome_expired")
            else:
                text = i18n_get_text(language, "main.welcome_no_sub")
            return await format_text_with_incident(text, language)
        if sub and sub.get("is_bypass_only"):
            text = i18n_get_text(language, "main.welcome_bypass")
            return await format_text_with_incident(text, language)
    except Exception:
        pass
    text = i18n_get_text(language, "main.welcome")
    return await format_text_with_incident(text, language)


@router.callback_query(F.data == "menu_ecosystem")
async def callback_ecosystem(callback: CallbackQuery):
    """⚪️ Наша экосистема"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    title = i18n_get_text(language, "main.ecosystem_title", "main.ecosystem_title")
    text = i18n_get_text(language, "main.ecosystem_text", "main.ecosystem_text")
    full_text = f"{title}\n\n{text}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "main.about"), callback_data="menu_about")],
        [InlineKeyboardButton(text="✍️ Трекер Only", url="https://t.me/ItsOnlyWbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, full_text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_profile")
async def callback_biz_profile(callback: CallbackQuery):
    """🏢 Мой бизнес — профиль бизнес-подписчика"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    await show_profile(callback, language)


@router.callback_query(F.data == "biz_ecosystem")
async def callback_biz_ecosystem(callback: CallbackQuery):
    """🌐 Экосистема для бизнес-пользователей"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "biz.ecosystem_text")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_control_panel")
async def callback_biz_control_panel(callback: CallbackQuery):
    """🎛 Панель управления"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "biz.control_panel_title")

    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        text += f"\n\n🔗 Ваша ссылка подключения готова."

    from app.handlers.common.keyboards import get_biz_control_panel_keyboard
    keyboard = get_biz_control_panel_keyboard(language)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_copy_login")
async def callback_biz_copy_login(callback: CallbackQuery):
    """📋 Скопировать логин (VPN ключ)"""
    telegram_id = callback.from_user.id
    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        await callback.answer("Скопируйте ссылку выше")
    else:
        await callback.answer("Ключ не найден", show_alert=True)


@router.callback_query(F.data == "biz_copy_password")
async def callback_biz_copy_password(callback: CallbackQuery):
    """🔑 Скопировать пароль (VPN ключ Plus)"""
    telegram_id = callback.from_user.id
    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        await callback.answer("Скопируйте ссылку выше")
    else:
        await callback.answer("Ключ не найден", show_alert=True)


@router.callback_query(F.data == "menu_settings")
async def callback_settings(callback: CallbackQuery):
    """⚙️ Настройки"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    title = i18n_get_text(language, "main.settings_title", "⚙️ Настройки")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗣 Изменить язык", callback_data="change_language")],
        [InlineKeyboardButton(text="🔐 Политика конфиденциальности", callback_data="about_privacy")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.ecosystem", "⚪️ Наша экосистема"),
            callback_data="menu_ecosystem"
        )],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    has_photo = getattr(callback.message, "photo", None) and len(callback.message.photo) > 0
    if has_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text=title, reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        await safe_edit_text(callback.message, title, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "menu_about")
async def callback_about(callback: CallbackQuery):
    """О сервисе. Entry from ecosystem."""
    from app.handlers.common.screens import _open_about_screen
    await _open_about_screen(callback, callback.bot)


@router.callback_query(F.data == "menu_service_status")
async def callback_service_status(callback: CallbackQuery):
    """Статус сервиса"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.service_status_text", "service_status_text")

    incident = await database.get_incident_settings()
    if incident["is_active"]:
        incident_text = incident.get("incident_text") or i18n_get_text(language, "main.incident_banner", "incident_banner")
        warning = i18n_get_text(language, "main.incident_status_warning", incident_text=incident_text)
        text = text + warning

    await safe_edit_text(callback.message, text, reply_markup=get_service_status_keyboard(language), bot=callback.bot)


@router.callback_query(F.data == "about_privacy")
async def callback_privacy(callback: CallbackQuery):
    """Политика конфиденциальности"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.privacy_policy_text", "privacy_policy_text")
    await safe_edit_text(callback.message, text, reply_markup=get_about_keyboard(language), parse_mode="HTML", bot=callback.bot)


@router.callback_query(F.data == "special_offer_buy")
async def callback_special_offer_buy(callback: CallbackQuery, state: FSMContext):
    """Спецпредложение -15% — перенаправляет на экран покупки."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id

    # Проверяем что спецпредложение еще активно
    special_offer = await database.get_special_offer_info(telegram_id)
    if not special_offer:
        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            "⏰ Срок спецпредложения истёк. Вы можете приобрести подписку по обычной цене.",
            parse_mode="HTML",
        )
        return

    # Открываем экран покупки — скидка 15% применится автоматически через calculate_final_price
    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "trial_discount_15")
async def callback_trial_discount_15(callback: CallbackQuery, state: FSMContext):
    """Скидка 15% из уведомления за 3 часа до окончания триала — автоматически применяет скидку"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        from datetime import timedelta, timezone
        from datetime import datetime as dt
        expires_at = dt.now(timezone.utc) + timedelta(days=7)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=15,
            expires_at=expires_at,
            created_by=0,  # system
        )
        await callback.message.answer(
            "🎁 Скидка 15% автоматически применена! Действует 7 дней.\n\nВыберите тариф:",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Failed to apply trial discount for {telegram_id}: {e}")

    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "paid_discount_15")
async def callback_paid_discount_15(callback: CallbackQuery, state: FSMContext):
    """Скидка 15% из уведомления за 3 часа до окончания платной подписки"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        from datetime import timedelta, timezone
        from datetime import datetime as dt
        expires_at = dt.now(timezone.utc) + timedelta(days=7)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=15,
            expires_at=expires_at,
            created_by=0,  # system
        )
        await callback.message.answer(
            "🎁 Скидка 15% автоматически применена! Действует 7 дней.\n\nВыберите тариф:",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Failed to apply paid discount for {telegram_id}: {e}")

    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "menu_instruction")
@router.callback_query(F.data == "instruction")
async def callback_instruction(callback: CallbackQuery):
    """Инструкция. Entry from main menu (menu_instruction) or profile (instruction)."""
    from app.handlers.common.screens import _open_instruction_screen
    await _open_instruction_screen(callback, callback.bot)



@router.callback_query(F.data == "go_profile", StateFilter(default_state))
@router.callback_query(F.data == "go_profile")
async def callback_go_profile(callback: CallbackQuery, state: FSMContext):
    """Переход в профиль с экрана выдачи ключа - работает независимо от FSM состояния"""
    telegram_id = callback.from_user.id
    
    # Немедленная обратная связь пользователю
    await callback.answer()
    
    # Очищаем FSM состояние, если пользователь был в каком-то процессе
    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"Cleared FSM state for user {telegram_id}, was: {current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")
    
    try:
        logger.info(f"Opening profile via go_profile for user {telegram_id}")
        
        language = await resolve_user_language(telegram_id)
        
        await show_profile(callback, language)
        
        logger.info(f"Profile opened successfully via go_profile for user {telegram_id}")
    except Exception as e:
        logger.exception(f"Error opening profile via go_profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            user = await database.get_user(telegram_id)
            language = await resolve_user_language(callback.from_user.id)
            error_text = i18n_get_text(language, "errors.profile_load")
            await callback.message.answer(error_text, parse_mode="HTML")
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")


@router.callback_query(F.data.in_({"copy_key_menu", "copy_key", "copy_key_plus", "copy_vpn_key"}))
async def callback_connect_instead_of_copy(callback: CallbackQuery):
    """Ключи больше не отправляются в боте; показываем кнопку «Подключиться» (Mini App)."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    language = await resolve_user_language(callback.from_user.id)
    await callback.message.answer(
        i18n_get_text(language, "connect.press_button"),
        parse_mode="HTML",
        reply_markup=get_connect_keyboard(language),
    )


@router.callback_query(F.data == "get_sub_key")
async def callback_get_sub_key(callback: CallbackQuery):
    """Отправить ключ подписки с инструкцией по подключению."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            i18n_get_text(language, "get_key.no_subscription", "❌ У вас нет активной подписки."),
            parse_mode="HTML",
        )
        return

    language = await resolve_user_language(telegram_id)
    from app.services.user_subscription_links import get_user_primary_subscription_url
    sub_url = await get_user_primary_subscription_url(telegram_id)

    text = i18n_get_text(language, "get_key.instruction_text",
        "📖 <b>Инструкция по подключению</b>\n\n"
        "<b>Happ</b> — откройте приложение → внизу нажмите на буфер обмена 🗒️ → ключ добавится автоматически\n\n"
        "⸻\n\n"
        "👇 Скопируйте ключ одним нажатием:")

    full_text = f"{text}\n\n<code>{sub_url}</code>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.device_button"),
            callback_data="setup_device",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])

    await safe_edit_text(callback.message, full_text, reply_markup=keyboard, bot=callback.bot)


# ===================== COMBO SUBSCRIPTION =====================

@router.callback_query(F.data == "buy_combo")
async def callback_buy_combo(callback: CallbackQuery):
    """Экран выбора комбо-тарифа (Basic/Plus)."""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)

    text = i18n_get_text(language, "combo.screen_title")
    text += "\n\n" + i18n_get_text(language, "combo.tariff_basic")
    text += "\n\n" + i18n_get_text(language, "combo.tariff_plus")

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "combo.select_basic"),
            callback_data="combo_tariff:combo_basic",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "combo.select_plus"),
            callback_data="combo_tariff:combo_plus",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_buy_vpn",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    # Main screen may be a photo — delete and send new message
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("combo_tariff:"))
async def callback_combo_tariff(callback: CallbackQuery, state: FSMContext):
    """Выбор периода комбо-тарифа."""
    try:
        await callback.answer()
    except Exception:
        pass

    combo_type = callback.data.split(":")[1]  # combo_basic or combo_plus
    if combo_type not in config.COMBO_TARIFFS:
        return

    language = await resolve_user_language(callback.from_user.id)
    tariff = config.COMBO_TARIFFS[combo_type]

    if combo_type == "combo_basic":
        text = i18n_get_text(language, "combo.tariff_basic")
    else:
        text = i18n_get_text(language, "combo.tariff_plus")

    # Check for active promo (FSM) — used for the header label
    from app.handlers.common.utils import get_promo_session
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    discount_pct = promo_session.get("discount_percent", 0) if promo_session else 0

    if discount_pct > 0:
        text += f"\n\n🎁 Промокод: скидка {discount_pct}%\nВыберите период:"
    else:
        text += "\n\nВыберите период:"

    from app.handlers.payments.callbacks import _period_badge
    from app.services.subscriptions import service as subscription_service

    buttons = []
    period_keys = {30: "combo.period_1", 90: "combo.period_3", 180: "combo.period_6", 365: "combo.period_12", 730: "combo.period_24"}
    for period_days, info in tariff.items():
        # Прогоняем через полную цепочку скидок (промокод / VIP / спецоффер / персональная)
        try:
            price_info = await subscription_service.calculate_price(
                telegram_id=callback.from_user.id,
                tariff=info["base_tariff"],
                period_days=period_days,
                promo_code=promo_code,
                base_price_override_rubles=info["price"],
            )
            final_price = price_info["final_price_kopecks"] // 100
        except Exception:
            final_price = info["price"]
        btn_text = i18n_get_text(language, period_keys[period_days], gb=info["gb"], price=final_price)
        badge = _period_badge(period_days)
        if badge:
            btn_text = f"{btn_text} {badge}"
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"combo_period:{combo_type}:{period_days}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_combo",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("combo_period:"))
async def callback_combo_period(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и оплата комбо-тарифа — используем стандартный экран оплаты."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    combo_type = parts[1]
    try:
        period_days = int(parts[2])
    except (ValueError, IndexError):
        return

    if combo_type not in config.COMBO_TARIFFS:
        return
    tariff = config.COMBO_TARIFFS[combo_type]
    if period_days not in tariff:
        return

    info = tariff[period_days]
    base_tariff = info["base_tariff"]
    base_price_kopecks = info["price"] * 100
    gb = info["gb"]

    # Apply full discount chain (promo / VIP / special offer / personal)
    from app.handlers.common.utils import get_promo_session
    from app.services.subscriptions import service as subscription_service
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    try:
        price_info = await subscription_service.calculate_price(
            telegram_id=callback.from_user.id,
            tariff=base_tariff,
            period_days=period_days,
            promo_code=promo_code,
            base_price_override_rubles=info["price"],
        )
        price_kopecks = price_info["final_price_kopecks"]
    except Exception:
        price_kopecks = base_price_kopecks

    # Сохраняем данные в FSM для стандартного платёжного потока
    await state.update_data(
        tariff_type=base_tariff,
        period_days=period_days,
        final_price_kopecks=price_kopecks,
        combo_bypass_gb=gb,
    )
    from app.handlers.common.states import PurchaseState
    await state.set_state(PurchaseState.choose_payment_method)

    from handlers import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, period_days, price_kopecks)


@router.callback_query(F.data.startswith("combo_pay_balance:"))
async def callback_combo_pay_balance(callback: CallbackQuery):
    """Оплата комбо с баланса: активация подписки + начисление трафика обхода."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    combo_type = parts[1]
    try:
        period_days = int(parts[2])
    except (ValueError, IndexError):
        return

    if combo_type not in config.COMBO_TARIFFS:
        return
    info = config.COMBO_TARIFFS[combo_type].get(period_days)
    if not info:
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    price = info["price"]
    gb = info["gb"]
    base_tariff = info["base_tariff"]

    balance = await database.get_user_balance(telegram_id)
    if balance < price:
        text = i18n_get_text(language, "traffic.insufficient_balance")
        buttons = [[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"combo_period:{combo_type}:{period_days}",
        )]]
        await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")
        return

    # 1. Create pending purchase with base tariff
    from app.services.subscriptions import service as subscription_service
    price_kopecks = price * 100
    try:
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=base_tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            is_combo=True,
        )
    except Exception as e:
        logger.error(f"Combo purchase creation failed: {e}")
        text = "❌ Ошибка создания покупки. Попробуйте позже."
        buttons = [[InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_combo")]]
        await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")
        return

    # 2. Deduct balance
    await database.decrease_balance(telegram_id, price, source="combo_purchase", description=f"Combo {base_tariff} {period_days}d + {gb}GB bypass")

    # 3. Finalize purchase (activates subscription, creates VPN key, etc.)
    try:
        result = await subscription_service.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider="balance",
            amount_rubles=float(price),
        )
        if not result.get("success"):
            logger.error(f"Combo finalize failed: {result}")
    except Exception as e:
        logger.error(f"Combo finalize error: {e}")

    # 4. Add bypass traffic
    from app.services import remnawave_service
    traffic_bytes = gb * 1024**3
    try:
        rmn_success = await remnawave_service.add_traffic(telegram_id, traffic_bytes)
        if not rmn_success:
            logger.warning(f"COMBO_PAY_BALANCE_TRAFFIC_FAIL user={telegram_id} gb={gb}")
    except Exception as traffic_err:
        logger.warning(f"COMBO_PAY_BALANCE_TRAFFIC_ERROR user={telegram_id}: {traffic_err}")

    # 5. Record traffic purchase + mark as combo
    await database.record_traffic_purchase(telegram_id, gb, 0)
    await database.set_combo_flag(telegram_id, True)

    months = period_days // 30
    text = i18n_get_text(language, "combo.purchase_success",
                         tariff=base_tariff.capitalize(), months=months, gb=gb)
    buttons = [
        [InlineKeyboardButton(text="📲 Подключиться", callback_data="connect_instruction")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ]
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


# ── Mini Shop ────────────────────────────────────────────────────

_APPLE_USD_RATE = 101   # RUB per 1 USD
_APPLE_TRY_RATE = 2.9   # RUB per 1 TRY

_APPLE_NOMINALS = {
    "usa": [2, 5, 10, 15, 20, 25, 50, 60, 70],
    "turkey": [100, 150, 200, 300, 500, 600],
    "russia": [500, 800, 1000, 1500, 2000, 2500, 3000],
    "india": [100, 200, 250, 500, 1000],
}
_APPLE_CURRENCIES = {"usa": "$", "turkey": "TL", "russia": "₽", "india": "INR"}
_APPLE_RATES = {"usa": _APPLE_USD_RATE, "turkey": _APPLE_TRY_RATE}
@router.callback_query(F.data == "mini_shop")
async def callback_mini_shop(callback: CallbackQuery):
    """Mini shop main screen — photo + caption."""
    try:
        await callback.answer()
    except Exception:
        pass
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "shop.title")
    # Telegram Stars временно скрыт (по запросу). Callback stars_buy и
    # обвязка i18n оставлены на месте — вернуть кнопку = раскомментировать
    # строку ниже.
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡️ Telegram Premium", callback_data="premium_buy")],
        # [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="stars_buy")],
        [InlineKeyboardButton(text="🍎 Пополнить Apple ID", callback_data="apple_region")],
        [InlineKeyboardButton(text="🎮 Пополнить Steam", callback_data="steam:disclaimer")],
        [InlineKeyboardButton(text="🎧 Spotify Premium", callback_data="spotify:start")],
        [InlineKeyboardButton(text="🧠 Claude Pro/Max (скоро)", callback_data="claude_coming_soon")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])

    # Delete the previous message (be it text or photo) and send a fresh
    # photo-with-caption screen.  _send_screen_photo degrades to a text
    # message if the file_id is unusable on the current bot.
    chat_id = callback.from_user.id
    try:
        await callback.message.delete()
    except Exception:
        pass
    from app.handlers.common.screens import _send_screen_photo, SHOP_PHOTO_FILE_ID
    await _send_screen_photo(
        callback.bot, chat_id, SHOP_PHOTO_FILE_ID, text,
        reply_markup=keyboard, parse_mode="HTML",
    )


@router.callback_query(F.data == "claude_coming_soon")
async def callback_claude_coming_soon(callback: CallbackQuery):
    """Claude Pro/Max — placeholder until launch."""
    language = await resolve_user_language(callback.from_user.id)
    await callback.answer(i18n_get_text(language, "shop.claude_coming_soon"), show_alert=True)


@router.callback_query(F.data == "menu_help")
async def callback_menu_help(callback: CallbackQuery):
    """Help menu — FAQ, instructions, direct support (photo screen)."""
    await _open_help_screen(callback, callback.bot)


@router.callback_query(F.data == "help_contacts")
async def callback_help_contacts(callback: CallbackQuery):
    """Contacts — support and sales emails (photo screen)."""
    try:
        await callback.answer()
    except Exception:
        pass
    from app.handlers.common.screens import _send_screen_photo, CONTACTS_PHOTO_FILE_ID
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "help.contacts_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_help")],
    ])
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _send_screen_photo(
        callback.bot, callback.message.chat.id, CONTACTS_PHOTO_FILE_ID, text,
        reply_markup=keyboard, parse_mode="HTML",
    )


@router.callback_query(F.data == "faq")
async def callback_faq(callback: CallbackQuery):
    """FAQ — top questions."""
    try:
        await callback.answer()
    except Exception:
        pass
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "help.faq_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, f"help.faq_q{n}"), callback_data=f"faq:{n}")]
        for n in range(1, 10)
    ] + [
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_help")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("faq:"))
async def callback_faq_answer(callback: CallbackQuery):
    """FAQ — individual answer."""
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        n = int(callback.data.split(":", 1)[1])
        if n < 1 or n > 9:
            return
    except (ValueError, IndexError):
        return
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, f"help.faq_a{n}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Помощь", url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="faq")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
