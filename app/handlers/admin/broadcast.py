"""
Admin broadcast handlers: create broadcasts, A/B tests, no-subscription broadcasts.
"""
import logging
import asyncio
import random
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.states import BroadcastCreate, AdminBroadcastNoSubscription
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_broadcast_test_type_keyboard,
    get_broadcast_segment_keyboard,
    get_broadcast_confirm_keyboard,
    get_broadcast_buttons_keyboard,
    get_ab_test_list_keyboard,
)
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.guards import ensure_db_ready_callback, ensure_db_ready_message

admin_broadcast_router = Router()
logger = logging.getLogger(__name__)

# Production broadcast: controlled concurrency, rate limiting, event-loop safe
BROADCAST_CONCURRENCY = 15          # Safe under Telegram 30 msg/sec
BROADCAST_BATCH_SIZE = 200          # Soft batch limit
BROADCAST_BATCH_PAUSE = 2           # Seconds between batches
BROADCAST_RETRY_LIMIT = 3           # Retry per user


async def _safe_send(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    photo_file_id: str | None = None,
    caption: str | None = None,
) -> bool:
    """Send message or photo with concurrency limit and TelegramRetryAfter respect."""
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if photo_file_id:
                    # Если есть фото — отправляем фото с подписью
                    await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        parse_mode="HTML",
                    )
                else:
                    await bot.send_message(user_id, text, parse_mode="HTML")
                return True
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return False



async def _safe_send_with_buttons(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_file_id: str | None = None,
    caption: str | None = None,
) -> bool:
    """Send message with optional inline buttons."""
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if photo_file_id:
                    await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")
                return True
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return False


def _build_broadcast_reply_markup(
    buttons: list[str],
    broadcast_id: int,
    discount: int | None = None,
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard for broadcast message based on selected buttons."""
    if not buttons:
        return None

    rows = []
    for btn in buttons:
        if btn == "buy":
            rows.append([InlineKeyboardButton(text="🛒 Купить", callback_data="menu_buy_vpn")])
        elif btn == "promo_buy":
            label = f"🎁 Купить со скидкой {discount}%" if discount else "🎁 Купить со скидкой"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"broadcast_promo_buy:{broadcast_id}")])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(text="📢 Наш канал", url="https://t.me/ATC_VPN")])
        elif btn == "support":
            rows.append([InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/Atlas_SupportSecurity")])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(text="👥 Пригласить друга", callback_data="menu_referral")])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_promo_buy:"))
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
            # No discount found, just redirect to tariff selection
            from app.handlers.common.screens import show_tariffs_main_screen
            await show_tariffs_main_screen(callback, state)
            return

        discount_percent = discount.get("discount_percent", 0)

        # Auto-apply discount to user
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )

        # Redirect to tariff screen
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state)

        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            f"🎁 Скидка {discount_percent}% автоматически применена! Действует 7 дней."
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@admin_broadcast_router.message(Command("notify_no_subscription"))
async def cmd_notify_no_subscription(message: Message, state: FSMContext):
    """Broadcast to users without active subscription or trial (admin only). Silently ignore non-admin."""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)
    await state.set_state(AdminBroadcastNoSubscription.waiting_for_text)
    await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"))


@admin_broadcast_router.message(AdminBroadcastNoSubscription.waiting_for_text)
async def process_no_sub_broadcast_text(message: Message, state: FSMContext):
    """Process broadcast text, show preview, ask confirmation."""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    if message.text and message.text.strip().lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.operation_cancelled"))
        return
    if not message.text or not message.text.strip():
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"))
        return
    text = message.text.strip()
    try:
        users = await database.get_eligible_no_subscription_broadcast_users()
        total = len(users)
    except Exception as e:
        logger.exception(f"Error fetching no_sub broadcast users: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.check_logs"))
        return
    if total == 0:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "broadcast._no_sub_zero_recipients"))
        await state.clear()
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(AdminBroadcastNoSubscription.waiting_for_confirmation)
    language = await resolve_user_language(message.from_user.id)
    preview = text[:500] + ("..." if len(text) > 500 else "")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.confirm"), callback_data="no_sub_broadcast:confirm")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="no_sub_broadcast:cancel")],
    ])
    await message.answer(
        i18n_get_text(language, "broadcast._no_sub_preview", preview=preview, total=total),
        reply_markup=keyboard
    )


@admin_broadcast_router.callback_query(F.data.startswith("no_sub_broadcast:"))
async def callback_no_sub_broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle confirm/cancel for no-subscription broadcast."""
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await callback.answer()
        return
    action = callback.data.split(":")[1]
    await callback.answer()
    if action == "cancel":
        await state.clear()
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(i18n_get_text(language, "admin.operation_cancelled"))
        return
    if action != "confirm":
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(i18n_get_text(language, "broadcast._validation_message_empty"))
        await state.clear()
        return
    try:
        users = await database.get_eligible_no_subscription_broadcast_users()
        total = len(users)
    except Exception:
        total = 0
    language = await resolve_user_language(callback.from_user.id)
    await callback.message.edit_text(
        i18n_get_text(language, "broadcast._no_sub_sending", total=total)
    )
    await state.clear()

    async def _run_broadcast():
        try:
            from broadcast_service import run_no_subscription_broadcast
            await run_no_subscription_broadcast(
                bot, text, callback.from_user.id, notify_admin_on_complete=True
            )
        except asyncio.CancelledError:
            logger.info("no_sub_broadcast task cancelled")
        except Exception as e:
            logger.exception(f"no_sub_broadcast failed: {e}")
            try:
                await bot.send_message(
                    callback.from_user.id,
                    i18n_get_text(
                        await resolve_user_language(callback.from_user.id),
                        "admin.check_logs"
                    ),
                )
            except Exception:
                pass

    asyncio.create_task(_run_broadcast())


@admin_broadcast_router.callback_query(F.data == "admin:broadcast")
async def callback_admin_broadcast(callback: CallbackQuery):
    """Раздел уведомлений"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "broadcast._section_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._create"), callback_data="broadcast:create")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_stats"), callback_data="broadcast:ab_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:notifications")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()

    # Логируем действие
    await database._log_audit_event_atomic_standalone("admin_broadcast_view", callback.from_user.id, None, "Admin viewed broadcast section")


@admin_broadcast_router.callback_query(F.data == "broadcast:create")
async def callback_broadcast_create(callback: CallbackQuery, state: FSMContext):
    """Начать создание уведомления"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(BroadcastCreate.waiting_for_title)
    await callback.message.answer(
        i18n_get_text(language, "broadcast._enter_title")
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_title)
async def process_broadcast_title(message: Message, state: FSMContext):
    """Обработка заголовка уведомления"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(title=message.text)
    await state.set_state(BroadcastCreate.waiting_for_test_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_test_type_keyboard(language)
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_test_type:"))
async def callback_broadcast_test_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа тестирования"""
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)
    test_type = callback.data.split(":")[1]
    
    await state.update_data(is_ab_test=(test_type == "ab"))
    
    if test_type == "ab":
        await state.set_state(BroadcastCreate.waiting_for_message_a)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_variant_a")
        )
    else:
        await state.set_state(BroadcastCreate.waiting_for_message)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_message")
        )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message_a)
async def process_broadcast_message_a(message: Message, state: FSMContext):
    """Обработка текста варианта A"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_a=message.text)
    await state.set_state(BroadcastCreate.waiting_for_message_b)
    await message.answer(
        i18n_get_text(language, "broadcast._enter_variant_b")
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message_b)
async def process_broadcast_message_b(message: Message, state: FSMContext):
    """Обработка текста варианта B"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_b=message.text)
    await state.set_state(BroadcastCreate.waiting_for_emoji)
    await message.answer(
        "Отправьте эмодзи для уведомления (любой смайлик):\n\n"
        "Популярные: 📢 🔥 🎉 💰 ⚡ 🎁 🚀 ❗ 💎 🏆"
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message, F.text | F.photo)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста/фото уведомления"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)

    # Поддержка отмены только для текстовых сообщений
    if message.text and message.text.strip().lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        await message.answer(i18n_get_text(language, "admin.operation_cancelled"))
        return

    # Принимаем либо фото (с подписью), либо текст
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        caption = message.caption or ""
        await state.update_data(
            message=None,
            has_photo=True,
            photo_file_id=photo_file_id,
            caption=caption,
        )
    elif message.text and message.text.strip():
        await state.update_data(
            message=message.text,
            has_photo=False,
            photo_file_id=None,
            caption=None,
        )
    else:
        await message.answer(i18n_get_text(language, "broadcast._enter_message"))
        return

    await state.set_state(BroadcastCreate.waiting_for_emoji)
    await message.answer(
        "Отправьте эмодзи для уведомления (любой смайлик):\n\n"
        "Популярные: 📢 🔥 🎉 💰 ⚡ 🎁 🚀 ❗ 💎 🏆"
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_emoji)
async def process_broadcast_emoji(message: Message, state: FSMContext):
    """Обработка выбора эмодзи"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)

    if not message.text or not message.text.strip():
        await message.answer("Отправьте эмодзи для уведомления:")
        return

    emoji = message.text.strip()
    # Allow any text as emoji prefix (user can send any emoji or even short text)
    if len(emoji) > 10:
        await message.answer("Слишком длинный текст. Отправьте эмодзи (1-2 символа):")
        return

    await state.update_data(emoji=emoji, type="custom")
    await state.set_state(BroadcastCreate.waiting_for_buttons)
    await message.answer(
        "Выберите кнопки для уведомления:",
        reply_markup=get_broadcast_buttons_keyboard(language)
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_btn:"))
async def callback_broadcast_buttons(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора кнопок для уведомления"""
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)
    btn_type = callback.data.split(":")[1]

    if btn_type == "none":
        await state.update_data(broadcast_buttons=[])
        await state.set_state(BroadcastCreate.waiting_for_segment)
        await callback.message.edit_text(
            "Выберите сегмент получателей:",
            reply_markup=get_broadcast_segment_keyboard(language)
        )
    elif btn_type == "promo_buy":
        # Need to ask for discount percentage
        await state.set_state(BroadcastCreate.waiting_for_discount)
        await callback.message.edit_text(
            "Введите процент скидки для акции (число от 1 до 99):"
        )
    elif btn_type == "done":
        # Finished selecting buttons, move to segment
        await state.set_state(BroadcastCreate.waiting_for_segment)
        await callback.message.edit_text(
            "Выберите сегмент получателей:",
            reply_markup=get_broadcast_segment_keyboard(language)
        )
    else:
        # Add button to list: buy, channel, support, referral
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type not in buttons:
            buttons.append(btn_type)
        await state.update_data(broadcast_buttons=buttons)
        # Show updated keyboard with selected buttons
        await callback.message.edit_text(
            f"Выбранные кнопки: {', '.join(_btn_label(b) for b in buttons)}\n\n"
            "Выберите ещё кнопки или нажмите «Готово»:",
            reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons)
        )


def _btn_label(btn_type: str) -> str:
    """Human-readable label for button type"""
    labels = {
        "buy": "🛒 Купить",
        "promo_buy": "🎁 Купить со скидкой",
        "channel": "📢 Наш канал",
        "support": "💬 Поддержка",
        "referral": "👥 Реферальная программа",
    }
    return labels.get(btn_type, btn_type)


@admin_broadcast_router.message(BroadcastCreate.waiting_for_discount)
async def process_broadcast_discount(message: Message, state: FSMContext):
    """Обработка ввода скидки для кнопки 'Купить со скидкой'"""
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    language = await resolve_user_language(message.from_user.id)

    try:
        discount = int(message.text.strip())
        if not 1 <= discount <= 99:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Введите число от 1 до 99:")
        return

    data = await state.get_data()
    buttons = data.get("broadcast_buttons", [])
    if "promo_buy" not in buttons:
        buttons.append("promo_buy")
    await state.update_data(broadcast_buttons=buttons, broadcast_discount=discount)
    await state.set_state(BroadcastCreate.waiting_for_segment)
    await message.answer(
        f"Скидка {discount}% установлена.\n\nВыберите сегмент получателей:",
        reply_markup=get_broadcast_segment_keyboard(language)
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_segment:"))
async def callback_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сегмента получателей"""
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer()
    segment = callback.data.split(":")[1]

    data_for_preview = await state.get_data()
    title = data_for_preview.get("title")
    emoji = data_for_preview.get("emoji", "📢")
    is_ab_test = data_for_preview.get("is_ab_test", False)
    has_photo = data_for_preview.get("has_photo", False)
    caption = data_for_preview.get("caption", "") if has_photo else ""
    buttons = data_for_preview.get("broadcast_buttons", [])
    discount = data_for_preview.get("broadcast_discount")

    segment_name = {
        "all_users": "Все пользователи",
        "active_subscriptions": "Только активные подписки"
    }

    if is_ab_test:
        message_a = data_for_preview.get("message_a", "")
        message_b = data_for_preview.get("message_b", "")
        preview_text = (
            f"{emoji} {title}\n\n"
            f"🔬 A/B ТЕСТ\n\n"
            f"Вариант A:\n{message_a}\n\n"
            f"Вариант B:\n{message_b}\n\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    else:
        message_text = data_for_preview.get("message", "")
        if has_photo:
            body = f"[📷 Фото]\n{caption}".strip()
        else:
            body = message_text
        preview_text = (
            f"{emoji} {title}\n\n"
            f"{body}\n\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )

    if buttons:
        preview_text += f"\nКнопки: {', '.join(_btn_label(b) for b in buttons)}"
    if discount:
        preview_text += f"\nСкидка: {discount}%"

    await state.update_data(segment=segment)
    await state.set_state(BroadcastCreate.waiting_for_confirm)

    language = await resolve_user_language(callback.from_user.id)

    preview_confirm_text = i18n_get_text(language, "broadcast._preview_confirm", preview=preview_text)
    await callback.message.edit_text(
        preview_confirm_text,
        reply_markup=get_broadcast_confirm_keyboard(language)
    )


@admin_broadcast_router.callback_query(F.data == "broadcast:confirm_send")
async def callback_broadcast_confirm_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и отправка уведомления"""
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    language = await resolve_user_language(callback.from_user.id)
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    message_a = data.get("message_a")
    message_b = data.get("message_b")
    is_ab_test = data.get("is_ab_test", False)
    has_photo = data.get("has_photo", False)
    photo_file_id = data.get("photo_file_id")
    caption = data.get("caption") or ""
    broadcast_type = data.get("type", "custom")
    segment = data.get("segment")
    emoji = data.get("emoji", "📢")
    broadcast_buttons = data.get("broadcast_buttons", [])
    broadcast_discount = data.get("broadcast_discount")

    # Проверка данных
    if not all([title, segment]):
        await callback.message.answer("Ошибка: не все данные заполнены. Начните заново.")
        await state.clear()
        return

    if is_ab_test:
        if not all([message_a, message_b]):
            await callback.message.answer("Ошибка: не заполнены тексты вариантов A и B. Начните заново.")
            await state.clear()
            return
    else:
        if not (message_text or has_photo):
            await callback.message.answer("Ошибка: не заполнен текст уведомления. Начните заново.")
            await state.clear()
            return

    try:
        # Создаем уведомление в БД
        broadcast_id = await database.create_broadcast(
            title, caption if has_photo else message_text, broadcast_type, segment, callback.from_user.id,
            is_ab_test=is_ab_test, message_a=message_a, message_b=message_b
        )

        # Save broadcast discount if set
        if broadcast_discount and "promo_buy" in broadcast_buttons:
            await database.save_broadcast_discount(broadcast_id, broadcast_discount)

        if is_ab_test:
            final_message_a = f"{emoji} {title}\n\n{message_a}"
            final_message_b = f"{emoji} {title}\n\n{message_b}"
        else:
            if has_photo:
                final_message = f"{emoji} {title}\n\n{caption}".strip()
            else:
                final_message = f"{emoji} {title}\n\n{message_text}"

        # Build inline keyboard for broadcast message
        reply_markup = _build_broadcast_reply_markup(broadcast_buttons, broadcast_id, broadcast_discount)

        # Получаем список пользователей по сегменту
        user_ids = await database.get_users_by_segment(segment)
        total = len(user_ids)

        logger.info(
            f"BROADCAST_START broadcast_id={broadcast_id} segment={segment} total_users={total}"
        )

        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._sending", total=total),
            reply_markup=None
        )

        semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
        sent_count = 0
        failed_list = []
        processed = 0

        async def _send_one(
            user_id: int,
            msg: str,
            variant,
            photo_file_id: str | None = None,
            caption: str | None = None,
        ):
            ok = await _safe_send_with_buttons(
                bot, user_id, msg, semaphore,
                reply_markup=reply_markup,
                photo_file_id=photo_file_id, caption=caption,
            )
            return (user_id, variant, ok)

        for i in range(0, total, BROADCAST_BATCH_SIZE):
            batch = user_ids[i:i + BROADCAST_BATCH_SIZE]
            batch_items = []
            for user_id in batch:
                if is_ab_test:
                    variant = "A" if random.random() < 0.5 else "B"
                    msg = final_message_a if variant == "A" else final_message_b
                    batch_items.append((user_id, msg, variant, None, None))
                else:
                    variant = None
                    if has_photo:
                        msg = final_message
                        batch_items.append((user_id, msg, variant, photo_file_id, final_message))
                    else:
                        msg = final_message
                        batch_items.append((user_id, msg, variant, None, None))

            tasks = [
                _send_one(uid, msg, variant, p_file_id, cap)
                for uid, msg, variant, p_file_id, cap in batch_items
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for r in results:
                if isinstance(r, Exception):
                    logger.warning(f"BROADCAST_TASK_ERROR broadcast_id={broadcast_id} error={r}")
                    continue
                user_id, variant, ok = r
                if ok:
                    await database.log_broadcast_send(broadcast_id, user_id, "sent", variant)
                    sent_count += 1
                else:
                    failed_list.append({"telegram_id": user_id, "error": "Send failed"})
                    await database.log_broadcast_send(broadcast_id, user_id, "failed", variant)
            
            processed += len(batch)
            logger.info(f"BROADCAST_PROGRESS processed={processed}/{total}")
            await asyncio.sleep(BROADCAST_BATCH_PAUSE)
        
        failed_count = len(failed_list)
        total_users = total
        logger.info(f"BROADCAST_COMPLETED total={total}")
        
        await database._log_audit_event_atomic_standalone(
            "broadcast_sent",
            callback.from_user.id,
            None,
            f"Broadcast ID: {broadcast_id}, Segment: {segment}, Sent: {sent_count}, Failed: {failed_count}"
        )
        
        # Admin report (localized)
        if failed_count == 0:
            result_text = i18n_get_text(language, "broadcast._report_success", total=total_users, sent=sent_count, broadcast_id=broadcast_id)
        else:
            failed_lines = "\n".join(
                f"{f['telegram_id']} — {f['error']}" for f in failed_list[:20]
            )
            if len(failed_list) > 20:
                failed_lines += f"\n... and {len(failed_list) - 20} more"
            result_text = i18n_get_text(language, "broadcast._report_partial", total=total_users, sent=sent_count, failed=failed_count, broadcast_id=broadcast_id, failed_list=failed_lines)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_broadcast"), callback_data="admin:broadcast")],
        ])
        
        await callback.message.edit_text(result_text, reply_markup=keyboard)
        
    except Exception as e:
        logger.exception(f"Error in broadcast send: {e}")
        await callback.message.answer(f"Ошибка при отправке уведомления: {e}")
        try:
            from app.services.admin_alerts import send_alert
            await send_alert(callback.bot, "worker", f"Broadcast send error: {type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass
    
    finally:
        await state.clear()


@admin_broadcast_router.callback_query(F.data == "broadcast:ab_stats")
async def callback_broadcast_ab_stats(callback: CallbackQuery):
    """Список A/B тестов"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        ab_tests = await database.get_ab_test_broadcasts()
        
        if not ab_tests:
            text = i18n_get_text(language, "broadcast._ab_stats_empty")
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            return
        
        text = i18n_get_text(language, "broadcast._ab_stats_select")
        keyboard = get_ab_test_list_keyboard(ab_tests, language)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stats_list", callback.from_user.id, None, f"Viewed {len(ab_tests)} A/B tests")
    
    except Exception as e:
        logger.exception(f"Error in callback_broadcast_ab_stats: {e}")
        await callback.message.answer(
            i18n_get_text(language, "broadcast._ab_stats_error")
        )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast:ab_stat:"))
async def callback_broadcast_ab_stat_detail(callback: CallbackQuery):
    """Статистика конкретного A/B теста"""
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    try:
        broadcast_id = int(callback.data.split(":")[2])

        # Получаем информацию об уведомлении
        broadcast = await database.get_broadcast(broadcast_id)
        if not broadcast:
            await callback.message.answer("Уведомление не найдено.")
            return
        
        # Получаем статистику
        stats = await database.get_ab_test_stats(broadcast_id)
        
        if not stats:
            text = f"📊 A/B статистика\n\nУведомление: #{broadcast_id}\n\nНедостаточно данных для анализа."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            return
        
        # Формируем текст статистики
        total_sent = stats["total_sent"]
        variant_a_sent = stats["variant_a_sent"]
        variant_b_sent = stats["variant_b_sent"]
        
        # Проценты
        if total_sent > 0:
            percent_a = round((variant_a_sent / total_sent) * 100)
            percent_b = round((variant_b_sent / total_sent) * 100)
        else:
            percent_a = 0
            percent_b = 0
        
        text = (
            f"📊 A/B статистика\n\n"
            f"Уведомление: #{broadcast_id}\n"
            f"Заголовок: {broadcast.get('title', '—')}\n\n"
            f"Вариант A:\n"
            f"— Отправлено: {variant_a_sent} ({percent_a}%)\n\n"
            f"Вариант B:\n"
            f"— Отправлено: {variant_b_sent} ({percent_b}%)\n\n"
            f"Всего отправлено: {total_sent}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stat_detail", callback.from_user.id, None, f"Viewed A/B stats for broadcast {broadcast_id}")
    
    except (ValueError, IndexError) as e:
        logging.error(f"Error parsing broadcast ID: {e}")
        await callback.message.answer("Ошибка: неверный ID уведомления.")
    except Exception as e:
        logger.exception(f"Error in callback_broadcast_ab_stat_detail: {e}")
        await callback.message.answer("Ошибка при получении статистики A/B теста. Проверь логи.")
