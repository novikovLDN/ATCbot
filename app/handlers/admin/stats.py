"""
Admin stats handlers: promo_stats, metrics, analytics, referral_stats.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.security import (
    validate_telegram_id,
    require_admin,
    log_security_warning,
    log_audit_event,
)
from app.handlers.common.states import AdminReferralSearch
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_stats_router = Router()
logger = logging.getLogger(__name__)

async def format_promo_stats_text(stats: list) -> str:
    """Форматировать статистику промокодов в текст"""
    if not stats:
        return "Промокоды не найдены."
    text = "📊 Статистика промокодов\n\n"
    for promo in stats:
        code = promo.get("code", "?")
        discount_percent = promo.get("discount_percent", 0)
        max_uses = promo.get("max_uses")
        used_count = promo.get("used_count", 0)
        is_eff = promo.get("is_effective_active", promo.get("is_active", False))
        text += f"{code}\n"
        text += f"— Скидка: {discount_percent}%\n"
        if max_uses is not None:
            text += f"— Использовано: {used_count} / {max_uses}\n"
            text += "— Статус: активен\n" if is_eff else "— Статус: неактивен\n"
        else:
            text += f"— Использовано: {used_count}\n"
            text += "— Статус: активен\n" if is_eff else "— Статус: неактивен\n"
        text += "\n"
    return text


def get_promo_stats_keyboard(stats: list, language: str) -> InlineKeyboardMarkup:
    """Клавиатура со статистикой и кнопками деактивации"""
    from app.i18n import get_text as i18n_get_text
    rows = []
    seen_codes = set()
    for promo in stats:
        code = promo.get("code")
        promo_id = promo.get("id")
        is_eff = promo.get("is_effective_active", promo.get("is_active", False))
        if code and promo_id and is_eff and code not in seen_codes:
            seen_codes.add(code)
            rows.append([
                InlineKeyboardButton(
                    text=f"⛔ Деактивировать {code}",
                    callback_data=f"admin:deactivate_promo:{promo_id}"
                )
            ])
    rows.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@admin_stats_router.message(Command("promo_stats"))
async def cmd_promo_stats(message: Message):
    """Команда для просмотра статистики промокодов (только для администратора)"""
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate telegram_id
    telegram_id = message.from_user.id
    is_valid, error = validate_telegram_id(telegram_id)
    if not is_valid:
        log_security_warning(
            event="Invalid telegram_id in promo_stats command",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": error}
        )
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.try_later"), parse_mode="HTML")
        return
    
    # STEP 4 — PART B: AUTHORIZATION GUARDS
    # Explicit admin authorization check - fail closed
    is_authorized, auth_error = require_admin(telegram_id)
    if not is_authorized:
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.access_denied", "error_access_denied"), parse_mode="HTML")
        return
    
    # STEP 4 — PART F: SECURITY LOGGING POLICY
    # Log admin action
    log_audit_event(
        event="admin_promo_stats_viewed",
        telegram_id=telegram_id,
        correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None
    )
    
    try:
        # Получаем статистику промокодов
        stats = await database.get_promo_stats()
        
        # Формируем текст ответа
        text = await format_promo_stats_text(stats)
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.promo_stats"), parse_mode="HTML")

@admin_stats_router.callback_query(F.data == "admin_promo_stats")
async def callback_admin_promo_stats(callback: CallbackQuery):
    """Обработчик кнопки статистики промокодов в админ-дашборде"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    try:
        stats = await database.get_promo_stats()
        text = await format_promo_stats_text(stats)
        keyboard = get_promo_stats_keyboard(stats, language)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.promo_stats"), show_alert=True)


@admin_stats_router.callback_query(F.data.startswith("admin:deactivate_promo:"))
async def callback_admin_deactivate_promo(callback: CallbackQuery):
    """Подтверждение деактивации промокода"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    try:
        promo_id = int(callback.data.split(":")[-1])
        language = await resolve_user_language(callback.from_user.id)
        text = f"⚠️ Деактивировать промокод #{promo_id}?\n\nЭто действие необратимо."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, деактивировать", callback_data=f"admin:deactivate_promo_confirm:{promo_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin_promo_stats"),
            ]
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
    except (ValueError, IndexError) as e:
        logger.warning(f"Invalid deactivate promo callback: {callback.data} {e}")
        await callback.answer("Ошибка параметра", show_alert=True)


@admin_stats_router.callback_query(F.data.startswith("admin:deactivate_promo_confirm:"))
async def callback_admin_deactivate_promo_confirm(callback: CallbackQuery):
    """Фактическая деактивация промокода после подтверждения"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    try:
        promo_id = int(callback.data.split(":")[-1])
        ok = await database.deactivate_promocode(promo_id=promo_id)
        language = await resolve_user_language(callback.from_user.id)
        if ok:
            stats = await database.get_promo_stats()
            text = await format_promo_stats_text(stats)
            keyboard = get_promo_stats_keyboard(stats, language)
            await safe_edit_text(callback.message, text, reply_markup=keyboard)
            await callback.answer("✅ Промокод деактивирован", show_alert=True)
        else:
            await callback.answer("❌ Не удалось деактивировать", show_alert=True)
    except (ValueError, IndexError) as e:
        logger.warning(f"Invalid deactivate promo confirm callback: {callback.data} {e}")
        await callback.answer("Ошибка параметра", show_alert=True)
    except Exception as e:
        logger.exception(f"Error deactivating promo: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.promo_stats"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:metrics")
async def callback_admin_metrics(callback: CallbackQuery):
    """Раздел Метрики"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        metrics = await database.get_business_metrics()
        
        text = "📈 Бизнес-метрики\n\n"
        
        # Среднее время подтверждения оплаты
        approval_time = metrics.get('avg_payment_approval_time_seconds')
        if approval_time:
            minutes = int(approval_time / 60)
            seconds = int(approval_time % 60)
            text += f"⏱ Среднее время подтверждения оплаты: {minutes} мин {seconds} сек\n"
        else:
            text += "⏱ Среднее время подтверждения оплаты: нет данных\n"
        
        # Среднее время жизни подписки
        lifetime = metrics.get('avg_subscription_lifetime_days')
        if lifetime:
            text += f"📅 Среднее время жизни подписки: {lifetime:.1f} дней\n"
        else:
            text += "📅 Среднее время жизни подписки: нет данных\n"
        
        # Количество продлений на пользователя
        renewals = metrics.get('avg_renewals_per_user', 0.0)
        text += f"🔄 Среднее количество продлений на пользователя: {renewals:.2f}\n"
        
        # Процент подтвержденных платежей
        approval_rate = metrics.get('approval_rate_percent', 0.0)
        text += f"✅ Процент подтвержденных платежей: {approval_rate:.1f}%\n"

        # Referral analytics
        try:
            ref = await database.get_referral_analytics()
            text += f"\n━━━ Реферальная программа ━━━\n"
            text += f"👥 Приглашённых: {ref.get('referred_users_count', 0)}\n"
            text += f"💰 Доход от рефералов: {ref.get('referral_revenue', 0):.2f} ₽\n"
            text += f"💸 Выплачено кешбэка: {ref.get('cashback_paid', 0):.2f} ₽\n"
            text += f"📈 Чистая прибыль: {ref.get('net_profit', 0):.2f} ₽\n"
        except Exception:
            pass

        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()
        
        # Логируем действие
        await database._log_audit_event_atomic_standalone("admin_view_metrics", callback.from_user.id, None, "Admin viewed business metrics")
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_metrics: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.metrics"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:stats")
async def callback_admin_stats(callback: CallbackQuery):
    """Раздел Статистика"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        stats = await database.get_admin_stats()

        text = "📊 Статистика\n\n"
        text += "━━━ Пользователи ━━━\n"
        text += f"👥 Всего: {stats['total_users']}\n"

        # Extended stats (if available)
        try:
            ext = await database.get_extended_bot_stats()
            text += f"🆕 Новых сегодня: {ext.get('new_today', '—')}\n"
            text += f"🎁 Trial: {ext.get('total_trial', '—')} ({ext.get('trial_rate', 0)}%)\n"
            text += f"📈 Конверсия: {ext.get('conversion_rate', 0)}%\n"
            text += f"📉 Отток: {ext.get('churn_rate', 0)}%\n"
        except Exception:
            pass

        text += f"\n━━━ Подписки ━━━\n"
        text += f"🔑 Активных: {stats['active_subscriptions']}\n"
        text += f"⛔ Истёкших: {stats['expired_subscriptions']}\n"

        text += f"\n━━━ Платежи ━━━\n"
        text += f"💳 Всего: {stats['total_payments']}\n"
        text += f"✅ Подтверждено: {stats['approved_payments']}"

        # Daily summary
        try:
            daily = await database.get_daily_summary(None)
            text += f"\n\n━━━ Сегодня ━━━\n"
            text += f"💰 Доход: {daily.get('revenue', 0):.2f} ₽\n"
            text += f"💳 Платежей: {daily.get('payments_count', 0)}\n"
            text += f"🆕 Новых польз.: {daily.get('new_users', 0)}\n"
            text += f"🔑 Новых подп.: {daily.get('new_subscriptions', 0)}"
        except Exception:
            pass

        await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
        await callback.answer()

        await database._log_audit_event_atomic_standalone("admin_view_stats", callback.from_user.id, None, "Admin viewed statistics")

    except Exception as e:
        logging.exception(f"Error in callback_admin_stats: {e}")
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.stats"), show_alert=True)


def _fmt_rub(kopecks: int) -> str:
    """Compact ruble amount: 597, 2.4к, 98к, 1.2М."""
    rub = (kopecks or 0) / 100
    if rub >= 1_000_000:
        return f"{rub / 1_000_000:.1f}М"
    if rub >= 10_000:
        return f"{rub / 1000:.0f}к"
    if rub >= 1000:
        return f"{rub / 1000:.1f}к"
    return f"{rub:.0f}"


def _format_purchase_stats(data: dict) -> str:
    """Render the purchase breakdown as two aligned <pre> tables."""
    windows = [
        ("24ч", "24h"), ("7д", "7d"), ("30д", "30d"),
        ("180д", "180d"), ("1г", "365d"), ("всё", "all"),
    ]
    cats = [
        ("Basic", "basic"), ("Plus", "plus"),
        ("Basic комбо", "basic_combo"), ("Plus комбо", "plus_combo"),
        ("MT Proxy", "proxy"),
    ]
    LW, CW = 12, 7

    def _row(label: str, cells: list) -> str:
        return label.ljust(LW) + "".join(str(c).rjust(CW) for c in cells)

    def _table(field: str, formatter) -> str:
        lines = [_row("", [w[0] for w in windows])]
        totals = [0] * len(windows)
        for clabel, ckey in cats:
            raw = [data[ckey][wkey][field] for _, wkey in windows]
            for i, v in enumerate(raw):
                totals[i] += v
            lines.append(_row(clabel, [formatter(v) for v in raw]))
        lines.append("─" * (LW + CW * len(windows)))
        lines.append(_row("Итого", [formatter(v) for v in totals]))
        return "\n".join(lines)

    count_tbl = _table("count", lambda v: str(v))
    rev_tbl = _table("revenue", _fmt_rub)
    return (
        "📦 <b>Покупки по тарифам</b>\n\n"
        "Количество покупок:\n"
        f"<pre>{count_tbl}</pre>\n"
        "Выручка, ₽ (≈):\n"
        f"<pre>{rev_tbl}</pre>\n"
        "<i>Источник — завершённые покупки. Время считается по началу "
        "оформления (оплата проходит в пределах 15 мин). Оплаты через "
        "Telegram Stars в выручке учитываются приблизительно.</i>"
    )


@admin_stats_router.callback_query(F.data == "admin:purchase_stats")
async def callback_admin_purchase_stats(callback: CallbackQuery):
    """Раздел: покупки по тарифам (Basic / Plus / комбо / MT Proxy)."""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    try:
        data = await database.get_purchase_breakdown()
        text = _format_purchase_stats(data)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:purchase_stats")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

        await database._log_audit_event_atomic_standalone(
            "admin_view_purchase_stats", callback.from_user.id, None,
            "Admin viewed purchase breakdown",
        )
    except Exception as e:
        logging.exception(f"Error in callback_admin_purchase_stats: {e}")
        await callback.answer(i18n_get_text(language, "errors.stats"), show_alert=True)



@admin_stats_router.callback_query(F.data == "admin:analytics")

async def callback_admin_analytics(callback: CallbackQuery):

    """📊 Финансовая аналитика - базовые метрики"""

    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    try:

        # Получаем базовые метрики (оптимизированные запросы)

        total_revenue = await database.get_total_revenue()

        paying_users_count = await database.get_paying_users_count()

        arpu = await database.get_arpu()

        avg_ltv = await database.get_ltv()

        

        # Формируем отчет (краткий и понятный)

        text = (

            f"📊 Финансовая аналитика\n\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"💰 Общий доход\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   {total_revenue:,.2f} ₽\n\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"👥 Платящие пользователи\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   {paying_users_count} чел.\n\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"📈 ARPU (Average Revenue Per User)\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   {arpu:,.2f} ₽\n\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"💎 Средний LTV (Lifetime Value)\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   {avg_ltv:,.2f} ₽\n"

        )

        

        # Клавиатура

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📈 Рост пользователей", callback_data="admin:analytics:growth")],
            [InlineKeyboardButton(text="📊 Расширенная статистика", callback_data="admin:analytics:extended")],
            [InlineKeyboardButton(text="📅 Ежемесячная сводка", callback_data="admin:analytics:monthly")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.refresh"), callback_data="admin:analytics")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]
        ])

        

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        await callback.answer()

        

        # Логируем действие

        await database._log_audit_event_atomic_standalone(

            "admin_view_analytics",

            callback.from_user.id,

            None,

            "Admin viewed financial analytics"

        )

        

    except Exception as e:

        logger.exception(f"Error in admin analytics: {e}")

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "errors.analytics"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:analytics:monthly")

async def callback_admin_analytics_monthly(callback: CallbackQuery):

    """Ежемесячная сводка"""

    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    try:

        now = datetime.now(timezone.utc)

        current_month = await database.get_monthly_summary(now.year, now.month)

        

        # Предыдущий месяц

        if now.month == 1:

            prev_month = await database.get_monthly_summary(now.year - 1, 12)

        else:

            prev_month = await database.get_monthly_summary(now.year, now.month - 1)

        

        text = (

            f"📅 Ежемесячная сводка\n\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"📊 Текущий месяц ({current_month['year']}-{current_month['month']:02d})\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   Доход: {current_month['revenue']:.2f} ₽\n"

            f"   Платежей: {current_month['payments_count']}\n"

            f"   Новых пользователей: {current_month['new_users']}\n"

            f"   Новых подписок: {current_month['new_subscriptions']}\n\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"📊 Предыдущий месяц ({prev_month['year']}-{prev_month['month']:02d})\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   Доход: {prev_month['revenue']:.2f} ₽\n"

            f"   Платежей: {prev_month['payments_count']}\n"

            f"   Новых пользователей: {prev_month['new_users']}\n"

            f"   Новых подписок: {prev_month['new_subscriptions']}\n\n"

        )

        

        # Сравнение

        revenue_change = current_month['revenue'] - prev_month['revenue']

        revenue_change_percent = (revenue_change / prev_month['revenue'] * 100) if prev_month['revenue'] > 0 else 0

        

        text += (

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"📈 Изменение дохода\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"   Изменение: {revenue_change:+.2f} ₽ ({revenue_change_percent:+.1f}%)\n"

        )

        

        keyboard = InlineKeyboardMarkup(inline_keyboard=[

            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_analytics"), callback_data="admin:analytics")]

        ])

        

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        await callback.answer()

        

    except Exception as e:

        logger.exception(f"Error in monthly analytics: {e}")

        await callback.answer("Ошибка при получении ежемесячной сводки", show_alert=True)


# ==================== АНАЛИТИКА ПО ПЕРИОДАМ (РОСТ ПОЛЬЗОВАТЕЛЕЙ) ====================

PERIOD_OPTIONS = [
    ("6ч", 6),
    ("24ч", 24),
    ("3д", 72),
    ("7д", 168),
    ("14д", 336),
    ("28д", 672),
    ("60д", 1440),
    ("180д", 4320),
    ("365д", 8760),
]


def _get_growth_period_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура выбора периода для аналитики роста."""
    rows = []
    row = []
    for label, hours in PERIOD_OPTIONS:
        row.append(InlineKeyboardButton(text=label, callback_data=f"admin:growth:{hours}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:analytics")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_stats_router.callback_query(F.data == "admin:analytics:growth")
async def callback_admin_analytics_growth(callback: CallbackQuery):
    """Экран выбора периода для аналитики роста пользователей"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)
    await callback.answer()
    text = "📈 Рост пользователей\n\nВыберите период для просмотра статистики:"
    await safe_edit_text(callback.message, text, reply_markup=_get_growth_period_keyboard(language))


@admin_stats_router.callback_query(F.data.startswith("admin:growth:"))
async def callback_admin_growth_period(callback: CallbackQuery):
    """Показать аналитику за выбранный период"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)

    try:
        hours = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    # Find period label
    period_label = next((label for label, h in PERIOD_OPTIONS if h == hours), f"{hours}ч")

    try:
        stats = await database.get_analytics_by_period(hours)

        trial_rate = round((stats["trial_activated"] / stats["new_users"] * 100), 1) if stats["new_users"] > 0 else 0
        total_trial_rate = round((stats["total_trial_used"] / stats["total_users"] * 100), 1) if stats["total_users"] > 0 else 0

        text = f"📈 Аналитика за {period_label}\n\n"
        text += f"👥 Новые пользователи: {stats['new_users']}\n"
        text += f"🎁 Активировали пробный период: {stats['trial_activated']}\n"
        text += f"📊 Конверсия в trial: {trial_rate}%\n"
        text += f"🔑 Новые подписки: {stats['new_subscriptions']}\n\n"
        text += f"— Общие показатели —\n"
        text += f"👥 Всего пользователей: {stats['total_users']}\n"
        text += f"🎁 Всего trial активаций: {stats['total_trial_used']} ({total_trial_rate}%)"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:growth:{hours}")],
            [InlineKeyboardButton(text="◀️ Назад к периодам", callback_data="admin:analytics:growth")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:analytics")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

        await database._log_audit_event_atomic_standalone(
            "admin_view_growth_analytics",
            callback.from_user.id,
            None,
            f"Viewed growth analytics for period: {period_label}"
        )

    except Exception as e:
        logger.exception(f"Error in growth analytics: {e}")
        await callback.answer("Ошибка при получении аналитики", show_alert=True)


# ==================== РАСШИРЕННАЯ СТАТИСТИКА БОТА ====================

@admin_stats_router.callback_query(F.data == "admin:analytics:extended")
async def callback_admin_extended_stats(callback: CallbackQuery):
    """Расширенная статистика и мониторинг бота"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)

    try:
        stats = await database.get_extended_bot_stats()

        text = "📊 Расширенная статистика\n\n"
        text += "— Пользователи —\n"
        text += f"👥 Всего: {stats['total_users']}\n"
        text += f"🆕 Новых сегодня: {stats['new_today']}\n"
        text += f"🎁 Trial активаций: {stats['total_trial']} ({stats['trial_rate']}%)\n\n"

        text += "— Подписки —\n"
        text += f"🔑 Активных: {stats['active_subs']}\n"
        text += f"⛔ Истёкших: {stats['expired_subs']}\n"
        text += f"📈 Конверсия: {stats['conversion_rate']}%\n"
        text += f"📉 Отток: {stats['churn_rate']}%\n"
        text += f"🔄 Ср. подписок на юзера: {stats['avg_subs_per_user']}\n\n"

        text += "— Финансы —\n"
        text += f"💰 Общая выручка: {stats['total_revenue']}₽\n"
        text += f"📅 MRR (30 дней): {stats['mrr']}₽\n\n"

        text += "— Система —\n"
        text += f"📢 Рассылок отправлено: {stats['total_broadcasts']}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:analytics:extended")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:analytics")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

        await database._log_audit_event_atomic_standalone(
            "admin_view_extended_stats",
            callback.from_user.id,
            None,
            "Admin viewed extended bot statistics"
        )

    except Exception as e:
        logger.exception(f"Error in extended stats: {e}")
        await callback.answer("Ошибка при получении расширенной статистики", show_alert=True)
