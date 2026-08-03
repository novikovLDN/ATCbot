"""Управление уже отправленными рассылками: удаление и статистика A/B.

Удаление стирает сообщения у получателей по сохранённым message_id —
операция необратимая, поэтому идёт через подтверждение. Статистика A/B
показывает, какой из двух вариантов сработал лучше.
"""
import logging
import asyncio
import random
from datetime import datetime, timezone, timedelta

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
from app.services.user_subscription_links import get_user_bypass_url


admin_broadcast_manage_router = Router()
logger = logging.getLogger(__name__)


@admin_broadcast_manage_router.callback_query(F.data == "broadcast:delete_list")
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
            bot=callback.bot,
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
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot)


@admin_broadcast_manage_router.callback_query(F.data.startswith("broadcast:delete_confirm:"))
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
            bot=callback.bot,
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
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@admin_broadcast_manage_router.callback_query(F.data.startswith("broadcast:delete_exec:"))
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
            bot=callback.bot,
        )
        return

    await safe_edit_text(
        callback.message,
        f"🗑 Удаляю {len(pairs)} сообщений броадкаста #{broadcast_id}...\n\n⏳ Это может занять несколько минут. Результат будет отправлен в чат.",
        bot=callback.bot,
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


@admin_broadcast_manage_router.callback_query(F.data == "broadcast:ab_stats")
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
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), bot=callback.bot)
            return
        
        text = i18n_get_text(language, "broadcast._ab_stats_select")
        keyboard = get_ab_test_list_keyboard(ab_tests, language)
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stats_list", callback.from_user.id, None, f"Viewed {len(ab_tests)} A/B tests")
    
    except Exception as e:
        logger.exception(f"Error in callback_broadcast_ab_stats: {e}")
        await callback.message.answer(
            i18n_get_text(language, "broadcast._ab_stats_error"),
            parse_mode="HTML",
        )


@admin_broadcast_manage_router.callback_query(F.data.startswith("broadcast:ab_stat:"))
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
            await callback.message.answer("Уведомление не найдено.", parse_mode="HTML")
            return
        
        # Получаем статистику
        stats = await database.get_ab_test_stats(broadcast_id)
        
        if not stats:
            text = f"📊 A/B статистика\n\nУведомление: #{broadcast_id}\n\nНедостаточно данных для анализа."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="broadcast:ab_stats")],
            ])
            await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
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
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_ab_stat_detail", callback.from_user.id, None, f"Viewed A/B stats for broadcast {broadcast_id}")
    
    except (ValueError, IndexError) as e:
        logging.error(f"Error parsing broadcast ID: {e}")
        await callback.message.answer("Ошибка: неверный ID уведомления.", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_broadcast_ab_stat_detail: {e}")
        await callback.message.answer("Ошибка при получении статистики A/B теста. Проверь логи.", parse_mode="HTML")
