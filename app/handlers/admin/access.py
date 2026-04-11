"""
Admin access management handlers: grant/revoke access, keys management, VIP, user search.
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
        status_message = await callback.message.edit_text(status_text, reply_markup=None)
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
                            await status_message.edit_text(status_text)
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
            await status_message.edit_text(final_text, reply_markup=keyboard)
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
            await callback.message.answer(first_part)
            # Отправляем вторую часть
            second_part = "\n".join(report_lines[len(report_lines)//2:])
            await callback.message.answer(second_part)
        else:
            await callback.message.answer(report_text)
        
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
            reply_markup=get_admin_back_keyboard(language)
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
    await callback.message.edit_text(text, reply_markup=get_admin_back_keyboard(language))
    await state.set_state(AdminUserSearch.waiting_for_user_id)
    await callback.answer()


@admin_access_router.message(AdminUserSearch.waiting_for_user_id)
async def process_admin_user_id(message: Message, state: FSMContext):
    """Обработка введённого Telegram ID или username пользователя"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "admin.access_denied"))
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
                await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
                await state.clear()
                return
            username = username.lower()  # Приводим к нижнему регистру
            user = await database.find_user_by_id_or_username(username=username)
            search_by = "username"
            search_value = username
        
        # Если пользователь не найден
        if not user:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
            await state.clear()
            return
        
        # Получаем полный обзор пользователя через admin service
        try:
            overview = await admin_service.get_admin_user_overview(user["telegram_id"])
        except UserNotFoundError:
            await message.answer("Пользователь не найден.\nПроверьте Telegram ID или username.")
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
            vpn_key = overview.subscription.get('vpn_key', '—')
            if vpn_key and vpn_key != '—':
                text += f"VPN-ключ:\n<code>{vpn_key}</code>\n"
            else:
                text += "VPN-ключ: —\n"
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
            ),
            parse_mode="HTML"
        )
        
        # Логируем просмотр информации о пользователе
        details = f"Admin searched by {search_by}: {search_value}, found user {user['telegram_id']}"
        await database._log_audit_event_atomic_standalone("admin_view_user", message.from_user.id, user["telegram_id"], details)
        
        await state.clear()
        
    except Exception as e:
        logging.exception(f"Error in process_admin_user_id: {e}")
        await message.answer("Ошибка при получении информации о пользователе. Проверь логи.")
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
            await callback.message.answer(text, reply_markup=get_admin_back_keyboard(language))
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
        
        await callback.message.answer(text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем просмотр истории
        await database._log_audit_event_atomic_standalone("admin_view_user_history", callback.from_user.id, target_user_id, f"Admin viewed subscription history for user {target_user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_user_history: {e}")
        await callback.answer("Ошибка при получении истории подписок", show_alert=True)


# Unit labels for flexible grant (Russian)
GRANT_FLEX_UNIT_LABELS = {"minutes": "минут", "hours": "часов", "days": "дней", "months": "месяцев"}


def _grant_flex_calculated_days(amount: float, unit: str) -> float:
    """Convert amount + unit to days. minutes → N/1440, hours → N/24, days → N, months → N*30."""
    if unit == "minutes":
        return amount / 1440.0
    if unit == "hours":
        return amount / 24.0
    if unit == "days":
        return amount
    if unit == "months":
        return amount * 30.0
    return amount


@admin_access_router.callback_query(F.data.startswith("admin_grant_basic:"))
async def callback_admin_grant_basic(callback: CallbackQuery, state: FSMContext):
    """Entry: Admin selects «Выдать Basic». Ask for duration number, then unit."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        await state.update_data(grant_user_id=user_id, grant_tariff="basic")
        await state.set_state(AdminGrantState.waiting_amount)
        await callback.message.edit_text("Введите срок действия (число):")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_basic: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin_grant_plus:"))
async def callback_admin_grant_plus(callback: CallbackQuery, state: FSMContext):
    """Entry: Admin selects «Выдать Plus». Ask for duration number, then unit."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        await state.update_data(grant_user_id=user_id, grant_tariff="plus")
        await state.set_state(AdminGrantState.waiting_amount)
        await callback.message.edit_text("Введите срок действия (число):")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_plus: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_access_router.message(StateFilter(AdminGrantState.waiting_amount), F.text)
async def process_admin_grant_flex_amount(message: Message, state: FSMContext):
    """After admin entered number, show unit selection keyboard."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await state.clear()
        return
    try:
        value = float(message.text.strip().replace(",", "."))
        if value <= 0:
            await message.answer("Введите положительное число.")
            return
        await state.update_data(grant_amount=value)
        await state.set_state(AdminGrantState.waiting_unit)
        language = await resolve_user_language(message.from_user.id)
        await message.answer("Выберите единицу срока:", reply_markup=get_admin_grant_flex_unit_keyboard(language))
    except ValueError:
        await message.answer("Введите число (например: 30).")
    except Exception as e:
        logger.exception(f"Error in process_admin_grant_flex_amount: {e}")
        await message.answer("Ошибка.")
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_flex_unit:"), StateFilter(AdminGrantState.waiting_unit))
async def callback_admin_grant_flex_unit(callback: CallbackQuery, state: FSMContext):
    """Admin selected unit → show confirmation (N unit_label, total minutes/days)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        # callback_data format: "admin:grant_flex_unit:minutes" → parts[2] = unit
        parts = callback.data.split(":")
        unit = parts[2] if len(parts) > 2 else ""
        if unit not in GRANT_FLEX_UNIT_LABELS:
            await callback.answer("Неизвестная единица", show_alert=True)
            return
        data = await state.get_data()
        amount = data.get("grant_amount")
        user_id = data.get("grant_user_id")
        tariff = data.get("grant_tariff", "basic")
        if amount is None or user_id is None:
            await callback.answer("Данные сессии потеряны. Начните заново.", show_alert=True)
            await state.clear()
            return
        calculated_days = _grant_flex_calculated_days(amount, unit)
        total_minutes = calculated_days * 24 * 60
        total_days = calculated_days
        unit_label = GRANT_FLEX_UNIT_LABELS[unit]
        tariff_label = "Basic" if tariff == "basic" else "Plus"
        await state.update_data(
            grant_unit=unit,
            grant_unit_label=unit_label,
            grant_calculated_days=calculated_days,
        )
        await state.set_state(AdminGrantState.waiting_confirm)
        text = (
            f"Выдать {tariff_label} на {int(amount) if amount == int(amount) else amount} {unit_label} пользователю {user_id}?\n"
            f"Это составит примерно {int(total_minutes)} минут / {total_days:.1f} дней.\n\n"
            "✅ Подтвердить   ❌ Отмена"
        )
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=get_admin_grant_flex_confirm_keyboard(language))
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_flex_unit: {e}")
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data == "admin:grant_flex_confirm", StateFilter(AdminGrantState.waiting_confirm))
async def callback_admin_grant_flex_confirm(callback: CallbackQuery, state: FSMContext):
    """After confirm: show notify user choice, then execute grant in next step."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        data = await state.get_data()
        if not all([data.get("grant_user_id"), data.get("grant_tariff"), data.get("grant_calculated_days") is not None]):
            await callback.answer("Данные сессии потеряны.", show_alert=True)
            await state.clear()
            return
        await state.set_state(AdminGrantState.waiting_notify)
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(
            "Уведомить пользователя о выдаче доступа?",
            reply_markup=get_admin_grant_flex_notify_keyboard(language),
        )
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_flex_confirm: {e}")
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_flex_notify:"), StateFilter(AdminGrantState.waiting_notify))
async def callback_admin_grant_flex_notify(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Execute grant; if notify=yes send user message, then show admin confirmation."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        notify = callback.data.split(":")[-1].lower() == "yes"
        data = await state.get_data()
        user_id = data.get("grant_user_id")
        tariff = data.get("grant_tariff", "basic")
        amount = data.get("grant_amount")
        unit_label = data.get("grant_unit_label", "")
        calculated_days = data.get("grant_calculated_days", 0)
        grant_unit = data.get("grant_unit", "days")
        if not all([user_id, tariff, calculated_days is not None]):
            await callback.answer("Данные сессии потеряны.", show_alert=True)
            await state.clear()
            return
        if grant_unit in ("minutes", "hours") and calculated_days < 1:
            total_minutes = max(1, int(round(calculated_days * 1440)))
            expires_at, _ = await database.admin_grant_access_minutes_atomic(
                telegram_id=user_id,
                minutes=total_minutes,
                admin_telegram_id=callback.from_user.id,
            )
        else:
            days_int = max(1, int(round(calculated_days)))
            expires_at, _ = await database.admin_grant_access_atomic(
                telegram_id=user_id,
                days=days_int,
                admin_telegram_id=callback.from_user.id,
                tariff=tariff,
            )
        expires_date = expires_at.strftime("%d.%m.%Y")
        tariff_label = "Basic" if tariff == "basic" else "Plus"

        # Fire-and-forget: create/renew Remnawave bypass
        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            if tariff in ("basic", "plus"):
                renew_remnawave_user_bg(user_id, tariff, expires_at, period_days=days_int)
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_ADMIN_GRANT_FAIL: tg=%s %s", user_id, rmn_err)

        # Site sync (fire-and-forget)
        try:
            from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
            if _ss():
                sync_days = max(1, int(round(calculated_days)))
                asyncio.ensure_future(notify_subscription_extend(user_id, sync_days, tariff))
                asyncio.ensure_future(sync_balance(user_id))
        except Exception:
            pass

        text_admin = (
            f"✅ Выдан {tariff_label} доступ\n"
            f"👤 Пользователь: {user_id}\n"
            f"⏱ Срок: {int(amount) if amount == int(amount) else amount} {unit_label}\n"
            f"📅 До: {expires_date}"
        )
        if notify:
            try:
                await bot.send_message(
                    user_id,
                    f"🎁 Вам выдан доступ {tariff_label}\n📅 Действует до: {expires_date}",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.exception(f"Error sending grant notification to user {user_id}: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await safe_edit_text(callback.message, text_admin, reply_markup=get_admin_back_keyboard(language))
        await database._log_audit_event_atomic_standalone(
            "admin_grant_access_flex",
            callback.from_user.id,
            user_id,
            f"Admin granted {tariff_label} {amount} {unit_label}, notify={notify}, expires={expires_date}",
        )
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_flex_notify: {e}")
        await callback.answer("Ошибка выдачи доступа", show_alert=True)
    await state.clear()


@admin_access_router.callback_query(F.data == "admin:grant_flex_cancel")
async def callback_admin_grant_flex_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel flexible grant flow (from unit, confirm or notify step)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    await callback.answer()
    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    await safe_edit_text(callback.message, "Отменено.", reply_markup=get_admin_back_keyboard(language))


# ----- Admin switch tariff (Basic ↔ Plus) -----

def _admin_switch_confirm_keyboard(user_id: int, tariff: str, language: str = "ru"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_switch_confirm:{tariff}:{user_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin:show_user:{user_id}"),
        ],
    ])


def _admin_switch_notify_keyboard(user_id: int, tariff: str, language: str = "ru"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"admin_switch_notify:yes:{user_id}:{tariff}")],
        [InlineKeyboardButton(text="🔕 Нет", callback_data=f"admin_switch_notify:no:{user_id}:{tariff}")],
    ])


@admin_access_router.callback_query(F.data.startswith("admin_switch_plus:"))
async def callback_admin_switch_plus(callback: CallbackQuery):
    """Перевести пользователя с Basic на Plus — показать подтверждение."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text("ru", "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        text = (
            f"Перевести пользователя {user_id} с Basic на Plus?\n"
            "📅 Срок подписки не изменится.\n\n"
            "✅ Подтвердить   ❌ Отмена"
        )
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=_admin_switch_confirm_keyboard(user_id, "plus", language))
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_plus: {e}")
        await callback.answer("Ошибка", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin_switch_basic:"))
async def callback_admin_switch_basic(callback: CallbackQuery):
    """Перевести пользователя с Plus на Basic — показать подтверждение."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text("ru", "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        text = (
            f"Перевести пользователя {user_id} с Plus на Basic?\n"
            "📅 Срок подписки не изменится.\n"
            "⚠️ Ключ будет ротирован с выделенного сервера на базовый.\n\n"
            "✅ Подтвердить   ❌ Отмена"
        )
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=_admin_switch_confirm_keyboard(user_id, "basic", language))
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_basic: {e}")
        await callback.answer("Ошибка", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin_switch_confirm:"))
async def callback_admin_switch_confirm(callback: CallbackQuery, bot: Bot):
    """Выполнить смену тарифа: VPN API + БД, затем спросить про уведомление."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text("ru", "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        parts = callback.data.split(":")
        tariff = parts[1]
        user_id = int(parts[2])
        subscription = await database.get_subscription(user_id)
        if not subscription or not subscription.get("uuid"):
            await callback.answer("Нет активной подписки или UUID", show_alert=True)
            return
        uuid_val = subscription["uuid"].strip()
        language = await resolve_user_language(callback.from_user.id)
        if tariff == "plus":
            upgrade_result = await vpn_utils.upgrade_vless_user(uuid_val)
            vpn_key_plus = upgrade_result.get("vless_url_plus")
            await database.admin_switch_tariff(user_id, "plus", vpn_key_plus=vpn_key_plus)
            await database._log_audit_event_atomic_standalone("ADMIN_SWITCH_TO_PLUS", callback.from_user.id, user_id, "Tariff switched to Plus")
        else:
            await vpn_utils.remove_plus_inbound(uuid_val)
            await database.admin_switch_tariff(user_id, "basic", vpn_key_plus=None)
            await database._log_audit_event_atomic_standalone("ADMIN_SWITCH_TO_BASIC", callback.from_user.id, user_id, "Tariff switched to Basic")
        tariff_label = "Plus" if tariff == "plus" else "Basic"
        text = f"✅ Готово. Тариф изменён на {tariff_label}\n\nУведомить пользователя?"
        await callback.message.edit_text(text, reply_markup=_admin_switch_notify_keyboard(user_id, tariff, language))
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_confirm: {e}")
        await callback.answer("Ошибка смены тарифа", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin_switch_notify:"))
async def callback_admin_switch_notify(callback: CallbackQuery, bot: Bot):
    """После смены тарифа: уведомить пользователя или нет, затем вернуть к карточке."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    await callback.answer()
    try:
        parts = callback.data.split(":")
        # admin_switch_notify:yes:{user_id}:{tariff} → parts[1]=yes/no, parts[2]=user_id, parts[3]=tariff
        notify_yes = parts[1].lower() == "yes"
        user_id = int(parts[2])
        tariff = parts[3]
        tariff_label = "Plus" if tariff == "plus" else "Basic"
        if notify_yes:
            sub = await database.get_subscription(user_id)
            msg = f"🔄 Ваш тариф изменён на {tariff_label}\n📅 Срок подписки не изменился."
            try:
                await bot.send_message(user_id, msg, parse_mode="HTML")
                if tariff == "plus" and sub and sub.get("vpn_key_plus"):
                    await bot.send_message(user_id, f"<code>{sub['vpn_key_plus']}</code>", parse_mode="HTML")
            except Exception as e:
                logger.exception(f"Error sending switch notify to user {user_id}: {e}")
        overview = await admin_service.get_admin_user_overview(user_id)
        sub_type = (overview.subscription.get("subscription_type") or "basic").strip().lower() if overview.subscription else "basic"
        if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
            sub_type = "basic"
        keyboard = get_admin_user_keyboard(
            has_active_subscription=overview.subscription_status.is_active,
            user_id=user_id,
            has_discount=overview.user_discount is not None,
            is_vip=overview.is_vip,
            subscription_type=sub_type,
            language=await resolve_user_language(callback.from_user.id),
        )
        text = f"✅ Тариф изменён на {tariff_label}."
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_notify: {e}")
        await callback.answer("Ошибка", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin:grant:") & ~F.data.startswith("admin:grant_custom:") & ~F.data.startswith("admin:grant_days:") & ~F.data.startswith("admin:grant_minutes:") & ~F.data.startswith("admin:grant_1_year:") & ~F.data.startswith("admin:grant_unit:") & ~F.data.startswith("admin:grant:notify:") & ~F.data.startswith("admin:notify:") & ~F.data.startswith("admin:grant_flex"))
async def callback_admin_grant(callback: CallbackQuery, state: FSMContext):
    """
    Entry point: Admin selects "Выдать доступ" for a user.
    Shows quick action buttons (1/7/14 days, 1 year, 10 minutes, custom).
    """
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Сохраняем user_id в состоянии
        await state.update_data(user_id=user_id)
        
        # Показываем клавиатуру выбора срока
        text = "Выберите срок доступа:"
        await callback.message.edit_text(text, reply_markup=get_admin_grant_days_keyboard(user_id))
        await state.set_state(AdminGrantAccess.waiting_for_days)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_days set for user {user_id}")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


async def _do_grant_1_year_setup(callback: CallbackQuery, state: FSMContext, language: str) -> None:
    """Shared logic: parse user_id, update FSM, show notify choice. Used by primary and fallback."""
    parts = callback.data.split(":")
    user_id = int(parts[2])
    await state.update_data(user_id=user_id, days=365, action_type="grant_1_year")
    text = "✅ Выдать доступ на 1 год\n\nУведомить пользователя?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:notify:yes")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:notify:no")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(AdminGrantAccess.waiting_for_notify)


@admin_access_router.callback_query(F.data.startswith("admin:grant_days:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_days(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    4️⃣ NOTIFY USER LOGIC (GRANT + REVOKE)
    
    Quick action: Grant access for N days.
    Ask for notify_user choice before executing.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        days = int(parts[3])
        
        # Save user_id and days in FSM, ask for notify choice
        await state.update_data(user_id=user_id, days=days, action_type="grant_days")
        
        text = f"✅ Выдать доступ на {days} дней\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set for quick action (days={days})")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_days: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_minutes:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_minutes(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    1️⃣ FIX CONTRACT MISUSE: Execute grant BEFORE showing notify buttons.
    2️⃣ STORE NOTIFY CONTEXT EXPLICITLY: Encode data in callback_data.
    
    Quick action: Grant access for N minutes, then ask for notify choice.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        minutes = int(parts[3])
        
        # 1️⃣ FIX CONTRACT MISUSE: Execute grant FIRST (treat as side-effect only)
        try:
            await database.admin_grant_access_minutes_atomic(
                telegram_id=user_id,
                minutes=minutes,
                admin_telegram_id=callback.from_user.id
            )
            # If no exception → grant is successful (don't check return value)
        except Exception as e:
            logger.exception(f"CRITICAL: Failed to grant admin access (minutes) for user {user_id}, minutes={minutes}, admin={callback.from_user.id}: {e}")
            text = f"❌ Ошибка выдачи доступа: {str(e)[:100]}"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Ошибка создания ключа", show_alert=True)
            await state.clear()
            return
        
        # 2️⃣ STORE NOTIFY CONTEXT EXPLICITLY: Encode all data in callback_data
        # Format: admin:notify:yes:minutes:<user_id>:<minutes>
        text = f"✅ Доступ выдан на {minutes} минут\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data=f"admin:notify:yes:minutes:{user_id}:{minutes}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data=f"admin:notify:no:minutes:{user_id}:{minutes}")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        
        # Clear FSM - notify handlers will work without FSM
        await state.clear()
        
        logger.debug(f"Grant executed for user {user_id}, minutes={minutes}, waiting for notify choice")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_minutes: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_1_year:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_1_year(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    4️⃣ NOTIFY USER LOGIC (GRANT + REVOKE)
    
    Quick action: Grant access for 1 year (365 days).
    Ask for notify_user choice before executing.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        await _do_grant_1_year_setup(callback, state, language)
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set for quick action (1 year)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_1_year: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_1_year:"))
async def callback_admin_grant_1_year_fallback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    FSM fallback: when FSM cleared, grant_1_year callback would be Unhandled.
    Runs when primary (StateFilter waiting_for_days) does not match.
    Re-establishes notify choice flow statelessly from callback_data.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    logger.warning(
        "ADMIN_FSM_FALLBACK_EXECUTED "
        f"user={callback.from_user.id} "
        f"callback={callback.data}"
    )
    await callback.answer()
    
    try:
        await _do_grant_1_year_setup(callback, state, language)
        logger.debug("FSM: grant_1_year fallback - notify choice restored")
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_1_year_fallback: {e}")
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_custom:"), StateFilter(AdminGrantAccess.waiting_for_days))
async def callback_admin_grant_custom_from_days(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Start custom grant flow from waiting_for_days state.
    This is the handler that was missing - works when FSM is in waiting_for_days.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(user_id=user_id)
        
        text = "⚙️ Настройка доступа\n\nВыберите единицу времени:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_minutes"), callback_data="admin:grant_unit:minutes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_hours"), callback_data="admin:grant_unit:hours")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_days"), callback_data="admin:grant_unit:days")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_unit)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_unit set for user {user_id} (from waiting_for_days state)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_custom_from_days: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_custom:"))
async def callback_admin_grant_custom(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Start custom grant flow - select duration unit first.
    Fallback handler (no state filter) - works from any state.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        user_id = int(callback.data.split(":")[2])
        await state.update_data(user_id=user_id)
        
        text = "⚙️ Настройка доступа\n\nВыберите единицу времени:"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_minutes"), callback_data="admin:grant_unit:minutes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_hours"), callback_data="admin:grant_unit:hours")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.grant_unit_days"), callback_data="admin:grant_unit:days")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data=f"admin:grant:{user_id}")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_unit)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_unit set for user {user_id} (from any state)")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_custom: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant_unit:"), StateFilter(AdminGrantAccess.waiting_for_unit))
async def callback_admin_grant_unit(callback: CallbackQuery, state: FSMContext):
    """
    2️⃣ CALLBACK HANDLERS — CRITICAL FIX
    
    Process duration unit selection, move to value input.
    Handler works ONLY in state waiting_for_unit.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        unit = callback.data.split(":")[2]  # minutes, hours, days (fixed: was [3], now [2] for admin:grant_unit:minutes)
        await state.update_data(duration_unit=unit)
        
        unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(unit, unit)
        text = f"⚙️ Настройка доступа\n\nЕдиница: {unit_text}\n\nВведите количество (положительное число):"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_value)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_value set, unit={unit}")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_unit: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.message(StateFilter(AdminGrantAccess.waiting_for_value))
async def process_admin_grant_value(message: Message, state: FSMContext):
    """
    PART 1: Process duration value input, move to notify choice.
    """
    language = await resolve_user_language(message.from_user.id)
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        await message.answer(i18n_get_text(language, "admin.access_denied"))
        await state.clear()
        return
    
    try:
        value = int(message.text.strip())
        if value <= 0:
            await message.answer("❌ Введите положительное число")
            return
        
        data = await state.get_data()
        unit = data.get("duration_unit")
        unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(unit, unit)
        
        await state.update_data(duration_value=value)
        
        text = f"⚙️ Настройка доступа\n\nПродолжительность: {value} {unit_text}\n\nУведомить пользователя?"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:grant:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:grant:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:main")],
        ])
        await message.answer(text, reply_markup=keyboard)
        await state.set_state(AdminGrantAccess.waiting_for_notify)
        
        logger.debug(f"FSM: AdminGrantAccess.waiting_for_notify set, value={value}, unit={unit}")
        
    except ValueError:
        await message.answer("❌ Введите число")
    except Exception as e:
        logger.exception(f"Error in process_admin_grant_value: {e}")
        await message.answer("Ошибка")
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:grant:notify:"), StateFilter(AdminGrantAccess.waiting_for_notify))
async def callback_admin_grant_notify(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    PART 1: Execute grant access with notify_user choice.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify_user = callback.data.split(":")[3] == "yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        duration_value = data.get("duration_value")
        duration_unit = data.get("duration_unit")
        
        if not all([user_id, duration_value, duration_unit]):
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        # PART 3: Convert duration to timedelta
        from datetime import timedelta
        if duration_unit == "minutes":
            duration = timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            duration = timedelta(hours=duration_value)
        else:  # days
            duration = timedelta(days=duration_value)
        
        logger.debug(f"FSM: Executing grant for user {user_id}, duration={duration}, notify_user={notify_user}")
        
        # PART 3: Execute grant_access
        try:
            result = await database.grant_access(
                telegram_id=user_id,
                duration=duration,
                source="admin",
                admin_telegram_id=callback.from_user.id,
                admin_grant_days=None  # Custom duration
            )
            
            expires_at = result["subscription_end"]
            vpn_key = result.get("vless_url") or result.get("uuid", "")
            
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            unit_text = {"minutes": "минут", "hours": "часов", "days": "дней"}.get(duration_unit, duration_unit)

            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
                if _ss():
                    sync_days = duration_value if duration_unit == "days" else (duration_value // 60 // 24 or 1)
                    asyncio.ensure_future(notify_subscription_extend(user_id, sync_days, "basic"))
                    asyncio.ensure_future(sync_balance(user_id))
            except Exception:
                pass

            text = f"✅ Доступ выдан на {duration_value} {unit_text}"
            if notify_user:
                text += "\nПользователь уведомлён."
            else:
                text += "\nДействие выполнено без уведомления."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # PART 6: Notify user if flag is True
            if notify_user and vpn_key:
                import admin_notifications
                vpn_key_html = f"<code>{vpn_key}</code>" if vpn_key else "⏳ Активация в процессе"
                user_text = f"✅ Вам выдан доступ на {duration_value} {unit_text}\n\nКлюч: {vpn_key_html}\nДействителен до: {expires_str}"
                # Use unified notification service
                await admin_notifications.send_user_notification(
                    bot=bot,
                    user_id=user_id,
                    message=user_text,
                    notification_type="admin_grant_custom",
                    parse_mode="HTML"
                )
            
            # PART 6: Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_custom",
                callback.from_user.id,
                user_id,
                f"Admin granted {duration_value} {duration_unit} access, notify_user={notify_user}, expires_at={expires_str}"
            )
            
        except Exception as e:
            logger.exception(f"Error granting custom access: {e}")
            await callback.message.answer(f"❌ Ошибка: {str(e)[:100]}", reply_markup=get_admin_back_keyboard(language))
        
        await state.clear()
        logger.debug(f"FSM: AdminGrantAccess cleared after grant")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_notify: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:notify:yes:minutes:") | F.data.startswith("admin:notify:no:minutes:"))
async def callback_admin_grant_minutes_notify(callback: CallbackQuery, bot: Bot):
    """
    3️⃣ REGISTER EXPLICIT CALLBACK HANDLERS
    4️⃣ IMPLEMENT NOTIFY LOGIC
    
    Handle notify choice for minutes grant.
    Works WITHOUT FSM - all data encoded in callback_data.
    Format: admin:notify:yes|no:minutes:<user_id>:<minutes>
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 3️⃣ REGISTER EXPLICIT CALLBACK HANDLERS: Parse callback_data
        parts = callback.data.split(":")
        if len(parts) != 6 or parts[1] != "notify" or parts[3] != "minutes":
            logger.warning(f"Invalid notify callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        notify_choice = parts[2]  # "yes" or "no"
        user_id = int(parts[4])
        minutes = int(parts[5])
        
        notify = notify_choice == "yes"
        
        # 4️⃣ ЛОГИРОВАНИЕ: при выборе notify
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify_choice}, user_id={user_id}, minutes={minutes}]")
        
        # 4️⃣ IMPLEMENT NOTIFY LOGIC: For admin:notify:yes
        if notify:
            # Use unified notification service
            import admin_notifications
            success = await admin_notifications.send_user_notification(
                bot=bot,
                user_id=user_id,
                message=f"Администратор выдал вам доступ на {minutes} минут",
                notification_type="admin_grant_minutes"
            )
            if success:
                logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, minutes={minutes}]")
        
        # 4️⃣ IMPLEMENT NOTIFY LOGIC: For admin:notify:no
        else:
            # 4️⃣ ЛОГИРОВАНИЕ: если notify=False
            logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, minutes={minutes}]")
        
        # 5️⃣ CLEAN TERMINATION: Edit admin message to "Готово"
        text = f"✅ Доступ выдан на {minutes} минут"
        if notify:
            text += "\nПользователь уведомлён."
        else:
            text += "\nДействие выполнено без уведомления."
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        
    except ValueError as e:
        logger.warning(f"Invalid callback data format: {callback.data}, error: {e}")
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
    except Exception as e:
        # 6️⃣ ERROR HANDLING: NO generic Exception raises, graceful exit
        logger.warning(f"Unexpected error in callback_admin_grant_minutes_notify: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)


@admin_access_router.callback_query(
    (F.data == "admin:notify:yes") | (F.data == "admin:notify:no"),
    StateFilter(AdminGrantAccess.waiting_for_notify)
)
async def callback_admin_grant_quick_notify_fsm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Handle notify choice for grant_days and grant_1_year (FSM-based flow).
    This handler works WITH FSM state (unlike minutes handler which is FSM-free).
    
    FIX: Missing handler for admin:notify:yes and admin:notify:no used by grant_days and grant_1_year.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify = callback.data == "admin:notify:yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        action_type = data.get("action_type")
        
        if not user_id or not action_type:
            logger.warning(f"Missing FSM data: user_id={user_id}, action_type={action_type}")
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify}, user_id={user_id}, action_type={action_type}]")
        
        # Execute grant based on action_type (treat as side-effect, don't check return value)
        if action_type == "grant_days":
            days = data.get("days")
            if not days:
                logger.error(f"Missing days in FSM for grant_days")
                await callback.answer("Ошибка: данные не найдены", show_alert=True)
                await state.clear()
                return
            
            # FIX: Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=days,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful (don't check return value)
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = f"✅ Доступ выдан на {days} дней"

            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
                if _ss():
                    asyncio.ensure_future(notify_subscription_extend(user_id, days, data.get("tariff", "basic")))
                    asyncio.ensure_future(sync_balance(user_id))
            except Exception:
                pass

            if notify:
                try:
                    user_text = f"Администратор выдал вам доступ на {days} дней"
                    await bot.send_message(user_id, user_text)
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, days={days}]")
                    text += "\nПользователь уведомлён."
                except Exception as e:
                    logger.exception(f"Error sending notification: {e}")
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, days={days}]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access",
                callback.from_user.id,
                user_id,
                f"Admin granted {days} days access, notify_user={notify}"
            )
        
        elif action_type == "grant_1_year":
            # FIX: Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=365,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful (don't check return value)
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = "✅ Доступ на 1 год выдан"

            # Site sync (fire-and-forget)
            try:
                from app.services.site_sync import notify_subscription_extend, sync_balance, is_enabled as _ss
                if _ss():
                    asyncio.ensure_future(notify_subscription_extend(user_id, 365, "basic"))
                    asyncio.ensure_future(sync_balance(user_id))
            except Exception:
                pass

            if notify:
                # Use unified notification service
                import admin_notifications
                success = await admin_notifications.send_user_notification(
                    bot=bot,
                    user_id=user_id,
                    message="Администратор выдал вам доступ на 1 год",
                    notification_type="admin_grant_1_year"
                )
                if success:
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, duration=1_year]")
                    text += "\nПользователь уведомлён."
                else:
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, duration=1_year]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_1_year",
                callback.from_user.id,
                user_id,
                f"Admin granted 1 year access, notify_user={notify}"
            )
        
        else:
            logger.warning(f"Unknown action_type: {action_type}")
            await callback.answer("Ошибка: неизвестный тип действия", show_alert=True)
        
        await state.clear()
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_quick_notify_fsm: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query((F.data == "admin:notify:yes") | (F.data == "admin:notify:no"))
async def callback_admin_grant_notify_fallback(callback: CallbackQuery, state: FSMContext):
    """
    FSM fallback: when FSM cleared, notify:yes/no would be Unhandled.
    Runs when primary (StateFilter waiting_for_notify) does not match.
    Without FSM data we cannot execute grant; inform user to retry.
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    logger.warning(
        "ADMIN_FSM_FALLBACK_EXECUTED "
        f"user={callback.from_user.id} "
        f"callback={callback.data}"
    )
    await callback.answer(
        "Сессия сброшена. Выберите пользователя заново и повторите действие.",
        show_alert=True
    )


@admin_access_router.callback_query(F.data.startswith("admin:revoke:user:"))
async def callback_admin_revoke(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    1️⃣ CALLBACK DATA SCHEMA (точечно)
    2️⃣ FIX handler callback_admin_revoke
    
    Admin revoke access - ask for notify choice first.
    Handler обрабатывает ТОЛЬКО callback вида: admin:revoke:user:<id>
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 2️⃣ FIX: Строгий guard - парсим только admin:revoke:user:<id>
        parts = callback.data.split(":")
        if len(parts) != 4 or parts[2] != "user":
            logger.warning(f"Invalid revoke callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        user_id = int(parts[3])
        
        # 4️⃣ FSM CONSISTENCY: Save user_id and ask for notify choice
        await state.update_data(user_id=user_id)
        
        text = i18n_get_text(language, "admin.revoke_confirm_text", "admin_revoke_confirm_text")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:revoke:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:revoke:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel", "admin_cancel"), callback_data=f"admin:user")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        await state.set_state(AdminRevokeAccess.waiting_for_notify_choice)
        
        # 5️⃣ ЛОГИРОВАНИЕ: выбран user_id
        logger.info(f"Admin {callback.from_user.id} initiated revoke for user {user_id}")
        logger.debug(f"FSM: AdminRevokeAccess.waiting_for_notify_choice set for user {user_id}")
        
    except ValueError as e:
        logger.error(f"Invalid user_id in revoke callback: {callback.data}, error: {e}")
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
        await state.clear()
    except Exception as e:
        logger.exception(f"Error in callback_admin_revoke: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_access_router.callback_query(F.data.startswith("admin:revoke:notify:"), StateFilter(AdminRevokeAccess.waiting_for_notify_choice))
async def callback_admin_revoke_notify(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler для notify
    
    Execute revoke with notify_user choice.
    Handler обрабатывает ТОЛЬКО callback вида: admin:revoke:notify:yes|no
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 1️⃣ НОРМАЛИЗАЦИЯ notify (КРИТИЧНО): читаем notify=yes|no
        parts = callback.data.split(":")
        if len(parts) != 4 or parts[2] != "notify":
            logger.warning(f"Invalid revoke notify callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            await state.clear()
            return
        
        # 1️⃣ НОРМАЛИЗАЦИЯ notify: явно приводим к bool
        notify_raw = parts[3]  # "yes" or "no"
        notify = notify_raw == "yes"  # bool: True or False
        
        # 4️⃣ FSM CONSISTENCY: используем сохраненный user_id
        data = await state.get_data()
        user_id = data.get("user_id")
        
        if not user_id:
            logger.error(f"user_id not found in FSM state for revoke notify")
            await callback.answer("Ошибка: user_id не найден", show_alert=True)
            await state.clear()
            return
        
        # 1️⃣ НОРМАЛИЗАЦИЯ notify: сохраняем в FSM ТОЛЬКО bool
        await state.update_data(notify=notify)
        
        # 4️⃣ ЛОГИРОВАНИЕ: при выборе notify
        logger.info(f"ADMIN_REVOKE_NOTIFY_SELECTED [user_id={user_id}, notify={notify}]")
        
        # 3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler: вызываем финальный revoke action
        revoked = await database.admin_revoke_access_atomic(
            telegram_id=user_id,
            admin_telegram_id=callback.from_user.id
        )

        # Fire-and-forget: disable Remnawave bypass
        if revoked:
            try:
                from app.services.remnawave_service import disable_remnawave_user_bg
                disable_remnawave_user_bg(user_id)
            except Exception as rmn_err:
                logger.warning("REMNAWAVE_ADMIN_REVOKE_FAIL: tg=%s %s", user_id, rmn_err)

        if not revoked:
            text = "❌ У пользователя нет активной подписки"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Нет активной подписки", show_alert=True)
        else:
            text = "✅ Доступ отозван"
            if notify:
                text += "\nПользователь уведомлён."
            else:
                text += "\nДействие выполнено без уведомления."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # 2️⃣ ПРОВЕРКА notify В ФИНАЛЬНОМ revoke: используем ТОЛЬКО if notify:
            # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ (ЯВНО): если notify=True
            if notify:
                # 5️⃣ ЗАЩИТА ОТ ТИХОГО ПРОПУСКА: проверяем telegram_id
                if not user_id:
                    logger.warning(f"ADMIN_REVOKE_NOTIFY_SKIP: user_id missing, notify=True but cannot send")
                else:
                    try:
                        # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ: используем telegram_id из FSM (НЕ из callback)
                        # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ: текст без форматных рисков (фиксированный)
                        # Use unified notification service
                        import admin_notifications
                        user_text = (
                            "Ваш доступ был отозван администратором.\n"
                            "Если вы считаете это ошибкой — обратитесь в поддержку."
                        )
                        success = await admin_notifications.send_user_notification(
                            bot=bot,
                            user_id=user_id,
                            message=user_text,
                            notification_type="admin_revoke"
                        )
                        if success:
                            # 4️⃣ ЛОГИРОВАНИЕ: при отправке уведомления
                            logger.info(f"NOTIFICATION_SENT [type=admin_revoke, user_id={user_id}]")
                    except Exception as e:
                        logger.exception(f"Error sending notification to user {user_id}: {e}")
                        # Не прерываем выполнение - revoke уже выполнен
            else:
                # 4️⃣ ЛОГИРОВАНИЕ: если notify=False
                logger.info(f"ADMIN_REVOKE_NOTIFY_SKIPPED [user_id={user_id}]")
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_revoke_access",
                callback.from_user.id,
                user_id,
                f"Admin revoked access, notify_user={notify}"
            )
        
        # 3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler: корректно завершаем FSM
        await state.clear()
        logger.debug(f"FSM: AdminRevokeAccess cleared after revoke")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_revoke_notify: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================


async def _show_admin_user_card(message_or_callback, user_id: int, admin_telegram_id: int):
    """Вспомогательная функция для отображения карточки пользователя администратору"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    language = await resolve_user_language(admin_telegram_id)
    try:
        overview = await admin_service.get_admin_user_overview(user_id)
    except UserNotFoundError:
        if hasattr(message_or_callback, 'edit_text'):
            await message_or_callback.edit_text(
                i18n_get_text(language, "admin.user_not_found"),
                reply_markup=get_admin_back_keyboard(language)
            )
        else:
            await message_or_callback.answer("❌ Пользователь не найден")
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
        vpn_key = overview.subscription.get('vpn_key', '—')
        if vpn_key and vpn_key != '—':
            text += f"VPN-ключ:\n<code>{vpn_key}</code>\n"
        else:
            text += "VPN-ключ: —\n"
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
        text += f"\n👑 VIP-��татус: активен\n"

    # Remnawave трафик (краткая сводка)
    _rmn_uuid = await database.get_remnawave_uuid(user_id)
    if _rmn_uuid:
        try:
            from app.services import remnawave_api
            _traffic = await remnawave_api.get_user_traffic(_rmn_uuid)
            if _traffic:
                _used = _traffic.get("usedTrafficBytes", 0)
                _limit = _traffic.get("trafficLimitBytes", 0)
                _remaining = max(0, _limit - _used)
                def _fmt(b):
                    return f"{b / 1024**3:.1f} Г��" if b >= 1024**3 else f"{b / 1024**2:.0f} МБ"
                text += f"\n📊 Трафик обхода: {_fmt(_used)} / {_fmt(_limit)} (ост. {_fmt(_remaining)})\n"
        except Exception:
            pass

    # Отображаем карточку
    sub_type = (overview.subscription.get("subscription_type") or "basic").strip().lower() if overview.subscription else "basic"
    if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
        sub_type = "basic"
    keyboard = get_admin_user_keyboard(
        has_active_subscription=overview.subscription_status.is_active,
        user_id=overview.user["telegram_id"],
        has_discount=overview.user_discount is not None,
        is_vip=overview.is_vip,
        subscription_type=sub_type,
        language=language
    )
    
    if hasattr(message_or_callback, 'edit_text'):
        await message_or_callback.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML")


@admin_access_router.callback_query(F.data.startswith("admin:vip_grant:"))
async def callback_admin_vip_grant(callback: CallbackQuery):
    """Обработчик кнопки 'Выдать VIP'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Проверяем, есть ли уже VIP-статус
        existing_vip = await database.is_vip_user(user_id)
        if existing_vip:
            # Если уже есть VIP, просто обновляем карточку
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("VIP уже назначен", show_alert=True)
            return
        
        # Назначаем VIP-статус
        success = await database.grant_vip_status(
            telegram_id=user_id,
            granted_by=callback.from_user.id
        )
        
        if success:
            # После успешного назначения VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("✅ VIP-статус выдан", show_alert=True)
        else:
            text = "❌ Ошибка при назначении VIP-статуса"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin:vip_revoke:"))
async def callback_admin_vip_revoke(callback: CallbackQuery):
    """Обработчик кнопки 'Снять VIP'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Отзываем VIP-статус
        success = await database.revoke_vip_status(
            telegram_id=user_id,
            revoked_by=callback.from_user.id
        )
        
        if success:
            # После успешного снятия VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("✅ VIP-статус снят", show_alert=True)
        else:
            text = "❌ VIP-статус не найден или уже снят"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("VIP не найден", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_revoke: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin:user_reissue:"))
async def callback_admin_user_reissue(callback: CallbackQuery):
    """Перевыпуск ключа из админ-дашборда. 5 слоёв защиты: immediate ACK, disabled UI, in-memory lock, Postgres advisory lock, correlation logging."""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    try:
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return

    # STEP 3 — IN-MEMORY ASYNC LOCK (fast UX check + real acquire)
    lock = get_reissue_lock(target_user_id)
    logger.debug("ADMIN_REISSUE_LOCK_ATTEMPT user=%s locked=%s", target_user_id, lock.locked())
    
    # STEP 1 — FAST CHECK (UX guard only)
    if lock.locked():
        logger.info("ADMIN_REISSUE_REJECTED_ALREADY_RUNNING user=%s", target_user_id)
        await callback.answer("Перевыпуск уже выполняется...", show_alert=False)
        return

    # STEP 2 — ACQUIRE (real acquire, no timeout)
    await lock.acquire()

    try:
        # STEP 1 — IMMEDIATE CALLBACK ACK (inside protected block to prevent lock leak)
        await callback.answer("Перевыпуск ключа запущен...", show_alert=False)
        correlation_id = str(uuid.uuid4())
        update_id = getattr(getattr(callback, "update", None), "update_id", None)
        logger.info(
            "ADMIN_REISSUE_START",
            extra={
                "correlation_id": correlation_id,
                "admin_id": callback.from_user.id,
                "target_user_id": target_user_id,
                "callback_id": callback.id,
                "update_id": update_id,
                "task_id": id(asyncio.current_task()),
            },
        )

        # STEP 2 — DISABLE BUTTON DURING PROCESSING
        try:
            await callback.message.edit_reply_markup(
                reply_markup=get_admin_user_keyboard_processing(target_user_id, language=language)
            )
        except TelegramBadRequest:
            pass  # Message may be edited by other handler

        admin_telegram_id = callback.from_user.id
        result = await database.reissue_vpn_key_atomic(
            target_user_id, admin_telegram_id, correlation_id=correlation_id
        )
        new_vpn_key, old_vpn_key = result

        if new_vpn_key is None:
            await safe_edit_text(
                callback.message,
                "❌ Не удалось перевыпустить ключ. Нет активной подписки или ошибка создания ключа.",
                reply_markup=get_admin_back_keyboard(language),
            )
            return

        # STEP 6 — RESTORE KEYBOARD AFTER SUCCESS
        user = await database.get_user(target_user_id)
        subscription = await database.get_subscription(target_user_id)
        is_vip = await database.is_vip_user(target_user_id)
        has_discount = await database.get_user_discount(target_user_id) is not None

        text = "👤 Информация о пользователе\n\n"
        text += f"Telegram ID: {target_user_id}\n"
        text += f"Username: @{user.get('username', 'не указан') if user else 'не указан'}\n\n"
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            text += "Статус подписки: ✅ Активна\n"
            text += f"Срок действия: до {expires_str}\n"
            text += f"VPN-ключ: <code>{new_vpn_key}</code>\n"
            text += f"\n✅ Ключ перевыпущен!\nСтарый ключ: {old_vpn_key[:20]}..."

        sub = await database.get_subscription(target_user_id)
        sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
        if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
            sub_type = "basic"
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_keyboard(
                has_active_subscription=True,
                user_id=target_user_id,
                has_discount=has_discount,
                is_vip=is_vip,
                subscription_type=sub_type,
                language=language,
            ),
            parse_mode="HTML",
        )

        logger.info(
            "ADMIN_REISSUE_COMPLETE",
            extra={"correlation_id": correlation_id, "target_user_id": target_user_id},
        )

        # Уведомляем пользователя
        try:
            from vpn_utils import build_sub_url
            user_text = get_reissue_notification_text(build_sub_url(target_user_id))
            keyboard = get_reissue_notification_keyboard()
            await callback.bot.send_message(target_user_id, user_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_user_id}: {e}")

    except Exception as e:
        logging.exception(f"Error in callback_admin_user_reissue: {e}")
        try:
            await safe_edit_text(
                callback.message,
                "❌ Ошибка при перевыпуске ключа. Проверь логи.",
                reply_markup=get_admin_back_keyboard(language),
            )
        except Exception:
            pass
    finally:
        # GUARANTEED RELEASE (lock was acquired, no check needed)
        lock.release()


# ====================================================================================
# ADMIN: DELETE USER FROM DB
# ====================================================================================

@admin_access_router.callback_query(F.data.startswith("admin:delete_user:"))
async def callback_admin_delete_user(callback: CallbackQuery):
    """Показываем подтверждение удаления пользователя из БД"""
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

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Подтвердить удаление",
                callback_data=f"admin:delete_user_confirm:{user_id}"
            )],
            [InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"admin:user_back:{user_id}"
            )],
        ])
        await callback.message.edit_text(
            f"⚠️ Вы точно хотите удалить пользователя <b>{user_id}</b> из базы данных?\n\n"
            "Будут удалены ВСЕ данные:\n"
            "• Профиль пользователя\n"
            "• Подписка и VPN-ключ\n"
            "• История платежей\n"
            "• Баланс\n"
            "• Реферальные данные\n"
            "• Скидки и VIP-статус\n\n"
            "❗️ Это действие необратимо!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except ValueError:
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in callback_admin_delete_user: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@admin_access_router.callback_query(F.data.startswith("admin:delete_user_confirm:"))
async def callback_admin_delete_user_confirm(callback: CallbackQuery):
    """Подтверждение удаления — выполняем полное удаление пользователя из БД"""
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
        admin_id = callback.from_user.id

        success = await database.admin_delete_user_complete(user_id, admin_id)

        if success:
            logger.info(f"Admin {admin_id} deleted user {user_id} from DB completely")
            await callback.message.edit_text(
                f"✅ Пользователь <b>{user_id}</b> полностью удалён из базы данных.",
                reply_markup=get_admin_back_keyboard(language),
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"❌ Пользователь <b>{user_id}</b> не найден в базе данных.",
                reply_markup=get_admin_back_keyboard(language),
                parse_mode="HTML"
            )
    except ValueError:
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in callback_admin_delete_user_confirm: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при удалении пользователя. Проверь логи.",
            reply_markup=get_admin_back_keyboard(language),
        )


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
