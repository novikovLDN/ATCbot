"""Перевыпуск VPN-ключей: одной подписки и всех активных сразу.

ЧТО ЗДЕСЬ
    Три экрана: перевыпуск по subscription_id, подтверждение массового
    перевыпуска и сам массовый прогон с живым счётчиком прогресса.

ПОЧЕМУ ОТДЕЛЬНО
    Это единственная админская операция в чате, которая идёт минутами и
    правит боевые ключи пачкой. Держать её рядом с экраном входа значит
    править вход, задевая массовую операцию.

ВНИМАНИЕ: РАЗДЕЛ СЕЙЧАС НЕДОСТИЖИМ ИЗ ИНТЕРФЕЙСА
    Ни одна клавиатура бота не отдаёт callback_data admin:reissue_key:,
    admin:reissue_all_active и admin:reissue_all_active_go — кнопки жили в
    удалённом разделе «Ключи». Код перенесён как есть и не выброшен,
    потому что решение об удалении раздела принимает владелец, а не
    рефакторинг. Учитывайте это, прежде чем чинить здесь что-то «по факту
    бага от пользователя»: пользователь сюда не попадает.

    Кнопки «назад» и «отмена» вели на admin:keys — адрес того самого
    удалённого раздела, то есть тоже в никуда. Переведены на admin:main:
    если раздел когда-нибудь снова подключат к меню, выход из него уже
    будет работать, а не молчать.

ЧТО ЛЕГКО СЛОМАТЬ
    Массовый прогон идёт ИТЕРАТИВНО с паузой 1.5 секунды между ключами —
    это защита от лимитов панели. Замена цикла на asyncio.gather выглядит
    как ускорение, а на деле кладёт Remnawave и оставляет часть подписок
    с перевыпущенным ключом, а часть — со старым.

    Правка сообщения о прогрессе завёрнута в отлов «message is not
    modified»: без него Telegram уронит весь прогон на первом же
    неизменившемся тексте.
"""
import logging
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_reissue_router = Router()
logger = logging.getLogger(__name__)


@admin_reissue_router.callback_query(F.data.startswith("admin:reissue_key:"))
async def callback_admin_reissue_key(callback: CallbackQuery, bot: Bot):
    """Перевыпуск ключа для одной подписки (по subscription_id)"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем subscription_id из callback_data
        subscription_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    admin_telegram_id = callback.from_user.id
    
    try:
        import vpn_utils
        
        # Проверяем, что подписка активна и получаем данные
        subscription = await database.get_active_subscription(subscription_id)
        if not subscription:
            await callback.answer("Подписка не найдена или не активна", show_alert=True)
            return
        
        telegram_id = subscription.get("telegram_id")
        old_uuid = subscription.get("uuid")
        
        if not old_uuid:
            await callback.answer("У подписки нет UUID для перевыпуска", show_alert=True)
            return
        
        # Перевыпускаем ключ
        await callback.answer("Перевыпускаю ключ...")
        
        try:
            new_uuid, vless_url = await database.reissue_subscription_key(subscription_id)
        except ValueError as e:
            await callback.answer(f"Ошибка: {str(e)}", show_alert=True)
            return
        except Exception as e:
            logging.exception(f"Failed to reissue key for subscription {subscription_id}: {e}")
            await callback.answer(f"Ошибка при перевыпуске ключа: {str(e)}", show_alert=True)
            return
        
        # Показываем админу результат
        user = await database.get_user(telegram_id)
        user_lang = await resolve_user_language(telegram_id)
        username = user.get("username", i18n_get_text(user_lang, "common.username_not_set")) if user else i18n_get_text(user_lang, "common.username_not_set")
        
        expires_at = subscription["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
        
        text = "✅ Ключ успешно перевыпущен\n\n"
        text += f"Подписка ID: {subscription_id}\n"
        text += f"Пользователь: @{username} ({telegram_id})\n"
        text += f"Срок действия: до {expires_str}\n\n"
        text += f"Новый VPN-ключ:\n<code>{vless_url}</code>"
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        await callback.answer("Ключ успешно перевыпущен")
        
        # Логируем в audit_log
        await database._log_audit_event_atomic_standalone(
            "admin_reissue_key",
            admin_telegram_id,
            telegram_id,
            f"Reissued key for subscription_id={subscription_id}, old_uuid={old_uuid[:8]}..., new_uuid={new_uuid[:8]}..."
        )
        
        # НЕ отправляем уведомление пользователю автоматически (согласно требованиям)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_reissue_key: {e}")
        await callback.answer("Ошибка при перевыпуске ключа", show_alert=True)


@admin_reissue_router.callback_query(F.data == "admin:reissue_all_active")
async def callback_admin_reissue_all_active_confirm(callback: CallbackQuery):
    """Подтверждение массового перевыпуска"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)
    text = "⚠️ Массовый перевыпуск ключей\n\nВсе активные VPN-ключи будут перевыпущены.\nПродолжить?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, перевыпустить", callback_data="admin:reissue_all_active_go"),
            # Было admin:keys — адрес удалённого раздела «Ключи», у него нет
            # обработчика. Отмена массового перевыпуска обязана работать
            # ВСЕГДА: молчащая «Отмена» на экране подтверждения читается как
            # «не отменилось», и второе нажатие уходит в «Да».
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin:main"),
        ]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@admin_reissue_router.callback_query(F.data == "admin:reissue_all_active_go")
async def callback_admin_reissue_all_active(callback: CallbackQuery, bot: Bot):
    """Массовый перевыпуск ключей для всех активных подписок"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer("Начинаю массовый перевыпуск...")
    language = await resolve_user_language(callback.from_user.id)

    try:
        admin_telegram_id = callback.from_user.id
        
        # Получаем все активные подписки
        subscriptions = await database.get_all_active_subscriptions()
        
        total_count = len(subscriptions)
        success_count = 0
        failed_count = 0
        failed_subscriptions = []
        
        if total_count == 0:
            await safe_edit_text(
                callback.message,
                i18n_get_text(language, "admin.no_active_subscriptions_reissue"),
                reply_markup=get_admin_back_keyboard(language)
            )
            return
        
        # Отправляем начальное сообщение
        status_text = f"🔄 Массовый перевыпуск ключей\n\nВсего подписок: {total_count}\nОбработано: 0/{total_count}\nУспешно: 0\nОшибок: 0"
        status_message = await callback.message.edit_text(status_text, reply_markup=None, parse_mode="HTML")
        # Примечание: status_message используется для динамического обновления, защита не нужна
        
        # Обрабатываем каждую подписку ИТЕРАТИВНО (НЕ параллельно)
        for idx, subscription in enumerate(subscriptions, 1):
            subscription_id = subscription.get("id")
            telegram_id = subscription.get("telegram_id")
            old_uuid = subscription.get("uuid")
            
            if not subscription_id or not old_uuid:
                failed_count += 1
                failed_subscriptions.append(subscription_id or telegram_id)
                continue
            
            try:
                # Перевыпускаем ключ (returns new_uuid, vless_url — API is source of truth)
                await database.reissue_subscription_key(subscription_id)
                success_count += 1
                
                # Обновляем статус каждые 10 подписок или в конце
                if idx % 10 == 0 or idx == total_count:
                    status_text = (
                        f"🔄 Массовый перевыпуск ключей\n\n"
                        f"Всего подписок: {total_count}\n"
                        f"Обработано: {idx}/{total_count}\n"
                        f"✅ Успешно: {success_count}\n"
                        f"❌ Ошибок: {failed_count}"
                    )
                    try:
                        try:
                            await status_message.edit_text(status_text, parse_mode="HTML")
                        except TelegramBadRequest as e:
                            if "message is not modified" not in str(e):
                                raise
                    except Exception:
                        pass
                
                # Rate limiting: 1-2 секунды между запросами
                if idx < total_count:
                    import asyncio
                    await asyncio.sleep(1.5)
                    
            except Exception as e:
                failed_count += 1
                failed_subscriptions.append(subscription_id)
                logging.exception(f"Error reissuing key for subscription {subscription_id} (user {telegram_id}) in bulk operation: {e}")
                continue
        
        # Финальное сообщение
        final_text = (
            f"✅ Массовый перевыпуск завершён\n\n"
            f"Всего подписок: {total_count}\n"
            f"✅ Успешно: {success_count}\n"
            f"❌ Ошибок: {failed_count}"
        )
        
        if failed_subscriptions:
            failed_list = ", ".join(map(str, failed_subscriptions[:10]))
            if len(failed_subscriptions) > 10:
                failed_list += f" и ещё {len(failed_subscriptions) - 10}"
            final_text += f"\n\nОшибки у подписок: {failed_list}"
        
        # admin:keys — раздел, удалённый вместе с 23 другими админскими
        # модулями; обработчика под него нет. Возвращаем в admin:main.
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        try:
            await status_message.edit_text(final_text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        
        # Логируем в audit_log
        await database._log_audit_event_atomic_standalone(
            "admin_reissue_all_active",
            admin_telegram_id,
            None,
            f"Bulk reissue: total={total_count}, success={success_count}, failed={failed_count}"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_reissue_all_active: {e}")
        await callback.message.edit_text(
            i18n_get_text(language, "admin.reissue_bulk_error", error=str(e)[:80], default=f"❌ Ошибка при массовом перевыпуске: {str(e)[:80]}"),
            reply_markup=get_admin_back_keyboard(language),
            parse_mode="HTML",
        )
