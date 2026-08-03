"""Поиск пользователя и его карточка в админке — вход в раздел «Доступ».

ЧТО ОСТАЛОСЬ ЗДЕСЬ
    Экран «Ключи», массовый перевыпуск, поиск пользователя по ID или
    username, история подписок и возврат к карточке.

КУДА УЕХАЛО ОСТАЛЬНОЕ
    access_grant.py   выдача доступа (дни, минуты, гибкий срок, год)
    access_switch.py  смена тарифа Basic ↔ Plus
    access_revoke.py  отзыв доступа, VIP, перевыпуск ключа, удаление
    _user_card.py     сама карточка пользователя (её показывают из трёх мест)

    Файл был на 2283 строки и смешивал четыре разные обязанности: найти
    человека, дать доступ, поменять тариф, отнять. Правки в одной из них
    приходилось делать посреди трёх других.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

import config
import database
import vpn_utils
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.admin import service as admin_service
from app.services.admin.exceptions import UserNotFoundError
from app.handlers.common.states import AdminGrantAccess, AdminGrantState, AdminRevokeAccess, AdminUserSearch
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_admin_user_keyboard,
    get_admin_user_keyboard_processing,
    get_admin_grant_days_keyboard,
    get_admin_grant_flex_unit_keyboard,
    get_admin_grant_flex_confirm_keyboard,
    get_admin_grant_flex_notify_keyboard,
)
from app.handlers.common.utils import safe_edit_text, get_reissue_lock, get_reissue_notification_text
from app.handlers.common.keyboards import get_reissue_notification_keyboard
# Карточка пользователя — общий экран нескольких разделов, вынесена, чтобы
# выделенные из этого файла модули не тянули друг друга по кругу.
from app.handlers.admin._user_card import _show_admin_user_card

admin_access_router = Router()
logger = logging.getLogger(__name__)



@admin_access_router.callback_query(F.data == "admin:keys")
async def callback_admin_keys(callback: CallbackQuery):
    """Раздел VPN-ключи в админ-дашборде"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Показываем меню управления ключами
        text = "🔑 Управление VPN-ключами\n\n"
        text += "Доступные действия:\n"
        text += "• Перевыпустить ключ для одного пользователя\n"
        text += "• Перевыпустить ключи для всех активных пользователей\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_for_user"), callback_data="admin:user")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.reissue_all_keys"), callback_data="admin:keys:reissue_all")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)


@admin_access_router.callback_query(F.data == "admin:keys:reissue_all")
async def callback_admin_keys_reissue_all(callback: CallbackQuery, bot: Bot):
    """Массовый перевыпуск ключей для всех активных пользователей"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer("Начинаю массовый перевыпуск...")
    
    try:
        admin_telegram_id = callback.from_user.id
        
        # Получаем все активные подписки
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            now = datetime.now(timezone.utc)
            subscriptions = await conn.fetch(
                """SELECT telegram_id, uuid, vpn_key, expires_at 
                   FROM subscriptions 
                   WHERE status = 'active' 
                   AND expires_at > $1 
                   AND uuid IS NOT NULL
                   ORDER BY telegram_id""",
                database._to_db_utc(now)
            )
        
        total_count = len(subscriptions)
        success_count = 0
        failed_count = 0
        failed_users = []
        successful_ids = []
        failed_ids = []
        
        if total_count == 0:
            await safe_edit_text(
                callback.message,
                i18n_get_text(language, "admin.no_active_subscriptions_reissue"),
                reply_markup=get_admin_back_keyboard(language)
            )
            return
        
        # Отправляем начальное сообщение
        status_text = f"🔄 Массовый перевыпуск ключей\n\nВсего пользователей: {total_count}\nОбработано: 0/{total_count}\nУспешно: 0\nОшибок: 0"
        status_message = await callback.message.edit_text(status_text, reply_markup=None, parse_mode="HTML")
        # Примечание: status_message используется для динамического обновления, защита не нужна
        
        # Обрабатываем каждую подписку
        for idx, sub_row in enumerate(subscriptions, 1):
            subscription = dict(sub_row)
            telegram_id = subscription["telegram_id"]
            
            try:
                # Перевыпускаем ключ
                result = await database.reissue_vpn_key_atomic(telegram_id, admin_telegram_id)
                new_vpn_key, old_vpn_key = result
                
                if new_vpn_key is None:
                    failed_count += 1
                    failed_users.append(telegram_id)
                    failed_ids.append(f"{telegram_id} (no key returned)")
                    logging.error(f"Failed to reissue key for user {telegram_id} in bulk operation")
                    continue
                
                success_count += 1
                successful_ids.append(telegram_id)
                
                # Отправляем уведомление пользователю
                try:
                    notify_lang = await resolve_user_language(telegram_id)
                    
                    try:
                        from vpn_utils import build_sub_url
                        _sub_url = build_sub_url(telegram_id)
                        user_text = i18n_get_text(notify_lang, "admin.reissue_user_notification", sub_url=f"<code>{_sub_url}</code>")
                    except (KeyError, TypeError):
                        # Fallback to default if localization not found
                        from vpn_utils import build_sub_url
                        user_text = get_reissue_notification_text(build_sub_url(telegram_id))
                    
                    keyboard = get_reissue_notification_keyboard(notify_lang)
                    await bot.send_message(telegram_id, user_text, reply_markup=keyboard, parse_mode="HTML")
                except Exception as e:
                    logging.warning(f"Failed to send reissue notification to user {telegram_id}: {e}")
                
                # Обновляем статус каждые 10 пользователей или в конце
                if idx % 10 == 0 or idx == total_count:
                    status_text = (
                        f"🔄 Массовый перевыпуск ключей\n\n"
                        f"Всего пользователей: {total_count}\n"
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
                failed_users.append(telegram_id)
                error_type = type(e).__name__
                failed_ids.append(f"{telegram_id} ({error_type})")
                logging.exception(f"Error reissuing key for user {telegram_id} in bulk operation: {e}")
                continue
        
        # Финальное сообщение
        final_text = (
            f"✅ Массовый перевыпуск завершён\n\n"
            f"Всего пользователей: {total_count}\n"
            f"✅ Успешно: {success_count}\n"
            f"❌ Ошибок: {failed_count}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:keys")]
        ])
        
        try:
            await status_message.edit_text(final_text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        
        # Отправляем детальный отчёт админу
        report_lines = []
        report_lines.append("🔁 Массовый перевыпуск завершён\n")
        report_lines.append(f"✅ Успешно: {len(successful_ids)}")
        
        if successful_ids:
            report_lines.append("IDs:")
            # Разбиваем на части если слишком много (Telegram limit 4096 chars)
            if len(successful_ids) <= 50:
                for uid in successful_ids:
                    report_lines.append(f"- {uid}")
            else:
                for uid in successful_ids[:50]:
                    report_lines.append(f"- {uid}")
                report_lines.append(f"... и ещё {len(successful_ids) - 50} успешных")
        
        report_lines.append("")
        report_lines.append(f"❌ Ошибки: {len(failed_ids)}")
        
        if failed_ids:
            report_lines.append("IDs:")
            # Разбиваем на части если слишком много
            if len(failed_ids) <= 50:
                for item in failed_ids:
                    report_lines.append(f"- {item}")
            else:
                for item in failed_ids[:50]:
                    report_lines.append(f"- {item}")
                report_lines.append(f"... и ещё {len(failed_ids) - 50} ошибок")
        
        report_text = "\n".join(report_lines)
        
        # Проверяем длину и разбиваем на части если нужно
        if len(report_text) > 4000:
            # Отправляем первую часть
            first_part = "\n".join(report_lines[:len(report_lines)//2])
            await callback.message.answer(first_part, parse_mode="HTML")
            # Отправляем вторую часть
            second_part = "\n".join(report_lines[len(report_lines)//2:])
            await callback.message.answer(second_part, parse_mode="HTML")
        else:
            await callback.message.answer(report_text, parse_mode="HTML")
        
        # Логируем в audit_log
        await database._log_audit_event_atomic_standalone(
            "admin_reissue_all",
            admin_telegram_id,
            None,
            f"Bulk reissue: total={total_count}, success={success_count}, failed={failed_count}"
        )
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys_reissue_all: {e}")
        await callback.message.edit_text(
            i18n_get_text(language, "admin.reissue_bulk_error", error=str(e)[:80], default=f"❌ Ошибка при массовом перевыпуске: {str(e)[:80]}"),
            reply_markup=get_admin_back_keyboard(language),
            parse_mode="HTML",
        )


@admin_access_router.callback_query(F.data.startswith("admin:keys:"))
async def callback_admin_keys_legacy(callback: CallbackQuery):
    """Раздел VPN-ключи"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        stats = await database.get_vpn_keys_stats()
        
        text = "🔑 VPN-ключи\n\n"
        text += f"Всего ключей: {stats['total']}\n"
        text += f"Использованных: {stats['used']}\n"
        
        if stats['free'] <= 5:
            text += f"⚠️ Свободных: {stats['free']}\n"
            text += "\n⚠️ ВНИМАНИЕ: Количество свободных ключей критически низкое!"
        else:
            text += f"Свободных: {stats['free']}"
        
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем просмотр статистики ключей
        await database._log_audit_event_atomic_standalone("admin_view_keys", callback.from_user.id, None, f"Admin viewed VPN keys stats: {stats['free']} free")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_keys: {e}")
        await callback.answer("Ошибка при получении статистики ключей", show_alert=True)


@admin_access_router.callback_query(F.data == "admin:user")
async def callback_admin_user(callback: CallbackQuery, state: FSMContext):
    """Раздел Пользователь - запрос Telegram ID или username"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    text = i18n_get_text(language, "admin.user_prompt_enter_id")
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
    await state.set_state(AdminUserSearch.waiting_for_user_id)
    await callback.answer()


@admin_access_router.message(AdminUserSearch.waiting_for_user_id)
async def process_admin_user_id(message: Message, state: FSMContext):
    """Обработка введённого Telegram ID или username пользователя"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"), parse_mode="HTML")
        await state.clear()
        return
    
    try:
        user_input = message.text.strip()
        
        # Определяем, является ли ввод числом (ID) или строкой (username)
        try:
            target_user_id = int(user_input)
            # Это число - ищем по ID
            user = await database.find_user_by_id_or_username(telegram_id=target_user_id)
            search_by = "ID"
            search_value = str(target_user_id)
        except ValueError:
            # Это строка - ищем по username
            username = user_input.lstrip('@')  # Убираем @, если есть
            if not username:  # Пустая строка после удаления @
                await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.", parse_mode="HTML")
                await state.clear()
                return
            username = username.lower()  # Приводим к нижнему регистру
            user = await database.find_user_by_id_or_username(username=username)
            search_by = "username"
            search_value = username
        
        # Если пользователь не найден
        if not user:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.", parse_mode="HTML")
            await state.clear()
            return
        
        # Получаем полный обзор пользователя через admin service
        try:
            overview = await admin_service.get_admin_user_overview(user["telegram_id"])
        except UserNotFoundError:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.", parse_mode="HTML")
            await state.clear()
            return
        
        # Получаем доступные действия через admin service
        actions = admin_service.get_admin_user_actions(overview)
        
        # Формируем карточку пользователя (только форматирование)
        text = "👤 Пользователь\n\n"
        text += f"Telegram ID: {overview.user['telegram_id']}\n"
        username_display = overview.user.get('username') or 'не указан'
        text += f"Username: @{username_display}\n"
        
        # Язык
        user_language = overview.user.get('language') or 'ru'
        language_display = i18n_get_text("ru", f"lang.button_{user_language}")
        text += f"Язык: {language_display}\n"
        
        # Дата регистрации
        created_at = overview.user.get('created_at')
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_str = created_at.strftime("%d.%m.%Y %H:%M")
            text += f"Дата регистрации: {created_str}\n"
        else:
            text += "Дата регистрации: —\n"
        
        text += "\n"
        
        # Информация о подписке
        if overview.subscription:
            expires_at = overview.subscription_status.expires_at
            if expires_at:
                expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            else:
                expires_str = "—"
            
            if overview.subscription_status.is_active:
                text += "Статус подписки: ✅ Активна\n"
            else:
                text += "Статус подписки: ⛔ Истекла\n"
            
            text += f"Срок действия: до {expires_str}\n"
            from vpn_utils import build_sub_url
            sub_url = build_sub_url(overview.user['telegram_id'])
            if sub_url:
                text += f"Ключ подписки:\n<code>{sub_url}</code>\n"
            else:
                text += "Ключ подписки: —\n"
        else:
            text += "Статус подписки: ❌ Нет подписки\n"
            text += "VPN-ключ: —\n"
            text += "Срок действия: —\n"

        # Статистика
        text += f"\nКоличество продлений: {overview.stats['renewals_count']}\n"
        text += f"Количество перевыпусков: {overview.stats['reissues_count']}\n"

        # Персональная скидка
        if overview.user_discount:
            discount_percent = overview.user_discount["discount_percent"]
            expires_at_discount = overview.user_discount.get("expires_at")
            if expires_at_discount:
                if isinstance(expires_at_discount, str):
                    expires_at_discount = datetime.fromisoformat(expires_at_discount.replace('Z', '+00:00'))
                expires_str = expires_at_discount.strftime("%d.%m.%Y %H:%M")
                text += f"\n🎯 Персональная скидка: {discount_percent}% (до {expires_str})\n"
            else:
                text += f"\n🎯 Персональная скидка: {discount_percent}% (бессрочно)\n"

        # VIP-статус
        if overview.is_vip:
            text += f"\n👑 VIP-статус: активен\n"

        # Traffic discount indicator (separate from subscription discount)
        if overview.user_traffic_discount:
            tdp = overview.user_traffic_discount.get("discount_percent", 0)
            tde = overview.user_traffic_discount.get("expires_at")
            if tde:
                text += f"\n🌐 Скидка на ГБ обхода: {tdp}% (до {tde.strftime('%d.%m.%Y')})\n"
            else:
                text += f"\n🌐 Скидка на ГБ обхода: {tdp}% (бессрочно)\n"

        # Используем actions для определения доступных действий
        sub_type = (overview.subscription.get("subscription_type") or "basic").strip().lower() if overview.subscription else "basic"
        if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
            sub_type = "basic"
        await message.answer(
            text,
            reply_markup=get_admin_user_keyboard(
                has_active_subscription=overview.subscription_status.is_active,
                user_id=overview.user["telegram_id"],
                has_discount=overview.user_discount is not None,
                is_vip=overview.is_vip,
                subscription_type=sub_type,
                has_traffic_discount=overview.user_traffic_discount is not None,
            ),
            parse_mode="HTML"
        )
        
        # Логируем просмотр информации о пользователе
        details = f"Admin searched by {search_by}: {search_value}, found user {user['telegram_id']}"
        await database._log_audit_event_atomic_standalone("admin_view_user", message.from_user.id, user["telegram_id"], details)
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_user_id: {e}")
        await message.answer("Ошибка при получении информации о пользователе. Проверь логи.", parse_mode="HTML")
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:show_user:"))
async def callback_admin_show_user(callback: CallbackQuery):
    """Вернуться к карточке пользователя (например после отмены смены тарифа)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[2])
        await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
    except (ValueError, IndexError) as e:
        logger.warning(f"Invalid admin:show_user callback: {callback.data}, error={e}")
        await callback.answer("Ошибка", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin:user_history:"))
async def callback_admin_user_history(callback: CallbackQuery):
    """История подписок пользователя (админ)"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        # Получаем user_id из callback_data
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return
    
    try:
        # Получаем историю подписок
        history = await database.get_subscription_history(target_user_id, limit=10)
        
        if not history:
            text = "🧾 История подписок\n\nИстория подписок пуста."
            await callback.message.answer(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
            await callback.answer()
            return
        
        # Формируем текст истории
        text = "🧾 История подписок\n\n"
        
        action_type_map = {
            "purchase": "Покупка",
            "renewal": "Продление",
            "reissue": "Выдача нового ключа",
            "manual_reissue": "Перевыпуск ключа",
        }
        
        for record in history:
            start_date = record["start_date"]
            if isinstance(start_date, str):
                start_date = datetime.fromisoformat(start_date)
            start_str = start_date.strftime("%d.%m.%Y")
            
            end_date = record["end_date"]
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            end_str = end_date.strftime("%d.%m.%Y")
            
            action_type = record["action_type"]
            action_text = action_type_map.get(action_type, action_type)
            
            text += f"• {start_str} — {action_text}\n"
            
            # Для purchase и reissue показываем ключ
            if action_type in ["purchase", "reissue", "manual_reissue"]:
                text += f"  Ключ: {record['vpn_key']}\n"
            
            text += f"  До: {end_str}\n\n"
        
        await callback.message.answer(text, reply_markup=get_admin_back_keyboard(language), parse_mode="HTML")
        await callback.answer()
        
        # Логируем просмотр истории
        await database._log_audit_event_atomic_standalone("admin_view_user_history", callback.from_user.id, target_user_id, f"Admin viewed subscription history for user {target_user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_user_history: {e}")
        await callback.answer("Ошибка при получении истории подписок", show_alert=True)


# Unit labels for flexible grant (Russian)


# ----- Admin switch tariff (Basic ↔ Plus) -----


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================


# ====================================================================================
# ADMIN: DELETE USER FROM DB
# ====================================================================================


@admin_access_router.callback_query(F.data.startswith("admin:user_back:"))
async def callback_admin_user_back(callback: CallbackQuery):
    """Возврат к карточке пользователя после отмены удаления"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer()

    try:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Ошибка формата команды", show_alert=True)
            return

        user_id = int(parts[2])
        await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
    except ValueError:
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in callback_admin_user_back: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)
