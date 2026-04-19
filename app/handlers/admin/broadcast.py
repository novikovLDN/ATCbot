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
) -> int | None:
    """Send message or photo. Returns message_id on success, None on failure."""
    from app.utils.telegram_safe import convert_tg_emoji
    text = convert_tg_emoji(text)
    if caption:
        caption = convert_tg_emoji(caption)
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if photo_file_id:
                    result = await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        parse_mode="HTML",
                    )
                else:
                    result = await bot.send_message(user_id, text, parse_mode="HTML")
                return result.message_id
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return None



async def _safe_send_with_buttons(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_file_id: str | None = None,
    caption: str | None = None,
) -> int | None:
    """Send message with optional inline buttons. Returns message_id on success, None on failure."""
    from app.utils.telegram_safe import convert_tg_emoji
    text = convert_tg_emoji(text)
    if caption:
        caption = convert_tg_emoji(caption)
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                if photo_file_id:
                    result = await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=caption or text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    result = await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")
                return result.message_id
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return None


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
        elif btn == "promo_traffic":
            label = f"📊 Купить трафик −{discount}%" if discount else "📊 Купить трафик"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"broadcast_promo_traffic:{broadcast_id}")])
        elif btn == "bypass":
            rows.append([InlineKeyboardButton(text="🌐 Включить обход", callback_data="traffic_info")])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(text="📢 Наш канал", url="https://t.me/ATC_VPN")])
        elif btn == "support":
            rows.append([InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/Atlas_SupportSecurity")])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(text="👥 Пригласить друга", callback_data="menu_referral")])
        elif btn == "happ_ios":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для iOS ⚡️",
                url="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
            )])
        elif btn == "happ_android":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для Android 🤖",
                url="https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
            )])
        elif btn == "web_client":
            rows.append([InlineKeyboardButton(
                text="🌐 Веб-клиент QoDev",
                url="https://qodev.dev",
            )])

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

        # Redirect to tariff screen
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state)

        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            f"🎁 Скидка {discount_percent}% автоматически применена! Действует {discount_label}."
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_promo_traffic:"))
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


@admin_broadcast_router.message(Command("notify_no_subscription"))
async def cmd_notify_no_subscription(message: Message, state: FSMContext):
    """Broadcast to users without active subscription or trial (admin only). Silently ignore non-admin."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    await state.set_state(AdminBroadcastNoSubscription.waiting_for_text)
    await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"), parse_mode="HTML")


@admin_broadcast_router.message(AdminBroadcastNoSubscription.waiting_for_text)
async def process_no_sub_broadcast_text(message: Message, state: FSMContext):
    """Process broadcast text, show preview, ask confirmation."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    if message.text and message.text.strip().lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.operation_cancelled"))
        return
    if not message.text or not message.text.strip():
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"), parse_mode="HTML")
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
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    action = callback.data.split(":")[1]
    await callback.answer()
    if action == "cancel":
        await state.clear()
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(i18n_get_text(language, "admin.operation_cancelled"), parse_mode="HTML")
        return
    if action != "confirm":
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(i18n_get_text(language, "broadcast._validation_message_empty"), parse_mode="HTML")
        await state.clear()
        return
    try:
        users = await database.get_eligible_no_subscription_broadcast_users()
        total = len(users)
    except Exception:
        total = 0
    language = await resolve_user_language(callback.from_user.id)
    await callback.message.edit_text(
        i18n_get_text(language, "broadcast._no_sub_sending", total=total),
        parse_mode="HTML",
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
                    parse_mode="HTML",
                )
            except Exception:
                pass

    asyncio.create_task(_run_broadcast())


@admin_broadcast_router.callback_query(F.data == "admin:broadcast")
async def callback_admin_broadcast(callback: CallbackQuery):
    """Раздел уведомлений"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "broadcast._section_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._create"), callback_data="broadcast:create")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_stats"), callback_data="broadcast:ab_stats")],
        [InlineKeyboardButton(text="🗑 Удалить уведомление", callback_data="broadcast:delete_list")],
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
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
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
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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
            i18n_get_text(language, "broadcast._enter_variant_a"),
            parse_mode="HTML",
        )
    else:
        await state.set_state(BroadcastCreate.waiting_for_message)
        await callback.message.edit_text(
            i18n_get_text(language, "broadcast._enter_message"),
            parse_mode="HTML",
        )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message_a)
async def process_broadcast_message_a(message: Message, state: FSMContext):
    """Обработка текста варианта A"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_a=message.text)
    await state.set_state(BroadcastCreate.waiting_for_message_b)
    await message.answer(
        i18n_get_text(language, "broadcast._enter_variant_b"),
        parse_mode="HTML",
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message_b)
async def process_broadcast_message_b(message: Message, state: FSMContext):
    """Обработка текста варианта B"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_b=message.text)
    await state.set_state(BroadcastCreate.waiting_for_emoji)
    await message.answer(
        "Отправьте эмодзи для уведомления (любой смайлик):\n\n"
        "Популярные: 📢 🔥 🎉 💰 ⚡ 🎁 🚀 ❗ 💎 🏆\n\n"
        "Или нажмите /skip чтобы отправить без эмодзи."
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message, F.text | F.photo)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста/фото уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
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
        "Популярные: 📢 🔥 🎉 💰 ⚡ 🎁 🚀 ❗ 💎 🏆\n\n"
        "Или нажмите /skip чтобы отправить без эмодзи."
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_emoji)
async def process_broadcast_emoji(message: Message, state: FSMContext):
    """Обработка выбора эмодзи"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)

    if not message.text or not message.text.strip():
        await message.answer("Отправьте эмодзи или /skip:")
        return

    text = message.text.strip()

    if text.lower() in ("/skip", "skip"):
        await state.update_data(emoji="", type="custom")
    else:
        if len(text) > 10:
            await message.answer("Слишком длинный текст. Отправьте эмодзи (1-2 символа) или /skip:")
            return
        await state.update_data(emoji=text, type="custom")
    await state.set_state(BroadcastCreate.waiting_for_buttons)
    await message.answer(
        "Выберите кнопки для уведомления:",
        reply_markup=get_broadcast_buttons_keyboard(language)
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_btn:"))
async def callback_broadcast_buttons(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора кнопок для уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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
            reply_markup=get_broadcast_segment_keyboard(language),
            parse_mode="HTML",
        )
    elif btn_type in ("promo_buy", "promo_traffic"):
        # Need to ask for discount percentage
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type not in buttons:
            buttons.append(btn_type)
        await state.update_data(broadcast_buttons=buttons, _pending_promo_type=btn_type)
        await state.set_state(BroadcastCreate.waiting_for_discount)
        if btn_type == "promo_traffic":
            await callback.message.edit_text(
                "Введите процент скидки на трафик для акции (число от 1 до 99):",
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(
                "Введите процент скидки для акции (число от 1 до 99):",
                parse_mode="HTML",
            )
    elif btn_type == "done":
        # Finished selecting buttons, move to segment
        await state.set_state(BroadcastCreate.waiting_for_segment)
        await callback.message.edit_text(
            "Выберите сегмент получателей:",
            reply_markup=get_broadcast_segment_keyboard(language),
            parse_mode="HTML",
        )
    else:
        # Toggle button in list (add or remove)
        data = await state.get_data()
        buttons = data.get("broadcast_buttons", [])
        if btn_type in buttons:
            buttons.remove(btn_type)
        else:
            buttons.append(btn_type)
        await state.update_data(broadcast_buttons=buttons)
        # Show updated keyboard with selected buttons
        await callback.message.edit_text(
            f"Выбранные кнопки: {', '.join(_btn_label(b) for b in buttons)}\n\n"
            "Выберите ещё кнопки или нажмите «Готово»:",
            reply_markup=get_broadcast_buttons_keyboard(language, selected=buttons),
            parse_mode="HTML",
        )


def _btn_label(btn_type: str) -> str:
    """Human-readable label for button type"""
    labels = {
        "buy": "🛒 Купить",
        "promo_buy": "🎁 Купить со скидкой",
        "promo_traffic": "📊 Купить трафик промо",
        "bypass": "🌐 Включить обход",
        "channel": "📢 Наш канал",
        "support": "💬 Поддержка",
        "referral": "👥 Реферальная программа",
        "happ_ios": "📲 Скачать Happ iOS",
        "happ_android": "📲 Скачать Happ Android",
        "web_client": "🌐 Веб-клиент QoDev",
    }
    return labels.get(btn_type, btn_type)


@admin_broadcast_router.message(BroadcastCreate.waiting_for_discount)
async def process_broadcast_discount(message: Message, state: FSMContext):
    """Обработка ввода скидки для кнопки 'Купить со скидкой'"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
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
    pending_type = data.get("_pending_promo_type", "promo_buy")
    if pending_type not in buttons:
        buttons.append(pending_type)
    await state.update_data(broadcast_buttons=buttons, broadcast_discount=discount)
    await state.set_state(BroadcastCreate.waiting_for_discount_duration)

    duration_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="6 часов", callback_data="promo_duration:6h"),
            InlineKeyboardButton(text="12 часов", callback_data="promo_duration:12h"),
        ],
        [
            InlineKeyboardButton(text="1 день", callback_data="promo_duration:1d"),
            InlineKeyboardButton(text="3 дня", callback_data="promo_duration:3d"),
        ],
        [
            InlineKeyboardButton(text="7 дней", callback_data="promo_duration:7d"),
            InlineKeyboardButton(text="14 дней", callback_data="promo_duration:14d"),
        ],
        [
            InlineKeyboardButton(text="30 дней", callback_data="promo_duration:30d"),
        ],
    ])
    await message.answer(
        f"Скидка {discount}% установлена.\n\n⏱ Выберите время действия скидки:",
        reply_markup=duration_keyboard,
    )


_DURATION_MAP = {
    "6h": (6, "часов", "6 часов"),
    "12h": (12, "часов", "12 часов"),
    "1d": (24, "часов", "1 день"),
    "3d": (72, "часов", "3 дня"),
    "7d": (168, "часов", "7 дней"),
    "14d": (336, "часов", "14 дней"),
    "30d": (720, "часов", "30 дней"),
}


@admin_broadcast_router.callback_query(F.data.startswith("promo_duration:"))
async def callback_promo_duration(callback: CallbackQuery, state: FSMContext):
    """Выбор времени действия скидки"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    duration_key = callback.data.split(":")[1]
    duration_hours, _, duration_label = _DURATION_MAP.get(duration_key, (168, "часов", "7 дней"))

    data = await state.get_data()
    discount = data.get("broadcast_discount", 0)

    await state.update_data(broadcast_discount_hours=duration_hours, broadcast_discount_label=duration_label)
    await state.set_state(BroadcastCreate.waiting_for_segment)

    await callback.message.edit_text(
        f"Скидка {discount}% на {duration_label}.\n\nВыберите сегмент получателей:",
        reply_markup=get_broadcast_segment_keyboard(language),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_segment:"))
async def callback_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сегмента получателей"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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

    prefix = f"{emoji} " if emoji else ""
    if is_ab_test:
        message_a = data_for_preview.get("message_a", "")
        message_b = data_for_preview.get("message_b", "")
        preview_text = (
            f"{prefix}{title}\n\n"
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
            f"{prefix}{title}\n\n"
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
        reply_markup=get_broadcast_confirm_keyboard(language),
        parse_mode="HTML",
    )


@admin_broadcast_router.callback_query(F.data == "broadcast:confirm_send")
async def callback_broadcast_confirm_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение и отправка уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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

        # Save broadcast discount if set (for promo_buy or promo_traffic)
        if broadcast_discount and ("promo_buy" in broadcast_buttons or "promo_traffic" in broadcast_buttons):
            data_for_save = await state.get_data()
            _disc_hours = data_for_save.get("broadcast_discount_hours", 168)
            _disc_label = data_for_save.get("broadcast_discount_label", "7 дней")
            await database.save_broadcast_discount(broadcast_id, broadcast_discount, _disc_hours, _disc_label)

        prefix = f"{emoji} " if emoji else ""
        if is_ab_test:
            final_message_a = f"{prefix}{title}\n\n{message_a}"
            final_message_b = f"{prefix}{title}\n\n{message_b}"
        else:
            if has_photo:
                final_message = f"{prefix}{title}\n\n{caption}".strip()
            else:
                final_message = f"{prefix}{title}\n\n{message_text}"

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
            reply_markup=None,
            parse_mode="HTML",
        )

        # Prepare message variants before launching background task
        if is_ab_test:
            msg_variants = {"a": final_message_a, "b": final_message_b}
        elif has_photo:
            msg_variants = {"text": final_message, "photo_file_id": photo_file_id}
        else:
            msg_variants = {"text": final_message}

        admin_id = callback.from_user.id
        chat_id = callback.message.chat.id

        async def _run_broadcast_send():
            try:
                semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
                sent_count = 0
                failed_list = []
                processed = 0

                async def _send_one(
                    user_id: int,
                    msg: str,
                    variant,
                    p_file_id: str | None = None,
                    cap: str | None = None,
                ):
                    msg_id = await _safe_send_with_buttons(
                        bot, user_id, msg, semaphore,
                        reply_markup=reply_markup,
                        photo_file_id=p_file_id, caption=cap,
                    )
                    return (user_id, variant, msg_id)

                for i in range(0, total, BROADCAST_BATCH_SIZE):
                    batch = user_ids[i:i + BROADCAST_BATCH_SIZE]
                    batch_items = []
                    for user_id in batch:
                        if is_ab_test:
                            variant = "A" if random.random() < 0.5 else "B"
                            msg = msg_variants["a"] if variant == "A" else msg_variants["b"]
                            batch_items.append((user_id, msg, variant, None, None))
                        else:
                            variant = None
                            if has_photo:
                                batch_items.append((user_id, msg_variants["text"], variant, msg_variants["photo_file_id"], msg_variants["text"]))
                            else:
                                batch_items.append((user_id, msg_variants["text"], variant, None, None))

                    tasks = [
                        _send_one(uid, msg, v, p_fid, cap)
                        for uid, msg, v, p_fid, cap in batch_items
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for r in results:
                        if isinstance(r, Exception):
                            logger.warning(f"BROADCAST_TASK_ERROR broadcast_id={broadcast_id} error={r}")
                            continue
                        uid, v, msg_id = r
                        if msg_id:
                            await database.log_broadcast_send(broadcast_id, uid, "sent", v, message_id=msg_id)
                            sent_count += 1
                        else:
                            failed_list.append({"telegram_id": uid, "error": "Send failed"})
                            await database.log_broadcast_send(broadcast_id, uid, "failed", v)

                    processed += len(batch)
                    logger.info(f"BROADCAST_PROGRESS processed={processed}/{total}")
                    await asyncio.sleep(BROADCAST_BATCH_PAUSE)

                failed_count = len(failed_list)
                total_users = total
                logger.info(f"BROADCAST_COMPLETED total={total}")

                await database._log_audit_event_atomic_standalone(
                    "broadcast_sent",
                    admin_id,
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

                await bot.edit_message_text(result_text, chat_id=chat_id, message_id=callback.message.message_id, reply_markup=keyboard)

            except asyncio.CancelledError:
                logger.info(f"BROADCAST_CANCELLED broadcast_id={broadcast_id}")
                raise
            except Exception as e:
                logger.exception(f"Error in broadcast send: {e}")
                try:
                    await bot.send_message(chat_id, f"Ошибка при отправке уведомления: {e}", parse_mode="HTML")
                except Exception:
                    pass
                try:
                    from app.services.admin_alerts import send_alert
                    await send_alert(bot, "worker", f"Broadcast send error: {type(e).__name__}: {str(e)[:200]}")
                except Exception:
                    pass

        asyncio.create_task(_run_broadcast_send())

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


@admin_broadcast_router.callback_query(F.data == "broadcast:delete_list")
async def callback_broadcast_delete_list(callback: CallbackQuery):
    """Список броадкастов для удаления у пользователей."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    broadcasts = await database.get_recent_broadcasts(limit=10)
    if not broadcasts:
        await safe_edit_text(
            callback.message,
            "📭 Нет броадкастов для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")],
            ]),
        )
        return

    lines = ["🗑 <b>Удалить уведомление у пользователей</b>\n"]
    buttons = []
    for b in broadcasts:
        bid = b["id"]
        title = (b["title"] or "—")[:30]
        sent = b["sent_count"] or 0
        has_ids = b["has_msg_ids"] or 0
        date_str = b["created_at"].strftime("%d.%m %H:%M") if b["created_at"] else "—"
        label = f"#{bid} {title} ({sent} отпр.)"
        if has_ids == 0:
            label += " ❌ нет ID"
        lines.append(f"• <b>#{bid}</b> {title} — {sent} отпр., {has_ids} с ID — {date_str}")
        if has_ids > 0:
            buttons.append([InlineKeyboardButton(
                text=f"🗑 #{bid} {title}",
                callback_data=f"broadcast:delete_confirm:{bid}",
            )])

    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")])
    text = "\n".join(lines)
    if not any("delete_confirm" in str(b) for row in buttons for b in row):
        text += "\n\n⚠️ Ни один броадкаст не имеет сохранённых message_id. Удаление доступно только для новых уведомлений."
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_broadcast_router.callback_query(F.data.startswith("broadcast:delete_confirm:"))
async def callback_broadcast_delete_confirm(callback: CallbackQuery):
    """Подтверждение удаления броадкаста."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    broadcast_id = int(callback.data.split(":")[-1])
    pairs = await database.get_broadcast_message_ids(broadcast_id)

    if not pairs:
        await safe_edit_text(
            callback.message,
            f"❌ Броадкаст #{broadcast_id} — нет сообщений с сохранёнными ID для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="broadcast:delete_list")],
            ]),
        )
        return

    text = (
        f"🗑 <b>Удалить броадкаст #{broadcast_id}?</b>\n\n"
        f"Будет удалено <b>{len(pairs)}</b> сообщений из чатов пользователей.\n\n"
        f"⚠️ Это действие необратимо."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Удалить {len(pairs)} сообщений", callback_data=f"broadcast:delete_exec:{broadcast_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:delete_list")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)


@admin_broadcast_router.callback_query(F.data.startswith("broadcast:delete_exec:"))
async def callback_broadcast_delete_exec(callback: CallbackQuery):
    """Выполнение удаления броадкаста у пользователей."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    broadcast_id = int(callback.data.split(":")[-1])
    pairs = await database.get_broadcast_message_ids(broadcast_id)

    if not pairs:
        await safe_edit_text(
            callback.message,
            f"❌ Нет сообщений для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="broadcast:delete_list")],
            ]),
        )
        return

    await safe_edit_text(
        callback.message,
        f"🗑 Удаляю {len(pairs)} сообщений броадкаста #{broadcast_id}...\n\n⏳ Это может занять несколько минут. Результат будет отправлен в чат.",
    )

    # Run deletion in background to avoid webhook timeout
    async def _delete_in_background():
        bot = callback.bot
        deleted = 0
        failed = 0
        for telegram_id, message_id in pairs:
            try:
                await bot.delete_message(chat_id=telegram_id, message_id=message_id)
                deleted += 1
            except Exception:
                failed += 1
            if deleted % 30 == 0:
                await asyncio.sleep(1)  # Rate limit

        await database.mark_broadcast_messages_deleted(broadcast_id)

        text = (
            f"✅ <b>Броадкаст #{broadcast_id} удалён</b>\n\n"
            f"🗑 Удалено: {deleted}\n"
            f"❌ Не удалось: {failed}\n"
            f"📊 Всего: {len(pairs)}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К списку", callback_data="broadcast:delete_list")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:broadcast")],
        ])
        await bot.send_message(
            chat_id=config.ADMIN_TELEGRAM_ID, text=text,
            reply_markup=keyboard, parse_mode="HTML",
        )
        logger.info(f"BROADCAST_BULK_DELETE broadcast_id={broadcast_id} deleted={deleted} failed={failed} total={len(pairs)}")

    asyncio.create_task(_delete_in_background())


@admin_broadcast_router.callback_query(F.data == "broadcast:ab_stats")
async def callback_broadcast_ab_stats(callback: CallbackQuery):
    """Список A/B тестов"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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
