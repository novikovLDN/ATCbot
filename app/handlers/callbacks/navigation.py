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
from app.handlers.common.screens import (
    show_profile,
    _open_help_screen,
    _open_my_subscription_screen,
    _open_legal_screen,
)
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_connect_keyboard,
)
from app.handlers.common.emoji import CE
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


async def _get_main_text(telegram_id: int, language: str) -> str:
    """Определяет текст главного экрана: обычный, бизнес, bypass-only или без подписки.

    Legal footer больше не приклеивается — ссылки на политику и соглашение
    живут в отдельном экране «Правила» (Мой профиль → Правила).
    """
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
        [InlineKeyboardButton(text=i18n_get_text(language, "main.about"), callback_data="menu_about", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "main.tracker_only_btn", "✍️ Трекер Only"), url="https://t.me/ItsOnlyWbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main", icon_custom_emoji_id=CE["back"], style="primary")],
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
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main", icon_custom_emoji_id=CE["back"], style="primary")],
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
        text += i18n_get_text(language, "biz.link_ready_suffix", "\n\n🔗 Ваша ссылка подключения готова.")

    from app.handlers.common.keyboards import get_biz_control_panel_keyboard
    keyboard = get_biz_control_panel_keyboard(language)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_copy_login")
async def callback_biz_copy_login(callback: CallbackQuery):
    """📋 Скопировать логин (VPN ключ)"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        await callback.answer(i18n_get_text(language, "biz.copy_link_alert", "Скопируйте ссылку выше"))
    else:
        await callback.answer(i18n_get_text(language, "biz.no_key_alert", "Ключ не найден"), show_alert=True)


@router.callback_query(F.data == "biz_copy_password")
async def callback_biz_copy_password(callback: CallbackQuery):
    """🔑 Скопировать пароль (VPN ключ Plus)"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        await callback.answer(i18n_get_text(language, "biz.copy_link_alert", "Скопируйте ссылку выше"))
    else:
        await callback.answer(i18n_get_text(language, "biz.no_key_alert", "Ключ не найден"), show_alert=True)


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
        [InlineKeyboardButton(text=i18n_get_text(language, "main.settings_change_language_btn", "🗣 Изменить язык"), callback_data="change_language", icon_custom_emoji_id=CE["language"], style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "main.settings_privacy_btn", "🔐 Политика конфиденциальности"), callback_data="about_privacy", style="primary")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.ecosystem", "⚪️ Наша экосистема"),
            callback_data="menu_ecosystem",
            style="primary",
        )],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main", icon_custom_emoji_id=CE["back"], style="primary")],
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


@router.callback_query(F.data == "menu_my_subscription")
async def callback_my_subscription(callback: CallbackQuery):
    """Экран «Моя подписка» — краткое инфо + быстрые действия."""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    await _open_my_subscription_screen(callback, callback.bot)


@router.callback_query(F.data == "menu_legal")
async def callback_legal(callback: CallbackQuery):
    """Экран «Правила» — выбор правового документа."""
    await _open_legal_screen(callback, callback.bot)


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
            i18n_get_text(language, "errors.special_offer_expired", "⏰ Срок спецпредложения истёк. Вы можете приобрести подписку по обычной цене."),
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
        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            i18n_get_text(language, "main.discount_applied_choose_tariff", "🎁 Скидка 15% автоматически применена! Действует 7 дней.\n\nВыберите тариф:"),
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
        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            i18n_get_text(language, "main.discount_applied_choose_tariff", "🎁 Скидка 15% автоматически применена! Действует 7 дней.\n\nВыберите тариф:"),
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


# ── Connect instruction ──────────────────────────────────────────

_DEVICE_SELECT_PHOTO = {
    # prod: ОБЫЧНОЕ фото (модерация перенесена). Модерационный file_id —
    # в docs/MODERATION_VPN_BYPASS_CHANGESET.md (ставить при возобновлении).
    "prod": "AgACAgQAAxkBAAGILwVqmR0AATCd8V0czJQwFMVtbGWP97IAAncQaxsxNshQ3NkHkfgoXUwBAAMCAAN5AAM9BA",
    "stage": "AgACAgQAAxkBAAIhc2oZ_tiD1jsG8eB-9HrSgTTiyjEUAAJfD2sbEDfQUDPuD983y47VAQADAgADeQADOwQ",
}


@router.callback_query(F.data == "connect_instruction")
async def callback_connect_instruction(callback: CallbackQuery):
    """Подключиться → сразу выбор устройства."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Existing Remnawave юзер → ensure squad + продлить bypass expiry.
    # Для юзеров без remnawave_uuid НИЧЕГО тут не форсим: setup_step1
    # ниже позовёт get_user_bypass_url → _try_lazy_provision_entities,
    # который сам создаст bypass с правильным TRIAL_BYPASS_MB / 10 GB.
    # Раньше здесь были bogus overrides (2 GB для trial и 1 GB для paid),
    # которые перетирали накопленный трафик — убрано.
    if config.REMNAWAVE_ENABLED:
        from app.services import remnawave_service
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if rmn_uuid:
            remnawave_service._fire_and_forget(
                remnawave_service.extend_remnawave_for_bypass(telegram_id)
            )
            remnawave_service._fire_and_forget(
                remnawave_service.ensure_squad(telegram_id)
            )

    text = i18n_get_text(language, "setup.select_device")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iPhone / iPad", callback_data="setup_step1:ios", style="primary"),
            InlineKeyboardButton(text="🤖 Android", callback_data="setup_step1:android", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🍎 Mac", callback_data="setup_step1:macos", style="primary"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="setup_step1:windows", style="primary"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])

    # Always send photo + text for device selection
    _ds_photo = _DEVICE_SELECT_PHOTO.get("prod" if config.IS_PROD else "stage", "")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_photo(
        chat_id=telegram_id,
        photo=_ds_photo,
        caption=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ── Step 1: Install App ──────────────────────────────────────────

def _get_photo_id(key: str) -> str:
    """Get photo file_id based on environment."""
    env_key = "prod" if config.IS_PROD else "stage"
    return _SETUP_PHOTOS.get(key, {}).get(env_key, "")


@router.callback_query(F.data.startswith("setup_step1:"))
async def callback_setup_step1(callback: CallbackQuery):
    """Step 1: Install Happ app — shows photo + download buttons."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "setup.install_app")

    buttons = []

    if platform in ("ios", "macos"):
        # Incy — на первом месте (по запросу). Для iOS и macOS одна и
        # та же App Store ссылка (Apple Silicon Mac умеет ставить iOS-
        # приложения).
        incy_url = _DOWNLOAD_LINKS.get(platform, {}).get("incy")
        if incy_url:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.install_incy_btn", "📲 Скачать Incy"),
                url=incy_url,
                style="primary",
            )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.install_happ_ru"),
            url=_IOS_HAPP_LINKS["ru"],
            style="primary",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.install_happ_global"),
            url=_IOS_HAPP_LINKS["global"],
            style="primary",
        )])
    elif platform == "android":
        links = _DOWNLOAD_LINKS.get("android", {})
        if "happ" in links:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.install_happ_btn", "📲 Установить Happ"),
                url=links["happ"],
                style="primary",
            )])
        # Incy для Android — Play Market ссылка
        incy_url = links.get("incy")
        if incy_url:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.install_incy_btn", "📲 Скачать Incy"),
                url=incy_url,
                style="primary",
            )])
    elif platform == "windows":
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.download_happ_btn", "📲 Скачать Happ"),
            url="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
            style="primary",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.next_step"),
        callback_data=f"setup_step2:{platform}",
        style="success",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="connect_instruction",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Platform-specific photo (no photo for Windows)
    photo_key = {
        "ios": "install_app_ios",
        "macos": "install_app_ios",
        "android": "install_app_android",
    }.get(platform)
    photo_id = _get_photo_id(photo_key) if photo_key else ""

    try:
        await callback.message.delete()
    except Exception:
        pass

    if photo_id:
        await callback.bot.send_photo(
            chat_id=telegram_id,
            photo=photo_id,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


# ── Step 2: Install Keys ────────────────────────────────────────

@router.callback_query(F.data.startswith("setup_step2:"))
async def callback_setup_step2(callback: CallbackQuery):
    """Step 2: Copy & import VPN keys into app."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Get subscription keys
    subscription = await database.get_subscription(telegram_id)
    sub_url = ""
    bypass_url = ""
    if subscription:
        from app.services.user_subscription_links import (
            get_user_primary_subscription_url,
            get_user_bypass_url,
        )
        sub_url = await get_user_primary_subscription_url(telegram_id)
        # ⚠️ ВАЖНО: bypass URL берём через helper, который применяет
        # _rewrite_sub_host (sub.atlassecure.ru → subscription.vps-cloud.uk).
        # Раньше здесь был прямой get_user_traffic — Happ получал raw
        # panel URL с sub.atlassecure.ru и падал в "сертификат недействителен",
        # т.к. cert на этом хосте отсутствует/невалиден.
        if config.REMNAWAVE_ENABLED:
            bypass_url = await get_user_bypass_url(telegram_id) or ""

    # ── Aggregator-branch (пока admin-only, потом флип на всех) ────
    # Если юзер под гейтом sub_aggregator — отдаём ЕДИНЫЙ ключ (склеенная
    # подписка) с двумя кнопками Happ/Incy. Никаких "VPN vs Обход" — теперь
    # это одна ссылка, комбинирующая оба типа серверов внутри.
    from app.services import sub_aggregator
    agg_url = None
    if sub_aggregator.is_enabled_for(telegram_id):
        try:
            agg_url = await sub_aggregator.ensure_pair(telegram_id)
        except Exception as e:
            logger.warning("SETUP_STEP2 aggregator ensure_pair failed tg=%s: %s", telegram_id, e)

    if agg_url:
        from urllib.parse import quote, urlparse
        if config.PUBLIC_BASE_URL:
            base_url = config.PUBLIC_BASE_URL
        else:
            parsed = urlparse(config.WEBHOOK_URL)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        q = quote(agg_url, safe='')
        show_incy = platform in ("ios", "android", "macos")

        text = i18n_get_text(language, "setup.key_install_title_agg")
        buttons = [[InlineKeyboardButton(
            text=i18n_get_text(language, "setup.btn_add_happ", "📥 Добавить ключ в Happ"),
            url=f"{base_url}/open/happ?url={q}",
            style="primary",
        )]]
        if show_incy:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.btn_add_incy", "💚 Добавить ключ в Incy"),
                url=f"{base_url}/open/incy?url={q}",
                style="success",
            )])
        # V2RayTun — только iOS/Android (десктоп-схема нестабильна).
        if platform in ("ios", "android"):
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.btn_add_v2raytun", "🚀 Добавить ключ в V2RayTun"),
                url=f"{base_url}/open/v2raytun?url={q}",
                style="primary",
            )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.btn_done"),
            callback_data="setup_done",
            style="danger",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.btn_manual_setup", "⚙️ Настроить вручную"),
            callback_data=f"setup_manual:{platform}",
            style="primary",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.btn_need_help"),
            url="https://t.me/atlas_suppbot",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_step1:{platform}",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        # Отправляем фото + подпись (как legacy-экран), а не edit-text.
        photo_id = _get_photo_id("install_keys_agg")
        try:
            await callback.message.delete()
        except Exception:
            pass
        if photo_id:
            await callback.bot.send_photo(
                chat_id=telegram_id, photo=photo_id, caption=text,
                reply_markup=keyboard, parse_mode="HTML",
            )
        else:
            await callback.bot.send_message(
                chat_id=telegram_id, text=text,
                reply_markup=keyboard, parse_mode="HTML",
            )
        return

    text = i18n_get_text(language, "setup.key_install_title")

    buttons = []

    # === Auto-setup deeplinks (legacy dual-key) ===
    # Layout:
    #   [Happ VPN]       [Incy VPN]       — primary | success
    #   [Happ Обход]     [Incy Обход]     — primary | success
    #   [Готово]                           — danger
    #   [Установить вручную]               — primary
    #   [Нужна помощь]
    #   [Назад]
    # Incy-кнопки — на iOS/Android/macOS. Windows не показываем: у Incy
    # нет Windows-клиента, deeplink incy://crypt1/... там не откроется.
    # Bypass-only юзер (только трафик обхода, без основной подписки) —
    # sub_url пуст, показываем только ряд «Обход». Раньше `if sub_url`
    # прятал ОБА ряда → bypass-only оставался без единой кнопки-ключа.
    if sub_url or bypass_url:
        from urllib.parse import quote, urlparse
        if config.PUBLIC_BASE_URL:
            base_url = config.PUBLIC_BASE_URL
        else:
            parsed = urlparse(config.WEBHOOK_URL)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        show_incy = platform in ("ios", "android", "macos")

        # Ряд 1: VPN-ключ (Happ + Incy) — только если есть основная подписка.
        if sub_url:
            row_vpn = [InlineKeyboardButton(
                text=i18n_get_text(language, "setup.happ_vpn_label", "Happ VPN"),
                url=f"{base_url}/open/happ?url={quote(sub_url, safe='')}",
                style="primary",
            )]
            if show_incy:
                row_vpn.append(InlineKeyboardButton(
                    text=i18n_get_text(language, "setup.incy_vpn_label", "Incy VPN"),
                    url=f"{base_url}/open/incy?url={quote(sub_url, safe='')}",
                    style="success",
                ))
            buttons.append(row_vpn)

        # Ряд 2: Обход (Happ + Incy) — только если есть bypass_url
        if bypass_url:
            row_bypass = [InlineKeyboardButton(
                text=i18n_get_text(language, "setup.happ_bypass_label", "Happ Обход"),
                url=f"{base_url}/open/happ?url={quote(bypass_url, safe='')}",
                style="primary",
            )]
            if show_incy:
                row_bypass.append(InlineKeyboardButton(
                    text=i18n_get_text(language, "setup.incy_bypass_label", "Incy Обход"),
                    url=f"{base_url}/open/incy?url={quote(bypass_url, safe='')}",
                    style="success",
                ))
            buttons.append(row_bypass)

    # === Bottom buttons ===
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.btn_done"),
        callback_data="setup_done",
        style="danger",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.btn_manual"),
        callback_data=f"setup_manual:{platform}",
        style="primary",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.btn_need_help"),
        url="https://t.me/atlas_suppbot",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"setup_step1:{platform}",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Send photo + text
    photo_id = _get_photo_id("install_keys")
    try:
        await callback.message.delete()
    except Exception:
        pass

    if photo_id:
        await callback.bot.send_photo(
            chat_id=telegram_id,
            photo=photo_id,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


# ── Device setup flow (legacy + auto-setup) ──────────────────────

# Photo file IDs for setup screens
_SETUP_PHOTOS = {
    "install_app_ios": {
        "prod": "AgACAgQAAxkBAAEsTydp2K_IyYzWcQLdTzcx8R69LXkQPgAC6wxrG6gtyVKbKj2nQnrQggEAAwIAA3kAAzsE",
        "stage": "AgACAgQAAxkBAAIelmnYsCB_mV2UUCsZQxtCAUv6HfJkAALrDGsbqC3JUsb1k8gTRdgCAQADAgADeQADOwQ",
    },
    "install_app_android": {
        "prod": "AgACAgQAAxkBAAEsVZ9p2WKsEhB1jDTAYdA3TXJdqENHcAACzwxrG9Np0VKr7b7MS293SQEAAwIAA3cAAzsE",
        "stage": "AgACAgQAAxkBAAIeyGnZYtm7bZWgWSbQzaPQK9jDFIjxAALPDGsb02nRUmA2_j7leNc1AQADAgADdwADOwQ",
    },
    "install_keys": {
        "prod": "AgACAgQAAxkBAAEsTzVp2LGqLrhvY1TRSdQdmp_vmS_tEwAC7AxrG6gtyVLmvPzPSqNEwAEAAwIAA3cAAzsE",
        "stage": "AgACAgQAAxkBAAIeumnZWPxaNMkJApJ3JerkNYLX_kJbAALsDGsbqC3JUlRy7JVisnaVAQADAgADdwADOwQ",
    },
    # Экран единого ключа (aggregator-ветка). Aggregator admin-only на
    # проде → важен prod file_id; stage пусто → упадёт на текст без фото.
    "install_keys_agg": {
        "prod": "AgACAgQAAxkBAAGEhSxqiUQ8DQABFD7v3AABNHucV2UyK_njAAIXEGsbmPNJUM160ezW1tu9AQADAgADdwADPQQ",
        "stage": "",
    },
}

_IOS_HAPP_LINKS = {
    # 2026-XX: старая ссылка happ-proxy-utility/id6783623643 перестала
    # быть актуальной — App Store переехал на «Happ Proxy Utility Plus»
    # id6788279553. Единая точка правды для iOS-инсталляции и всех
    # broadcast-кнопок «Happ iOS».
    "ru": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6788279553?l=en-GB",
    "global": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
}

_INCY_IOS_URL = "https://apps.apple.com/ru/app/incy/id6756943388?l=en-GB"
_INCY_ANDROID_URL = "https://play.google.com/store/apps/details?id=llc.itdev.incy&hl=en_IE"

_DOWNLOAD_LINKS = {
    # 2026-06-08: V2RayTun снят со всех платформ, Hiddify тоже снят.
    # 2026-07-07: Incy добавлен для Android и macOS — раньше был
    # только iOS. macOS использует ту же App Store ссылку что и iOS
    # (Mac с Apple Silicon умеет ставить iOS-приложения из App Store).
    "ios": {
        "happ": _IOS_HAPP_LINKS["ru"],
        "incy": _INCY_IOS_URL,
    },
    "android": {
        "happ": "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
        "incy": _INCY_ANDROID_URL,
    },
    "macos": {
        # macOS ставит iOS-приложение Incy через App Store — та же ссылка.
        "happ": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
        "incy": _INCY_IOS_URL,
    },
    "windows": {
        "happ": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
    },
}

# Допускаем override через env — чтобы не пересобирать образ ради смены
# App Store ссылки.
_incy_ios_env = os.getenv("INCY_IOS_APP_URL")
if _incy_ios_env:
    _DOWNLOAD_LINKS["ios"]["incy"] = _incy_ios_env
    _DOWNLOAD_LINKS["macos"]["incy"] = _incy_ios_env
_incy_android_env = os.getenv("INCY_ANDROID_APP_URL")
if _incy_android_env:
    _DOWNLOAD_LINKS["android"]["incy"] = _incy_android_env


@router.callback_query(F.data == "setup_device")
async def callback_setup_device(callback: CallbackQuery):
    """Выбор устройства для настройки."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "setup.select_device")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iPhone / iPad", callback_data="setup_step1:ios", style="primary"),
            InlineKeyboardButton(text="🤖 Android", callback_data="setup_step1:android", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🍎 Mac", callback_data="setup_step1:macos", style="primary"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="setup_step1:windows", style="primary"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])

    has_photo = getattr(callback.message, "photo", None) and len(callback.message.photo) > 0
    if has_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_platform:"))
async def callback_setup_platform(callback: CallbackQuery):
    """Единый экран: скачать приложение + авто-настройка с кнопками."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Get subscription and keys
    subscription = await database.get_subscription(telegram_id)
    sub_url = None
    bypass_url = None
    if subscription:
        from app.services.user_subscription_links import get_user_primary_subscription_url
        sub_url = await get_user_primary_subscription_url(telegram_id)

    # Bypass key: available independently of main subscription.
    # Goes through the helper so cache misses + missing entities
    # auto-recover (lazy-provision creates the bypass entity if the
    # user has an active subscription but no remnawave_uuid yet).
    if config.REMNAWAVE_ENABLED:
        from app.services.user_subscription_links import get_user_bypass_url
        bypass_url = await get_user_bypass_url(telegram_id)

    # Build text
    text = i18n_get_text(language, f"setup.combined_{platform}")

    buttons = []

    # === Download links FIRST ===
    links = _DOWNLOAD_LINKS.get(platform, {})
    if platform in ("ios", "android", "macos"):
        # Happ — отдельная строка
        if "happ" in links:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.download_happ"),
                url=links["happ"],
            )])
        # Incy — отдельной строкой (iOS / Android / macOS).
        if "incy" in links:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.install_incy_btn", "📲 Скачать Incy"),
                url=links["incy"],
            )])
    else:
        # Windows: download buttons in pairs
        download_row = []
        for client, url in links.items():
            label = i18n_get_text(language, f"setup.download_{client}")
            download_row.append(InlineKeyboardButton(text=label, url=url))
            if len(download_row) == 2:
                buttons.append(download_row)
                download_row = []
        if download_row:
            buttons.append(download_row)

    # === Auto-setup buttons (if user has subscription) ===
    if sub_url:
        from urllib.parse import quote, urlparse
        if config.PUBLIC_BASE_URL:
            base_url = config.PUBLIC_BASE_URL
        else:
            parsed = urlparse(config.WEBHOOK_URL)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Incy на iOS показывается только если Node-сайдкар жив. Сразу
        # пробрасываем флаг сюда, чтобы не плодить нерабочих кнопок.
        from app.services import incy_crypto
        ios_clients = ["happ"]
        if incy_crypto.is_available():
            ios_clients.append("incy")

        _platform_clients = {
            "ios": ios_clients,
            "android": ["happ"],
            "macos": ["happ"],
            "windows": ["happ"],
        }
        _client_deeplink = {
            "happ": "happ",
            "incy": "incy",
        }
        _client_names = {
            "happ": "Happ",
            "incy": "Incy",
        }

        # Decorative separator
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.key_hint_press", "Установка ключа в одно нажатие 👇"),
            callback_data="noop",
            style="primary",
        )])

        clients = _platform_clients.get(platform, [])
        for client in clients:
            dl = _client_deeplink[client]
            name = _client_names[client]
            row = [InlineKeyboardButton(
                text=f"\U0001f310 {name}",
                url=f"{base_url}/open/{dl}?url={quote(sub_url, safe='')}",
            )]
            if bypass_url:
                row.append(InlineKeyboardButton(
                    text=f"\U0001f90d {name}",
                    url=f"{base_url}/open/{dl}?url={quote(bypass_url, safe='')}",
                ))
            buttons.append(row)

    # Manual setup + QR
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "setup.manual_button"),
            callback_data=f"setup_manual:{platform}",
            style="primary",
        ),
        InlineKeyboardButton(
            text=i18n_get_text(language, "setup.qr_button"),
            callback_data=f"setup_qr:{platform}",
            style="primary",
        ),
    ])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.done_button"),
        callback_data="setup_done",
        style="primary",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="connect_instruction",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_manual:"))
async def callback_setup_manual(callback: CallbackQuery):
    """Экран подробной инструкции по ручной настройке (стандарт + обход)."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # ── Aggregator-branch: один ключ (склеенная подписка) → 2 encrypted ссылки
    from app.services import sub_aggregator
    agg_url = None
    if sub_aggregator.is_enabled_for(telegram_id):
        try:
            agg_url = await sub_aggregator.ensure_pair(telegram_id)
        except Exception as e:
            logger.warning("SETUP_MANUAL aggregator ensure_pair failed tg=%s: %s", telegram_id, e)

    from app.services import happ_crypto, incy_crypto
    connect_text = i18n_get_text(language, f"setup.connect_{platform}")

    if agg_url:
        # 1 ссылка агрегатора → 2 encrypted формы (Happ + Incy).
        # Никаких "VPN vs Обход" — теперь всё в одном ключе.
        happ_link = happ_crypto.format_for_user(agg_url)
        keys_section = (
            "\n" + i18n_get_text(language, "setup.key_happ_label", "🔑 <b>Ключ Happ</b>:") + "\n"
            f"<blockquote expandable><code>{happ_link}</code></blockquote>"
        )
        if platform in ("ios", "android", "macos"):
            try:
                incy_link = await incy_crypto.to_incy_link(agg_url)
                if incy_link:
                    keys_section += (
                        "\n" + i18n_get_text(language, "setup.key_incy_label", "💚 <b>Ключ Incy</b>:") + "\n"
                        f"<blockquote expandable><code>{incy_link}</code></blockquote>"
                    )
            except Exception:
                logger.exception("SETUP_MANUAL incy_crypto failed for agg_url")
        text = f"{connect_text}\n{keys_section}"
        # Внизу — альтернативный ключ: сырая ссылка подписки (без шифрования),
        # подходит для любого клиента (V2Box / Incy / Happ и др.).
        if agg_url:
            text += (
                "\n\n" + i18n_get_text(language, "setup.manual_alt_key_hint")
                + f"\n<blockquote expandable><code>{agg_url}</code></blockquote>"
            )

        buttons = [
            [InlineKeyboardButton(
                text=i18n_get_text(language, "setup.done_button"),
                callback_data="setup_done",
                style="primary",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=f"setup_step2:{platform}",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
        return

    subscription = await database.get_subscription(telegram_id)
    sub_url = None
    bypass_url = None
    if subscription:
        from app.services.user_subscription_links import get_user_primary_subscription_url
        sub_url = await get_user_primary_subscription_url(telegram_id)

    # Bypass key: available independently of main subscription.
    # Goes through the helper so cache misses + missing entities
    # auto-recover (lazy-provision creates the bypass entity if the
    # user has an active subscription but no remnawave_uuid yet).
    if config.REMNAWAVE_ENABLED:
        from app.services.user_subscription_links import get_user_bypass_url
        bypass_url = await get_user_bypass_url(telegram_id)

    # Build keys section (legacy dual-key).
    # — Happ-ключи (sealed crypt4) для всех платформ;
    # — Incy-ключи (crypt1) для iOS/Android/macOS. Windows не показываем:
    #   Incy-клиента под Windows нет, incy://crypt1/... deep-link
    #   там некому обрабатывать.
    # Все ключи в свёрнутой цитате (blockquote expandable) — экран
    # компактный по умолчанию, юзер раскрывает только нужный ключ.
    def _happ_key_block(label_key: str, raw_url: str) -> str:
        happ_link = happ_crypto.format_for_user(raw_url)
        return (
            "\n" + i18n_get_text(language, label_key) + "\n"
            f"<blockquote expandable><code>{happ_link}</code></blockquote>"
        )

    async def _incy_key_block(label_key: str, raw_url: str) -> str:
        incy_link = await incy_crypto.to_incy_link(raw_url)
        # Если sidecar не вернул даже fallback — пропускаем блок.
        if not incy_link:
            return ""
        return (
            "\n" + i18n_get_text(language, label_key) + "\n"
            f"<blockquote expandable><code>{incy_link}</code></blockquote>"
        )

    keys_section = ""
    if sub_url:
        keys_section += _happ_key_block("setup.key_vpn_label", sub_url)
    if bypass_url:
        keys_section += _happ_key_block("setup.key_bypass_label", bypass_url)
    if platform in ("ios", "android", "macos"):
        if sub_url:
            keys_section += await _incy_key_block("setup.key_vpn_incy_label", sub_url)
        if bypass_url:
            keys_section += await _incy_key_block("setup.key_bypass_incy_label", bypass_url)

    if keys_section:
        text = f"{connect_text}\n{keys_section}"
    else:
        text = connect_text

    # Внизу — альтернативный ключ: сырая ссылка подписки без шифрования,
    # подходит для любого клиента (V2Box / Incy / Happ и др.).
    _alt_raw = sub_url or bypass_url
    if _alt_raw:
        text += (
            "\n\n" + i18n_get_text(language, "setup.manual_alt_key_hint")
            + f"\n<blockquote expandable><code>{_alt_raw}</code></blockquote>"
        )

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_step2:{platform}",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data == "setup_done")
async def callback_setup_done(callback: CallbackQuery, state: FSMContext):
    """Готово — отправить 🎉 и через 2 сек показать главный экран."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    # 1. Удаляем старый экран (инструкции)
    try:
        await callback.message.delete()
    except Exception:
        pass

    # 2. Отправляем 🎉
    msg = await callback.bot.send_message(
        chat_id=telegram_id,
        # parse_mode=HTML обязателен — иначе Telegram отдаст
        # текст вместо premium-эмодзи. Fallback внутри тега
        # (⚡️) увидят non-premium юзеры и старые клиенты.
        text='<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>',
        parse_mode="HTML",
    )

    # 3. Ждём 2 секунды
    await asyncio.sleep(2)

    # 4. Удаляем 🎉
    try:
        await msg.delete()
    except Exception:
        pass

    # 5. Отправляем главное меню с фото
    language = await resolve_user_language(telegram_id)
    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)

    await callback.bot.send_photo(
        chat_id=telegram_id,
        photo=_MAIN_PHOTO_ID,
        caption=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("setup_qr:"))
async def callback_setup_qr(callback: CallbackQuery):
    """Экран выбора: QR обычных серверов или обхода белых списков."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "get_key.no_subscription")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )]])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        return

    # Check if bypass is available
    has_bypass = False
    sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
    if config.REMNAWAVE_ENABLED and sub_type in ("basic", "plus"):
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if rmn_uuid:
            traffic = await remnawave_api.get_user_traffic(rmn_uuid)
            if traffic and traffic.get("subscriptionUrl"):
                has_bypass = True

    text = i18n_get_text(language, "setup.qr_choose_type")

    buttons = [
        [InlineKeyboardButton(
            text="🌐 " + i18n_get_text(language, "setup.qr_standard_btn"),
            callback_data=f"setup_qr_standard:{platform}",
            style="primary",
        )],
    ]
    if has_bypass:
        buttons.append([InlineKeyboardButton(
            text="🤍 " + i18n_get_text(language, "setup.qr_bypass_btn"),
            callback_data=f"setup_qr_bypass:{platform}",
            style="primary",
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"setup_platform:{platform}",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_qr_standard:"))
async def callback_setup_qr_standard(callback: CallbackQuery):
    """Выбор приложения (Happ / Incy) для обычных серверов."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    await _show_qr_app_choice(callback, platform, "standard", language)


@router.callback_query(F.data.startswith("setup_qr_bypass:"))
async def callback_setup_qr_bypass(callback: CallbackQuery):
    """Выбор приложения (Happ / Incy) для обхода белых списков."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    await _show_qr_app_choice(callback, platform, "bypass", language)


async def _show_qr_app_choice(callback: CallbackQuery, platform: str, kind: str, language: str):
    """Экран «Выберите приложение» — Incy / Happ.

    kind: 'standard' (обычные сервера) либо 'bypass' (обход).
    Кнопки ведут на единый хендлер setup_qr_app:{client}:{kind}:{platform},
    который уже забирает URL подписки и рендерит QR через _send_qr_screen."""
    text = i18n_get_text(language, "setup.qr_choose_app")

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.qr_app_btn_incy"),
            callback_data=f"setup_qr_app:incy:{kind}:{platform}",
            style="success",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.qr_app_btn_happ"),
            callback_data=f"setup_qr_app:happ:{kind}:{platform}",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_qr:{platform}",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_qr_app:"))
async def callback_setup_qr_app(callback: CallbackQuery):
    """QR-код подписки для выбранного приложения (Happ / Incy).

    Формат callback'а: setup_qr_app:{client}:{kind}:{platform}
      client  — 'happ' | 'incy'
      kind    — 'standard' (обычные сервера) | 'bypass' (обход)
      platform — ios/android/macos/windows (для back-навигации)
    """
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    _, client, kind, platform = parts
    if client not in ("happ", "incy") or kind not in ("standard", "bypass"):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    if kind == "standard":
        subscription = await database.get_subscription(telegram_id)
        if subscription:
            from app.services.user_subscription_links import get_user_primary_subscription_url
            url = await get_user_primary_subscription_url(telegram_id)
        else:
            url = None

        if not url:
            text = i18n_get_text(language, "get_key.no_subscription")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=f"setup_qr_standard:{platform}",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]])
            await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
            return

        label_key = "setup.key_vpn_incy_label" if client == "incy" else "setup.key_vpn_label"
    else:
        url = None
        if config.REMNAWAVE_ENABLED:
            from app.services import remnawave_api
            rmn_uuid = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid:
                traffic = await remnawave_api.get_user_traffic(rmn_uuid)
                if traffic:
                    url = traffic.get("subscriptionUrl", "") or None

        if not url:
            text = i18n_get_text(language, "setup.qr_bypass_unavailable")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=f"setup_qr_bypass:{platform}",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]])
            await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
            return

        label_key = "setup.key_bypass_incy_label" if client == "incy" else "setup.key_bypass_label"

    await _send_qr_screen(
        callback, platform, url, language,
        label_key=label_key, client=client, kind=kind,
    )


async def _send_qr_screen(
    callback: CallbackQuery,
    platform: str,
    url: str,
    language: str,
    label_key: str,
    client: str = "happ",
    kind: str = "standard",
):
    """Генерация QR-кода и отправка экрана с инструкцией.

    Happ → `happ://crypt4/<base64>` (pure-Python RSA-4096 sealing).
    Incy → `incy://crypt1/<payload>` (AES-256-GCM через Node sidecar;
    при недоступности sidecar'а incy_crypto само деградирует до
    `incy://add/<plain_url>` — экран всё равно живой)."""
    telegram_id = callback.from_user.id

    if client == "incy":
        # incy_crypto.to_incy_link сам кэширует, делает graceful
        # fallback и ловит исключения от sidecar'а. На совсем редкий
        # случай (например, наша обёртка кинула TypeError) — ловим
        # тут, чтобы экран всё равно отрендерился с сырой ссылкой.
        from app.services import incy_crypto
        try:
            wrapped = await incy_crypto.to_incy_link(url)
        except Exception:
            wrapped = None
        crypt_url = wrapped or url
        instruction_key = "setup.qr_instruction_incy"
    else:
        # format_for_user сам падает в raw URL при ошибке шифрования,
        # экран всегда отдаст рабочую ссылку.
        from app.services import happ_crypto
        crypt_url = happ_crypto.format_for_user(url) or url
        instruction_key = "setup.qr_instruction"

    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(crypt_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qr_text = i18n_get_text(language, instruction_key)
    # <blockquote expandable> сворачивает длинную (~700 char) ссылку
    # до одной строки с «Show more» — тап по <code> копирует.
    qr_text += (
        "\n\n" + i18n_get_text(language, label_key) + "\n"
        f"<blockquote expandable><code>{crypt_url}</code></blockquote>"
    )

    # Back → возвращаемся на экран выбора приложения (тот же handler
    # setup_qr_standard/bypass, что теперь рендерит app-picker).
    back_cb = f"setup_qr_{kind}:{platform}"

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=back_cb,
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.bot.send_photo(
        chat_id=telegram_id,
        photo=BufferedInputFile(buf.read(), filename="subscription_qr.png"),
        caption=qr_text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


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
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "combo.select_plus"),
            callback_data="combo_tariff:combo_plus",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id=CE["back"],
            style="primary",
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
        text += i18n_get_text(language, "combo.promo_period_prompt", "\n\n🎁 Промокод: скидка {discount_pct}%\nВыберите период:", discount_pct=discount_pct)
    else:
        text += i18n_get_text(language, "combo.choose_period_prompt", "\n\nВыберите период:")

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
            style="primary",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_combo",
        icon_custom_emoji_id=CE["back"],
        style="primary",
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


# ── Mini Shop ────────────────────────────────────────────────────

_APPLE_USD_RATE = 101   # RUB per 1 USD
_APPLE_TRY_RATE = 2.9   # RUB per 1 TRY

_APPLE_NOMINALS = {
    "usa": [2, 5, 10, 15, 20, 25, 50, 60, 70],
    "turkey": [100, 200, 300, 500, 600],
    "russia": [500, 800, 1000, 1500, 2000, 2500, 3000],
    "india": [100, 200, 250, 500, 1000],
}
_APPLE_CURRENCIES = {"usa": "$", "turkey": "TL", "russia": "₽", "india": "INR"}
_APPLE_RATES = {"usa": _APPLE_USD_RATE, "turkey": _APPLE_TRY_RATE}
_APPLE_REGIONS = {
    "usa": "🇺🇸 USA",
    "turkey": "🇹🇷 Turkey",
    "russia": "🇷🇺 Russia",
    "india": "🇮🇳 India",
}

# Явные price-точки для регионов, где нет линейного rate-конвертирования.
# Ключ — nominal региона, значение — цена в рублях к оплате.
# usa/turkey тоже держим тут (а не через rate), чтобы цены оставались
# ЦЕЛЫМИ рублями после наценки — иначе в оплате (price_kopecks) полезли бы
# копейки. usa = базовая (nominal×101) ×1.15; turkey = (nominal×2.9) ×1.20.
_APPLE_PRICES_EXPLICIT: dict[str, dict[int, int]] = {
    "usa": {
        2: 232, 5: 581, 10: 1162, 15: 1742, 20: 2323,
        25: 2904, 50: 5808, 60: 6969, 70: 8131,
    },
    "turkey": {
        100: 348, 200: 696, 300: 1044, 500: 1740, 600: 2088,
    },
    "russia": {
        500: 1400, 800: 2200, 1000: 2600, 1500: 3900,
        2000: 5200, 2500: 6400, 3000: 7700,
    },
    "india": {
        100: 149, 200: 249, 250: 299, 500: 599, 1000: 1099,
    },
}


def _apple_price_rub(region: str, nominal: int) -> float:
    """RUB-цена номинала для региона. Explicit-таблица приоритетнее rate."""
    table = _APPLE_PRICES_EXPLICIT.get(region)
    if table and nominal in table:
        return float(table[nominal])
    rate = _APPLE_RATES.get(region, 93)
    return round(nominal * rate, 2)


def _apple_nominal_label(region: str, nominal: int) -> str:
    """5$ / 500 TL / 500₽ / 100 INR — как показать номинал юзеру."""
    cur = _APPLE_CURRENCIES.get(region, "$")
    if cur == "$":
        return f"{nominal}$"
    if cur == "₽":
        return f"{nominal}₽"
    return f"{nominal} {cur}"


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
        [InlineKeyboardButton(text=i18n_get_text(language, "shop.premium_button", "⚡️ Telegram Premium"), callback_data="premium_buy", style="primary")],
        # [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="stars_buy")],
        [InlineKeyboardButton(text=i18n_get_text(language, "shop.apple_id_button", "🍎 Пополнить Apple ID"), callback_data="apple_region", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "shop.steam_top_up_button", "🎮 Пополнить Steam"), callback_data="steam:disclaimer", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "shop.spotify_button", "🎧 Spotify Premium"), callback_data="spotify:start", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "shop.claude_coming_soon_button", "🧠 Claude Pro/Max (скоро)"), callback_data="claude_coming_soon", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main", icon_custom_emoji_id=CE["back"], style="primary")],
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
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_help", icon_custom_emoji_id=CE["back"], style="primary")],
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
        [InlineKeyboardButton(text=i18n_get_text(language, f"help.faq_q{n}"), callback_data=f"faq:{n}", style="primary")]
        for n in range(1, 10)
    ] + [
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_help", icon_custom_emoji_id=CE["back"], style="primary")],
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
        [InlineKeyboardButton(text=i18n_get_text(language, "common.help_button", "💬 Помощь"), url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="faq", icon_custom_emoji_id=CE["back"], style="primary")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data == "apple_region")
async def callback_apple_region(callback: CallbackQuery):
    """Apple ID — region selection."""
    try:
        await callback.answer()
    except Exception:
        pass
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "shop.apple_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇸 USA", callback_data="apple_amount:usa", style="primary")],
        [InlineKeyboardButton(text="🇹🇷 Turkey", callback_data="apple_amount:turkey", style="primary")],
        [InlineKeyboardButton(text="🇷🇺 Russia", callback_data="apple_amount:russia", style="primary")],
        [InlineKeyboardButton(text="🇮🇳 India", callback_data="apple_amount:india", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop", icon_custom_emoji_id=CE["back"], style="primary")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("apple_amount:"))
async def callback_apple_amount(callback: CallbackQuery):
    """Apple ID — nominal selection."""
    try:
        await callback.answer()
    except Exception:
        pass
    region = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    nominals = _APPLE_NOMINALS.get(region, [])
    region_label = _APPLE_REGIONS.get(region, region)

    text = i18n_get_text(language, "shop.apple_amount_title", region=region_label)

    buttons = []
    row = []
    for nom in nominals:
        price_rub = round(_apple_price_rub(region, nom))
        row.append(InlineKeyboardButton(
            text=f"{_apple_nominal_label(region, nom)} — {price_rub}₽",
            callback_data=f"apple_confirm:{region}:{nom}",
            style="primary",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"), callback_data="apple_region",
        icon_custom_emoji_id=CE["back"], style="primary",
    )])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        bot=callback.bot, parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("apple_confirm:"))
async def callback_apple_confirm(callback: CallbackQuery):
    """Apple ID — confirmation screen with payment options."""
    try:
        await callback.answer()
    except Exception:
        pass
    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Валидируем номинал против актуального списка региона: снятые с продажи
    # номиналы (напр. Turkey 150 TL) не должны покупаться даже crafted-callback'ом.
    if nominal not in _APPLE_NOMINALS.get(region, []):
        await callback.answer("Этот номинал недоступен.", show_alert=True)
        return

    region_label = _APPLE_REGIONS.get(region, region)
    price_rub = _apple_price_rub(region, nominal)
    nominal_str = _apple_nominal_label(region, nominal)

    text = i18n_get_text(language, "shop.apple_confirm",
                         region=region_label, nominal=nominal_str, price=price_rub)

    # СБП в shop-магазине (Apple ID) оставляем Platega. Lava-кнопка
    # подменена на Wata (apple_pay_lava → apple_pay_wata) — код Lava
    # не удаляем, только UI-роутинг.
    buttons = [
        [InlineKeyboardButton(text=i18n_get_text(language, "payment.card_pl_pay_button", "💳 Оплатить картой"), callback_data=f"apple_pay_card:{region}:{nominal}", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "payment.lava_pay_button", "📱 Оплатить по СБП") + " 3%", callback_data=f"apple_pay_wata:{region}:{nominal}", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "payment.sbp_pay_button", "🏦 Оплатить через СБП"), callback_data=f"apple_pay_sbp:{region}:{nominal}", style="primary")],
    ]
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"), callback_data=f"apple_amount:{region}",
        icon_custom_emoji_id=CE["back"], style="primary",
    )])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        bot=callback.bot, parse_mode="HTML",
    )


# ── Apple ID Payment Handlers ────────────────────────────────────

@router.callback_query(F.data.startswith("apple_pay_lava:"))
async def callback_apple_pay_lava(callback: CallbackQuery):
    """Apple ID — pay via Lava (card)."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    price_rub = _apple_price_rub(region, nominal)

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "errors.card_payment_unavailable", "Оплата картой временно недоступна"), show_alert=True)
        return

    region_label = _APPLE_REGIONS.get(region, region)
    nominal_label = _apple_nominal_label(region, nominal)

    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=f"apple_id_{region}_{nominal}",
        period_days=0,
        price_kopecks=round(price_rub * 100),
        purchase_type="apple_id",
    )

    invoice_data = await lava_service.create_invoice(
        amount_rubles=price_rub,
        purchase_id=purchase_id,
        comment=f"Apple ID {region_label} {nominal_label}",
    )

    payment_url = invoice_data["payment_url"]

    try:
        await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_data["invoice_id"]))
    except Exception:
        pass

    text = i18n_get_text(language, "payment.lava_waiting", amount=price_rub)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "payment.lava_pay_button"), url=payment_url)],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop", icon_custom_emoji_id=CE["back"], style="primary")],
    ])

    lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    async def _del(bot, cid, msg):
        try:
            await asyncio.sleep(15 * 60)
            await bot.delete_message(chat_id=cid, message_id=msg.message_id)
        except Exception:
            pass
    asyncio.create_task(_del(callback.bot, telegram_id, lava_msg))


@router.callback_query(F.data.startswith("apple_pay_wata:"))
async def callback_apple_pay_wata(callback: CallbackQuery):
    """Apple ID — pay via Wata (admin-only beta)."""
    try:
        await callback.answer()
    except Exception:
        pass
    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id

    import wata_service
    if not wata_service.is_visible_to(telegram_id):
        await callback.answer("Wata пока в закрытой бете", show_alert=True)
        return

    price_rub = _apple_price_rub(region, nominal)
    region_label = _APPLE_REGIONS.get(region, region)
    nominal_label = _apple_nominal_label(region, nominal)

    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=f"apple_id_{region}_{nominal}",
        period_days=0,
        price_kopecks=round(price_rub * 100),
        purchase_type="apple_id",
    )
    try:
        invoice = await wata_service.create_invoice(
            amount_rubles=price_rub,
            purchase_id=purchase_id,
            comment=f"Apple ID {region_label} {nominal_label}",
            user_id=telegram_id,
        )
    except Exception as e:
        logger.exception("APPLE_WATA_INVOICE_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания платежа Wata", show_alert=True)
        return
    try:
        await database.update_pending_purchase_invoice_id(purchase_id, str(invoice["invoice_id"]))
    except Exception:
        pass

    text = f"💳 <b>Оплата через СБП 2</b>\n\nApple ID {region_label} · {nominal_label}\nК оплате: <b>{price_rub:.2f} ₽</b>"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {price_rub:.0f} ₽", url=invoice["payment_url"])],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="mini_shop", icon_custom_emoji_id=CE["back"], style="primary")],
    ])
    msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    async def _del(bot, cid, m):
        try:
            await asyncio.sleep(15 * 60)
            await bot.delete_message(chat_id=cid, message_id=m.message_id)
        except Exception:
            pass
    asyncio.create_task(_del(callback.bot, telegram_id, msg))


async def send_apple_id_success(bot, telegram_id: int, region: str, nominal: int, price_rub: float):
    """Send user confirmation + admin notification for Apple ID purchase."""
    from datetime import datetime, timezone

    language = await resolve_user_language(telegram_id)
    region_label = _APPLE_REGIONS.get(region, region)
    nominal_str = _apple_nominal_label(region, nominal)
    price_str = f"{price_rub:.2f}"

    # User notification
    text = i18n_get_text(
        language, "shop.apple_success",
        region=region_label, nominal=nominal_str, price=price_str,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.support_short", "💬 Поддержка"), url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back_arrow", "🔙 Назад"), callback_data="menu_main", icon_custom_emoji_id=CE["back"], style="primary")],
    ])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error("APPLE_SUCCESS_MSG_FAILED user=%s error=%s", telegram_id, e)

    # Admin notification with chat button
    user = await database.get_user(telegram_id)
    buyer_username = f"@{user['username']}" if user and user.get("username") else "—"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    admin_text = i18n_get_text(
        "ru", "shop.apple_admin",
        buyer_id=telegram_id, buyer_username=buyer_username,
        region=region_label, nominal=nominal_str,
        price=price_str, date=now_str,
    )
    try:
        from app.handlers.admin.apple_id_delivery import build_apple_admin_keyboard
        admin_kb = build_apple_admin_keyboard(telegram_id, region, nominal)
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, reply_markup=admin_kb, parse_mode="HTML")
    except Exception as e:
        logger.error("APPLE_ADMIN_NOTIFY_FAILED error=%s", e)


@router.callback_query(F.data.startswith("apple_pay_card:"))
async def callback_apple_pay_card(callback: CallbackQuery):
    """Apple ID — pay via YooKassa (Telegram Payments)."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    price_rub = _apple_price_rub(region, nominal)
    price_kopecks = round(price_rub * 100)
    nominal_label = _apple_nominal_label(region, nominal)
    region_label = _APPLE_REGIONS.get(region, region)

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.card_payment_unavailable", "Оплата картой временно недоступна"), show_alert=True)
        return

    MIN_PAYMENT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_KOPECKS:
        await callback.answer(i18n_get_text(language, "errors.min_card_amount", "Сумма ниже минимальной для оплаты картой (64₽)"), show_alert=True)
        return

    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=f"apple_id_{region}_{nominal}",
        period_days=0,
        price_kopecks=price_kopecks,
        purchase_type="apple_id",
    )

    from aiogram.types import LabeledPrice
    payload = f"purchase:{purchase_id}"

    try:
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Apple ID {region_label} {nominal_label}",
            description=f"Пополнение Apple ID {region_label} на {nominal_label}",
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"Apple ID {nominal_label}", amount=price_kopecks)],
        )
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=i18n_get_text(language, "payment.invoice_timeout"),
            parse_mode="HTML",
        )

        async def _del_invoice(bot, cid, msg):
            try:
                await asyncio.sleep(15 * 60)
                await bot.delete_message(chat_id=cid, message_id=msg.message_id)
            except Exception:
                pass
        asyncio.create_task(_del_invoice(callback.bot, telegram_id, invoice_msg))
        await callback.answer()
    except Exception as e:
        logger.exception("APPLE_CARD_INVOICE_ERROR user=%s: %s", telegram_id, e)
        await callback.answer(i18n_get_text(language, "errors.payment_creation", "Ошибка создания платежа"), show_alert=True)


@router.callback_query(F.data.startswith("apple_pay_sbp:"))
async def callback_apple_pay_sbp(callback: CallbackQuery):
    """Apple ID — pay via SBP (Platega)."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    region = parts[1]
    nominal = int(parts[2])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    price_rub = _apple_price_rub(region, nominal)
    price_kopecks = round(price_rub * 100)
    nominal_label = _apple_nominal_label(region, nominal)
    region_label = _APPLE_REGIONS.get(region, region)

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "errors.sbp_unavailable_toast", "СБП временно недоступен"), show_alert=True)
        return

    try:
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)
        sbp_price_rubles = sbp_price_kopecks / 100.0

        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"apple_id_{region}_{nominal}",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="apple_id",
        )

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Apple ID {region_label} {nominal_label}",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception:
            pass

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url,
            )],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop", icon_custom_emoji_id=CE["back"], style="primary")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.exception("APPLE_SBP_ERROR user=%s: %s", telegram_id, e)
        await callback.answer(i18n_get_text(language, "errors.sbp_creation", "Ошибка создания платежа СБП"), show_alert=True)
