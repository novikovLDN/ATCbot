"""
Admin base entry handlers: /admin command and dashboard callbacks.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.security import require_admin, admin_only
from app.handlers.admin.keyboards import get_admin_dashboard_keyboard, get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import AdminCreatePromocode
from app.core.runtime_context import get_bot_start_time

admin_base_router = Router()
logger = logging.getLogger(__name__)


@admin_base_router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message):
    """Административный дашборд"""
    language = await resolve_user_language(message.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await message.answer(text, reply_markup=get_admin_dashboard_keyboard(language))


@admin_base_router.callback_query(F.data == "admin:dashboard")
@admin_only
async def callback_admin_dashboard(callback: CallbackQuery):
    """
    Admin Dashboard — rich real-time overview with key metrics.
    """
    try:
        from app.core.system_state import recalculate_from_runtime, SystemSeverity

        system_state = recalculate_from_runtime()
        severity = system_state.get_severity()
        severity_map = {
            SystemSeverity.GREEN: "🟢 OK",
            SystemSeverity.YELLOW: "🟡 DEGRADED",
            SystemSeverity.RED: "🔴 CRITICAL",
        }

        def _icon(comp):
            return {"healthy": "✅", "degraded": "⚠️", "unavailable": "❌"}.get(comp.status.value, "❓")

        db_ready = database.DB_READY

        text = f"📊 Admin Dashboard\n\n"
        text += f"Статус: {severity_map[severity]}\n"
        text += f"БД: {_icon(system_state.database)} | VPN: {_icon(system_state.vpn_api)} | Платежи: {_icon(system_state.payments)}\n"

        # Key metrics (if DB is ready)
        if db_ready:
            try:
                stats = await database.get_admin_stats()
                daily = await database.get_daily_summary(None)
                text += f"\n━━━ Ключевые показатели ━━━\n"
                text += f"👥 Пользователей: {stats['total_users']}\n"
                text += f"🔑 Активных подписок: {stats['active_subscriptions']}\n"
                text += f"💳 Платежей: {stats['approved_payments']}/{stats['total_payments']}\n"
                text += f"\n━━━ Сегодня ━━━\n"
                text += f"💰 Доход: {daily.get('revenue', 0):.2f} ₽\n"
                text += f"🆕 Новых: {daily.get('new_users', 0)} польз. | {daily.get('new_subscriptions', 0)} подп.\n"
                text += f"💳 Платежей: {daily.get('payments_count', 0)}\n"
            except Exception as stats_err:
                logger.exception(f"Failed to load dashboard metrics: {stats_err}")
                err_short = str(stats_err)[:120]
                text += f"\n⚠️ Не удалось загрузить метрики\n<code>{err_short}</code>\n"

        # Uptime
        start_time = get_bot_start_time()
        if start_time:
            uptime_seconds = int((datetime.now(timezone.utc) - start_time).total_seconds())
            uptime_days = uptime_seconds // 86400
            uptime_hours = (uptime_seconds % 86400) // 3600
            uptime_minutes = (uptime_seconds % 3600) // 60
            text += f"\n⏱ Аптайм: {uptime_days}д {uptime_hours}ч {uptime_minutes}м"

        language = await resolve_user_language(callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.refresh"), callback_data="admin:dashboard")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_menu"), callback_data="admin:test_menu")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

        await database._log_audit_event_atomic_standalone(
            "admin_dashboard_viewed",
            callback.from_user.id,
            None,
            f"Admin viewed dashboard: db_ready={db_ready}"
        )

    except Exception as e:
        logger.exception(f"Error in callback_admin_dashboard: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.dashboard_data"), show_alert=True)


@admin_base_router.callback_query(F.data == "admin:main")
async def callback_admin_main(callback: CallbackQuery):
    """Главный экран админ-дашборда"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(callback.message, text, reply_markup=get_admin_dashboard_keyboard(language))
    await callback.answer()


@admin_base_router.callback_query(F.data.startswith("admin:reissue_key:"))
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


@admin_base_router.callback_query(F.data == "admin:reissue_all_active")
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
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin:keys"),
        ]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@admin_base_router.callback_query(F.data == "admin:reissue_all_active_go")
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
        status_message = await callback.message.edit_text(status_text, reply_markup=None)
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
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:keys")]
        ])
        
        try:
            await status_message.edit_text(final_text, reply_markup=keyboard)
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
            reply_markup=get_admin_back_keyboard(language)
        )


@admin_base_router.callback_query(F.data == "admin:create_promocode")
async def callback_admin_create_promocode(callback: CallbackQuery, state: FSMContext):
    """Начало создания промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    await state.set_state(AdminCreatePromocode.waiting_for_code_name)
    logger.info("PROMO_STATE_SET waiting_for_code_name")
    text = i18n_get_text(language, "admin.promocode_code_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@admin_base_router.callback_query(F.data.startswith("admin:promocode_unit:"))
async def callback_admin_promocode_unit(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора единицы времени"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    unit = callback.data.split(":")[2]  # hours, days, months
    
    unit_names = {
        "hours": "часов",
        "days": "дней",
        "months": "месяцев"
    }
    
    await state.update_data(promocode_duration_unit=unit)
    await state.set_state(AdminCreatePromocode.waiting_for_duration_value)
    logger.info("PROMO_STATE_SET waiting_for_duration_value unit=%s", unit)
    text = i18n_get_text(language, "admin.promocode_duration_value_prompt", unit=unit_names[unit])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:promocode_cancel")]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@admin_base_router.callback_query(F.data == "admin:promocode_confirm")
async def callback_admin_promocode_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение создания промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    data = await state.get_data()
    
    code = data.get("promocode_code")
    discount_percent = data.get("promocode_discount")
    duration_seconds = data.get("promocode_duration_seconds")
    max_uses = data.get("promocode_max_uses")
    
    if not all([code, discount_percent is not None, duration_seconds, max_uses]):
        await callback.answer("Ошибка: неполные данные", show_alert=True)
        await state.clear()
        return
    
    # Создаем промокод
    result = await database.create_promocode_atomic(
        code=code,
        discount_percent=discount_percent,
        duration_seconds=duration_seconds,
        max_uses=max_uses,
        created_by=callback.from_user.id
    )
    
    if result:
        # Форматируем длительность для отображения
        if duration_seconds < 3600:
            duration_str = f"{duration_seconds // 60} минут"
        elif duration_seconds < 86400:
            duration_str = f"{duration_seconds // 3600} часов"
        elif duration_seconds < 2592000:
            duration_str = f"{duration_seconds // 86400} дней"
        else:
            duration_str = f"{duration_seconds // 2592000} месяцев"
        
        text = i18n_get_text(
            language, "admin.promocode_created",
            code=code,
            discount=discount_percent,
            duration=duration_str,
            max_uses=max_uses
        )
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer("✅ Промокод создан", show_alert=True)
    else:
        text = i18n_get_text(language, "admin.promocode_creation_failed")
        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer("❌ Ошибка создания", show_alert=True)
    
    await state.clear()


@admin_base_router.callback_query(F.data == "admin:promocode_cancel")
async def callback_admin_promocode_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена создания промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)
    await state.clear()
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(callback.message, text, reply_markup=get_admin_dashboard_keyboard(language))
    await callback.answer()


# ==================== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ VIP-СТАТУСОМ ====================


@admin_base_router.callback_query(F.data == "admin:system")
async def callback_admin_system(callback: CallbackQuery):
    """
    PART A.3: Admin system status dashboard with severity and error summary.
    Uses SystemState for accurate runtime health display.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    try:
        from app.core.system_state import recalculate_from_runtime, SystemSeverity

        # Build real SystemState from runtime
        system_state = recalculate_from_runtime()

        # Count pending activations
        pending_activations = 0
        if database.DB_READY:
            try:
                pool = await database.get_pool()
                if pool:
                    async with pool.acquire() as conn:
                        pending_activations = await conn.fetchval(
                            "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                        ) or 0
            except Exception:
                pass

        severity = system_state.get_severity(pending_activations=pending_activations)
        severity_map = {
            SystemSeverity.GREEN: ("🟢", "OK"),
            SystemSeverity.YELLOW: ("🟡", "DEGRADED"),
            SystemSeverity.RED: ("🔴", "CRITICAL"),
        }
        sev_emoji, sev_label = severity_map[severity]

        text = f"{sev_emoji} Система ({sev_label})\n\n"

        # Component statuses
        def _comp_icon(comp):
            from app.core.system_state import ComponentStatus
            return {"healthy": "✅", "degraded": "⚠️", "unavailable": "❌"}.get(comp.status.value, "❓")

        text += "📊 Компоненты:\n"
        text += f"  • База данных: {_comp_icon(system_state.database)} {system_state.database.status.value.upper()}\n"
        text += f"  • VPN API: {_comp_icon(system_state.vpn_api)} {system_state.vpn_api.status.value.upper()}\n"
        text += f"  • Платежи: {_comp_icon(system_state.payments)} {system_state.payments.status.value.upper()}\n"

        # Redis status
        try:
            from app.utils.redis_client import ping as redis_ping, is_configured as redis_configured
            if redis_configured():
                redis_ok = await redis_ping()
                text += f"  • Redis: {'✅ HEALTHY' if redis_ok else '⚠️ UNAVAILABLE'}\n"
            else:
                text += f"  • Redis: ⚠️ НЕ НАСТРОЕН (FSM в памяти)\n"
        except Exception:
            text += f"  • Redis: ❓ ОШИБКА ПРОВЕРКИ\n"

        if pending_activations > 0:
            text += f"  • Ожидающих активаций: {pending_activations}\n"
        text += "\n"

        # Error summary from SystemState
        errors = system_state.get_error_summary()
        if errors:
            text += "⚠️ Проблемы:\n"
            for err in errors:
                text += f"  • {err['component']}: {err['reason']}\n"
                text += f"    → {err['impact']}\n"
            text += "\n"
        else:
            text += "✅ Проблем не обнаружено\n\n"

        # Uptime
        start_time = get_bot_start_time()
        if start_time:
            uptime_seconds = int(
                (datetime.now(timezone.utc) - start_time).total_seconds()
            )
        else:
            uptime_seconds = 0
        uptime_days = uptime_seconds // 86400
        uptime_hours = (uptime_seconds % 86400) // 3600
        uptime_minutes = (uptime_seconds % 3600) // 60
        uptime_str = f"{uptime_days}д {uptime_hours}ч {uptime_minutes}м"
        text += f"⏱ Время работы: {uptime_str}"
        logger.info("SYSTEM_PANEL_REQUESTED severity=%s uptime_seconds=%s", severity.value, uptime_seconds)

        language = await resolve_user_language(callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:system")],
            [InlineKeyboardButton(text="🌐 Добавить всех в Remnawave", callback_data="admin:remnawave_mass_provision")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_menu"), callback_data="admin:test_menu")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

        await database._log_audit_event_atomic_standalone(
            "admin_view_system",
            callback.from_user.id,
            None,
            f"Admin viewed system status: severity={severity.value}"
        )

    except Exception as e:
        logging.exception(f"Error in callback_admin_system: {e}")
        await callback.answer("Ошибка при получении системной информации", show_alert=True)


@admin_base_router.callback_query(F.data == "admin:remnawave_mass_provision")
async def callback_remnawave_mass_provision(callback: CallbackQuery):
    """Mass-provision all active subscribers to Remnawave (batches of 100)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()

    users = await database.get_active_users_without_remnawave()
    total = len(users)

    if total == 0:
        await callback.message.answer("✅ Все пользователи с подпиской уже в Remnawave.")
        return

    await callback.message.answer(
        f"🌐 Запускаю массовый провижн: {total} пользователей без Remnawave.\n"
        f"Пачками по 100, пауза 3 сек между пачками."
    )

    import asyncio
    from app.services import remnawave_service

    BATCH_SIZE = 100
    BATCH_PAUSE = 3
    success = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch = users[i:i + BATCH_SIZE]
        for user in batch:
            try:
                tg_id = user["telegram_id"]
                sub_type = (user.get("subscription_type") or "basic").strip().lower()
                expires_at = user.get("expires_at")
                if not expires_at:
                    failed += 1
                    continue
                await remnawave_service.create_remnawave_user(
                    tg_id, sub_type, expires_at,
                    traffic_limit_override=10 * 1024**3,
                )
                success += 1
            except Exception as e:
                logging.error("MASS_PROVISION_ERROR: tg=%s %s", user.get("telegram_id"), e)
                failed += 1

        processed = min(i + BATCH_SIZE, total)
        # Progress update every batch
        try:
            await callback.message.answer(
                f"⏳ Прогресс: {processed}/{total} (✅ {success} / ❌ {failed})"
            )
        except Exception:
            pass

        if processed < total:
            await asyncio.sleep(BATCH_PAUSE)

    await callback.message.answer(
        f"🏁 Массовый провижн завершён!\n\n"
        f"Всего: {total}\n"
        f"✅ Успешно: {success}\n"
        f"❌ Ошибки: {failed}"
    )

    await database._log_audit_event_atomic_standalone(
        "admin_remnawave_mass_provision",
        callback.from_user.id,
        None,
        f"Mass provision: total={total}, success={success}, failed={failed}",
    )


@admin_base_router.callback_query(F.data == "admin:test_menu")
async def callback_admin_test_menu(callback: CallbackQuery):
    """
    PART C.5: Admin test menu for testing notifications.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    language = await resolve_user_language(callback.from_user.id)

    text = "🧪 Тестовое меню\n\n"
    text += "Выберите тест для выполнения:\n"
    text += "• Тесты выполняются без реальных платежей\n"
    text += "• VPN API не вызывается\n"
    text += "• Все действия логируются в audit_log(type=test)"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_trial"), callback_data="admin:test:trial_activation")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_first_purchase"), callback_data="admin:test:first_purchase")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_renewal"), callback_data="admin:test:renewal")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.test_reminders"), callback_data="admin:test:reminders")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:system")],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()
    
    await database._log_audit_event_atomic_standalone(
        "admin_test_menu_viewed",
        callback.from_user.id,
        None,
        "Admin viewed test menu"
    )


@admin_base_router.callback_query(F.data.startswith("admin:test:"))
async def callback_admin_test(callback: CallbackQuery, bot: Bot):
    """
    PART C.5: Execute admin test actions.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    test_type = callback.data.split(":")[-1]
    
    try:
        # PART C.5: All tests are logged with type=test
        test_user_id = callback.from_user.id  # Use admin ID as test user
        
        if test_type == "trial_activation":
            # Test trial activation notification
            await bot.send_message(
                test_user_id,
                "🎁 [ТЕСТ] Уведомление об активации триала\n\n"
                "Ваш триал активирован! Пользуйтесь VPN бесплатно."
            )
            result_text = "✅ Тест активации триала выполнен"
            
        elif test_type == "first_purchase":
            # Test first purchase notification
            await bot.send_message(
                test_user_id,
                "💰 [ТЕСТ] Уведомление о первой покупке\n\n"
                "Спасибо за покупку! Ваша подписка активирована."
            )
            result_text = "✅ Тест уведомления о первой покупке выполнен"
            
        elif test_type == "renewal":
            # Test renewal notification
            await bot.send_message(
                test_user_id,
                "🔄 [ТЕСТ] Уведомление о продлении\n\n"
                "Ваша подписка автоматически продлена."
            )
            result_text = "✅ Тест уведомления о продлении выполнен"
            
        elif test_type == "reminders":
            # Test reminder notifications
            await bot.send_message(
                test_user_id,
                "⏰ [ТЕСТ] Напоминание о подписке\n\n"
                "Ваша подписка скоро истечёт. Продлите её сейчас!"
            )
            result_text = "✅ Тест напоминаний выполнен"
            
        else:
            result_text = "❌ Неизвестный тип теста"
        
        # PART C.5: Log test action
        await database._log_audit_event_atomic_standalone(
            "admin_test_executed",
            callback.from_user.id,
            None,
            f"Test type: {test_type}, result: {result_text}"
        )
        
        await callback.answer(result_text, show_alert=True)
        await callback_admin_test_menu(callback)
        
    except Exception as e:
        logger.exception(f"Error in admin test {test_type}: {e}")
        await callback.answer(f"Ошибка выполнения теста: {e}", show_alert=True)


@admin_base_router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    """Обработчик disabled кнопки во время перевыпуска ключа"""
    await callback.answer("Операция уже выполняется...", show_alert=False)
