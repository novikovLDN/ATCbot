"""
Admin broadcast handlers: create broadcasts, A/B tests, no-subscription broadcasts.
"""
import logging
import asyncio
import random

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
    get_broadcast_type_keyboard,
    get_broadcast_segment_keyboard,
    get_broadcast_confirm_keyboard,
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


async def _safe_send(bot: Bot, user_id: int, text: str, semaphore: asyncio.Semaphore) -> bool:
    """Send message with concurrency limit and TelegramRetryAfter respect."""
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            try:
                await bot.send_message(user_id, text)
                return True
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                await asyncio.sleep(1)
        return False



@admin_broadcast_router.message(Command("notify_no_subscription"))
async def cmd_notify_no_subscription(message: Message, state: FSMContext):
    """Broadcast to users without active subscription or trial (admin only). Silently ignore non-admin."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    await state.set_state(AdminBroadcastNoSubscription.waiting_for_text)
    await message.answer(i18n_get_text(language, "broadcast._no_sub_enter_text"))


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
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
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
    
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "broadcast._section_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._create"), callback_data="broadcast:create")],
        [InlineKeyboardButton(text=i18n_get_text(language, "broadcast._ab_stats"), callback_data="broadcast:ab_stats")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
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
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
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
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message_b=message.text)
    await state.set_state(BroadcastCreate.waiting_for_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_type_keyboard(language)
    )


@admin_broadcast_router.message(BroadcastCreate.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста уведомления"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    language = await resolve_user_language(message.from_user.id)
    
    await state.update_data(message=message.text)
    await state.set_state(BroadcastCreate.waiting_for_type)
    await message.answer(
        i18n_get_text(language, "broadcast._select_type"),
        reply_markup=get_broadcast_type_keyboard(language)
    )


@admin_broadcast_router.callback_query(F.data.startswith("broadcast_type:"))
async def callback_broadcast_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа уведомления"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    broadcast_type = callback.data.split(":")[1]
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    
    # Формируем предпросмотр
    type_emoji = {
        "info": "ℹ️",
        "maintenance": "🔧",
        "security": "🔒",
        "promo": "🎯"
    }
    type_name = {
        "info": "Информация",
        "maintenance": "Технические работы",
        "security": "Безопасность",
        "promo": "Промо"
    }
    
    await state.update_data(type=broadcast_type)
    await state.set_state(BroadcastCreate.waiting_for_segment)
    
    language = await resolve_user_language(callback.from_user.id)
    
    await callback.message.edit_text(
        i18n_get_text(language, "broadcast._select_segment"),
        reply_markup=get_broadcast_segment_keyboard(language)
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
    
    data = await state.get_data()
    title = data.get("title")
    message_text = data.get("message")
    broadcast_type = data.get("type")
    
    # Формируем предпросмотр
    type_emoji = {
        "info": "ℹ️",
        "maintenance": "🔧",
        "security": "🔒",
        "promo": "🎯"
    }
    type_name = {
        "info": "Информация",
        "maintenance": "Технические работы",
        "security": "Безопасность",
        "promo": "Промо"
    }
    segment_name = {
        "all_users": "Все пользователи",
        "active_subscriptions": "Только активные подписки"
    }
    
    data_for_preview = await state.get_data()
    is_ab_test = data_for_preview.get("is_ab_test", False)
    
    if is_ab_test:
        message_a = data_for_preview.get("message_a", "")
        message_b = data_for_preview.get("message_b", "")
        preview_text = (
            f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n"
            f"🔬 A/B ТЕСТ\n\n"
            f"Вариант A:\n{message_a}\n\n"
            f"Вариант B:\n{message_b}\n\n"
            f"Тип: {type_name.get(broadcast_type, broadcast_type)}\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    else:
        message_text = data_for_preview.get("message", "")
        preview_text = (
            f"{type_emoji.get(broadcast_type, '📢')} {title}\n\n"
            f"{message_text}\n\n"
            f"Тип: {type_name.get(broadcast_type, broadcast_type)}\n"
            f"Сегмент: {segment_name.get(segment, segment)}"
        )
    
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
    broadcast_type = data.get("type")
    segment = data.get("segment")
    
    # Проверка данных
    if not all([title, broadcast_type, segment]):
        await callback.message.answer("Ошибка: не все данные заполнены. Начните заново.")
        await state.clear()
        return
    
    if is_ab_test:
        if not all([message_a, message_b]):
            await callback.message.answer("Ошибка: не заполнены тексты вариантов A и B. Начните заново.")
            await state.clear()
            return
    else:
        if not message_text:
            await callback.message.answer("Ошибка: не заполнен текст уведомления. Начните заново.")
            await state.clear()
            return
    
    try:
        # Создаем уведомление в БД
        broadcast_id = await database.create_broadcast(
            title, message_text, broadcast_type, segment, callback.from_user.id,
            is_ab_test=is_ab_test, message_a=message_a, message_b=message_b
        )
        
        # Формируем сообщения для отправки
        type_emoji = {
            "info": "ℹ️",
            "maintenance": "🔧",
            "security": "🔒",
            "promo": "🎯"
        }
        emoji = type_emoji.get(broadcast_type, "📢")
        
        if is_ab_test:
            final_message_a = f"{emoji} {title}\n\n{message_a}"
            final_message_b = f"{emoji} {title}\n\n{message_b}"
        else:
            final_message = f"{emoji} {title}\n\n{message_text}"
        
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
        failed_list = []  # [{"telegram_id": int, "error": str}, ...]
        processed = 0
        
        async def _send_one(user_id: int, msg: str, variant):
            ok = await _safe_send(bot, user_id, msg, semaphore)
            return (user_id, variant, ok)
        
        for i in range(0, total, BROADCAST_BATCH_SIZE):
            batch = user_ids[i:i + BROADCAST_BATCH_SIZE]
            batch_items = []
            for user_id in batch:
                if is_ab_test:
                    variant = "A" if random.random() < 0.5 else "B"
                    msg = final_message_a if variant == "A" else final_message_b
                else:
                    variant = None
                    msg = final_message
                batch_items.append((user_id, msg, variant))
            
            tasks = [_send_one(uid, msg, variant) for uid, msg, variant in batch_items]
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
        logging.exception(f"Error in broadcast send: {e}")
        await callback.message.answer(f"Ошибка при отправке уведомления: {e}")
    
    finally:
        await state.clear()


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
        logging.exception(f"Error in callback_broadcast_ab_stats: {e}")
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
        logging.exception(f"Error in callback_broadcast_ab_stat_detail: {e}")
        await callback.message.answer("Ошибка при получении статистики A/B теста. Проверь логи.")
