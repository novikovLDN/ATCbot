"""Вход в админку из чата: /admin, ссылка на веб-дашборд, сброс пароля.

ЧТО ЗДЕСЬ
    Парадная дверь. Команда /admin показывает кнопку входа в дашборд по
    одноразовому токену и кнопку сброса пароля. Плюс два экрана, оставшихся
    от старого меню: admin:main и admin:dashboard.

ПОЧЕМУ ФАЙЛ УМЕНЬШИЛСЯ
    Здесь было 1167 строк и пять несвязанных разделов. Перевыпуск ключей,
    промокоды, диагностика системы и переписка с пользователем разъехались
    по соседям — см. app/handlers/admin/__init__.py.

ЧТО ЛЕГКО СЛОМАТЬ
    Ссылка на дашборд содержит одноразовый login-токен. Кэшировать её,
    переиспользовать или логировать целиком нельзя: это готовый вход в
    админку для любого, кто увидит URL.

    Подсказка про iPhone написана здесь, а не только в дашборде, намеренно:
    url-кнопка на iOS открывается во встроенном браузере Telegram, где Web
    Push не работает в принципе. Убрать текст значит оставить админа без
    уведомлений и без объяснения, почему.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.security import admin_only
from app.handlers.admin.keyboards import get_admin_dashboard_keyboard
from app.handlers.common.utils import safe_edit_text
from app.core.runtime_context import get_bot_start_time

admin_base_router = Router()
logger = logging.getLogger(__name__)


async def _build_admin_menu(message_or_callback) -> tuple[str, InlineKeyboardMarkup]:
    """Build the bot-side admin entry message: open dashboard +
    reset password. The full set of in-bot admin tools has moved to
    the web dashboard; this command is now just the front door."""
    from app.api.dashboard.auth import issue_login_token
    from app.services import admin_auth

    enabled = getattr(config, "DASHBOARD_ENABLED", False)
    has_password = False
    try:
        has_password = await admin_auth.credentials_exist()
    except Exception:
        pass

    rows: list[list[InlineKeyboardButton]] = []
    if enabled:
        try:
            token = issue_login_token(_admin_id(message_or_callback))
            url = f"{config.DASHBOARD_BASE_URL.rstrip('/')}/dashboard/?login={token}"
            rows.append([InlineKeyboardButton(text="🛡 Открыть дашборд", url=url)])
        except Exception as e:
            logger.warning("DASHBOARD_LINK_FAIL: %s", e)

    rows.append([InlineKeyboardButton(
        text="🔄 Сбросить пароль" if has_password else "🆕 Установить пароль",
        callback_data="admin:reset_password",
    )])

    if has_password:
        body = (
            "🛡 <b>Atlas Admin</b>\n\n"
            "Открой дашборд — войдёшь по уже установленному логину и паролю.\n\n"
            "Если забыл пароль — жми <b>«Сбросить пароль»</b>, "
            "потом снова открой дашборд и придумай новый."
        )
    else:
        body = (
            "🛡 <b>Atlas Admin</b>\n\n"
            "Это твой первый вход. Нажми <b>«Открыть дашборд»</b> — там "
            "тебя попросят придумать логин и пароль. "
            "После этого ссылка перестанет автоматически впускать "
            "в дашборд; для входа понадобятся логин/пароль."
        )

    # Подсказка про iPhone.
    #
    # Кнопка выше — обычная url-кнопка, и на iOS Telegram открывает её во
    # встроенном браузере. Там не регистрируется service worker, нет
    # PushManager и нет пункта «На экран Домой», поэтому push подключить
    # невозможно в принципе. Это ограничение Apple: Web Push на iOS работает
    # только у веб-приложения, добавленного на домашний экран из полноценного
    # Safari. Технически исправить нечего — можно только провести админа по
    # маршруту, поэтому маршрут написан прямо здесь, а не только в дашборде.
    if enabled:
        body += (
            "\n\n📱 <b>Чтобы приходили push на iPhone</b>\n"
            "1. «…» вверху справа → <b>Открыть в Safari</b>\n"
            "2. «Поделиться» → <b>На экран Домой</b>\n"
            "3. Запусти иконку Atlas Admin с домашнего экрана\n"
            "4. Настройки → <b>Подключить push</b>\n\n"
            "Во встроенном браузере Telegram push не подключается — "
            "это ограничение iOS. Пока push не настроен, важные уведомления "
            "приходят сюда, в Telegram."
        )

    return body, InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_id(obj) -> int:
    if hasattr(obj, "from_user") and obj.from_user is not None:
        return int(obj.from_user.id)
    return int(config.ADMIN_TELEGRAM_ID)


@admin_base_router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message):
    """Web-dashboard entry — magic-link + password reset button.

    The full in-bot admin menu has moved to the web dashboard; this
    command intentionally has nothing else."""
    body, kb = await _build_admin_menu(message)
    await message.answer(body, reply_markup=kb, parse_mode="HTML")


@admin_base_router.callback_query(F.data == "admin:reset_password")
@admin_only
async def callback_reset_password(callback: CallbackQuery):
    """Confirm-then-clear admin web credentials + every active
    session. Next dashboard visit will ask the admin to set new
    login/password."""
    try:
        await callback.answer()
    except Exception:
        pass
    rows = [
        [InlineKeyboardButton(
            text="⚠️ Да, сбросить",
            callback_data="admin:reset_password_confirm",
        )],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="admin:reset_password_cancel",
        )],
    ]
    text = (
        "⚠️ <b>Сбросить пароль?</b>\n\n"
        "Будет удалён логин/пароль и все активные сессии. "
        "При следующем открытии дашборда ты заново придумаешь "
        "логин и пароль через magic-ссылку.\n\n"
        "Старые открытые вкладки/PWA на устройствах разлогинятся."
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )


@admin_base_router.callback_query(F.data == "admin:reset_password_cancel")
@admin_only
async def callback_reset_password_cancel(callback: CallbackQuery):
    try:
        await callback.answer("Отменено")
    except Exception:
        pass
    body, kb = await _build_admin_menu(callback)
    try:
        await callback.message.edit_text(body, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(body, reply_markup=kb, parse_mode="HTML")


@admin_base_router.callback_query(F.data == "admin:reset_password_confirm")
@admin_only
async def callback_reset_password_confirm(callback: CallbackQuery):
    from app.services import admin_auth
    try:
        await callback.answer()
    except Exception:
        pass

    try:
        ok = await admin_auth.clear_credentials()
    except Exception as e:
        logger.exception("reset_password_confirm clear_credentials error: %s", e)
        ok = False

    if not ok:
        try:
            await callback.message.answer("❌ Не удалось сбросить. Попробуй ещё раз.")
        except Exception:
            pass
        return

    body, kb = await _build_admin_menu(callback)
    final_body = (
        "✅ <b>Сброшено</b>\n\n"
        "Логин и пароль удалены, все сессии закрыты.\n\n"
        f"{body}"
    )
    try:
        await callback.message.edit_text(
            final_body, reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            final_body, reply_markup=kb, parse_mode="HTML",
        )


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
