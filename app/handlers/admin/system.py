"""Служебные экраны: здоровье системы, обслуживание и тестовые уведомления.

ЧТО ЗДЕСЬ
    admin:system — светофор по компонентам (БД, VPN API, платежи, Redis),
    список проблем и аптайм; admin:remnawave_mass_provision — разовая
    заливка активных подписчиков в панель; admin:test_menu / admin:test:* —
    отправка тестовых уведомлений самому себе; admin:qodev — отчёт по
    пользователям, привязанным к сайту.

ПОЧЕМУ ВМЕСТЕ
    Это один экранный узел: тестовое меню и массовый провижн доступны
    только с экрана «Система» и возвращают на него же кнопкой «назад».

ЧТО ЛЕГКО СЛОМАТЬ
    Тесты шлют сообщения САМОМУ АДМИНУ (test_user_id = callback.from_user.id)
    и ничего не проводят через платежи и VPN API. Подстановка сюда чужого
    id превращает «тест» в рассылку живым людям.

    Массовый провижн уходит в фон через asyncio.create_task и живёт дольше
    обработчика: он держит ссылку на bot и chat_id, а не на callback.
    Обращение к callback.message после выхода из обработчика даст гонку с
    уже устаревшим сообщением.

    Схему отсюда больше не правят. Экран QoDev выполнял ALTER TABLE ...
    ADD COLUMN IF NOT EXISTS на живой таблице users; колонка site_linked
    заведена при инициализации схемы (database/legacy_schema.py). За тем,
    чтобы DDL не вернулся в обработчики, следит
    tests/services/test_no_ddl_in_handlers.py.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text
from app.core.runtime_context import get_bot_start_time

admin_system_router = Router()
logger = logging.getLogger(__name__)


@admin_system_router.callback_query(F.data == "admin:system")
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


@admin_system_router.callback_query(F.data == "admin:remnawave_mass_provision")
async def callback_remnawave_mass_provision(callback: CallbackQuery):
    """Mass-provision all active subscribers to Remnawave (parallel, background)."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()

    users = await database.get_active_users_without_remnawave()
    total = len(users)

    if total == 0:
        await callback.message.answer("✅ Все пользователи с подпиской уже в Remnawave.", parse_mode="HTML")
        return

    await callback.message.answer(
        f"🌐 Массовый провижн: {total} пользователей.\n"
        f"Параллельно по 20, пауза 2 сек. Работает в фоне.",
        parse_mode="HTML",
    )

    import asyncio
    from app.services import remnawave_service

    CONCURRENCY = 20
    BATCH_PAUSE = 2
    PROGRESS_EVERY = 100
    bot = callback.bot
    chat_id = callback.message.chat.id

    async def _run_mass_provision():
        success = 0
        failed = 0
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def _provision_one(user):
            nonlocal success, failed
            async with semaphore:
                try:
                    tg_id = user["telegram_id"]
                    sub_type = (user.get("subscription_type") or "basic").strip().lower()
                    expires_at = user.get("expires_at")
                    if not expires_at:
                        failed += 1
                        return
                    await remnawave_service.create_remnawave_user(
                        tg_id, sub_type, expires_at,
                        traffic_limit_override=10 * 1024**3,
                    )
                    success += 1
                except Exception as e:
                    logging.error("MASS_PROVISION_ERROR: tg=%s %s", user.get("telegram_id"), e)
                    failed += 1

        for i in range(0, total, PROGRESS_EVERY):
            batch = users[i:i + PROGRESS_EVERY]
            await asyncio.gather(*[_provision_one(u) for u in batch])

            processed = min(i + PROGRESS_EVERY, total)
            try:
                await bot.send_message(
                    chat_id,
                    f"⏳ Прогресс: {processed}/{total} (✅ {success} / ❌ {failed})",
                    parse_mode="HTML",
                )
            except Exception:
                pass

            if processed < total:
                await asyncio.sleep(BATCH_PAUSE)

        try:
            await bot.send_message(
                chat_id,
                f"🏁 Массовый провижн завершён!\n\n"
                f"Всего: {total}\n"
                f"✅ Успешно: {success}\n"
                f"❌ Ошибки: {failed}",
                parse_mode="HTML",
            )
        except Exception:
            pass

        await database._log_audit_event_atomic_standalone(
            "admin_remnawave_mass_provision",
            callback.from_user.id,
            None,
            f"Mass provision: total={total}, success={success}, failed={failed}",
        )

    asyncio.create_task(_run_mass_provision())


@admin_system_router.callback_query(F.data == "admin:test_menu")
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


@admin_system_router.callback_query(F.data.startswith("admin:test:"))
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
                "Ваш триал активирован! Пользуйтесь VPN бесплатно.",
                parse_mode="HTML",
            )
            result_text = "✅ Тест активации триала выполнен"
            
        elif test_type == "first_purchase":
            # Test first purchase notification
            await bot.send_message(
                test_user_id,
                "💰 [ТЕСТ] Уведомление о первой покупке\n\n"
                "Спасибо за покупку! Ваша подписка активирована.",
                parse_mode="HTML",
            )
            result_text = "✅ Тест уведомления о первой покупке выполнен"
            
        elif test_type == "renewal":
            # Test renewal notification
            await bot.send_message(
                test_user_id,
                "🔄 [ТЕСТ] Уведомление о продлении\n\n"
                "Ваша подписка автоматически продлена.",
                parse_mode="HTML",
            )
            result_text = "✅ Тест уведомления о продлении выполнен"
            
        elif test_type == "reminders":
            # Test reminder notifications
            await bot.send_message(
                test_user_id,
                "⏰ [ТЕСТ] Напоминание о подписке\n\n"
                "Ваша подписка скоро истечёт. Продлите её сейчас!",
                parse_mode="HTML",
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


@admin_system_router.callback_query(F.data == "admin:qodev")
async def callback_admin_qodev(callback: CallbackQuery):
    """Показать пользователей, привязанных к сайту QoDev."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()

    try:
        # Get linked users from bot DB (site_linked = true)
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            # Колонка site_linked заводится при инициализации схемы
            # (database/legacy_schema.py). ALTER TABLE отсюда убран: он
            # брал ACCESS EXCLUSIVE на users ради одного экрана.
            rows = await conn.fetch("""
                SELECT u.telegram_id, u.username, u.created_at,
                       s.expires_at, s.subscription_type
                FROM users u
                LEFT JOIN subscriptions s ON s.telegram_id = u.telegram_id
                WHERE u.site_linked = TRUE
                ORDER BY u.created_at DESC
                LIMIT 50
            """)

        if not rows:
            text = "🌐 <b>QoDev</b>\n\nПривязанных пользователей не найдено."
        else:
            text = f"🌐 <b>QoDev — привязанные пользователи</b>\n\nВсего: {len(rows)}\n\n"
            for row in rows[:20]:
                uname = f"@{row['username']}" if row['username'] else "—"
                plan = (row["subscription_type"] or "—").strip()
                expires = row["expires_at"]
                if expires and expires > datetime.now(timezone.utc):
                    exp_str = expires.strftime("%d.%m.%Y")
                    text += f"👤 <code>{row['telegram_id']}</code> · {uname} · {plan} · до {exp_str}\n"
                else:
                    text += f"👤 <code>{row['telegram_id']}</code> · {uname} · нет подписки\n"
            if len(rows) > 20:
                text += f"\n<i>...и ещё {len(rows) - 20}</i>"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:qodev")],
            [InlineKeyboardButton(text=i18n_get_text("ru", "admin.back"), callback_data="admin:main")],
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)

    except Exception as e:
        logger.exception("Error in admin:qodev: %s", e)
        await safe_edit_text(
            callback.message,
            f"🌐 <b>QoDev</b>\n\n❌ Ошибка: {e}",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot,
        )
