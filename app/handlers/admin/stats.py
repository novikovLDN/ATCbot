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
        await message.answer(i18n_get_text(language, "errors.try_later"))
        return
    
    # STEP 4 — PART B: AUTHORIZATION GUARDS
    # Explicit admin authorization check - fail closed
    is_authorized, auth_error = require_admin(telegram_id)
    if not is_authorized:
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.access_denied", "error_access_denied"))
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
        await message.answer(text)
    except Exception as e:
        logger.error(f"Error getting promo stats: {e}")
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.promo_stats"))

@admin_stats_router.callback_query(F.data == "admin_promo_stats")
async def callback_admin_promo_stats(callback: CallbackQuery):
    """Обработчик кнопки статистики промокодов в админ-дашборде"""
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
        await callback.answer("Ошибка загрузки метрик", show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:stats")
async def callback_admin_stats(callback: CallbackQuery):
    """Раздел Статистика"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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


@admin_stats_router.callback_query(F.data == "admin:referral_stats")
async def callback_admin_referral_stats(callback: CallbackQuery):
    """Реферальная статистика - главный экран с общей статистикой"""
    logger.info("REFERRAL_STATS_REQUESTED telegram_id=%s", callback.from_user.id)
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    user = await database.get_user(callback.from_user.id)

    language = await resolve_user_language(callback.from_user.id)

    await callback.answer()

    

    try:

        # Получаем общую статистику

        overall_stats = await database.get_referral_overall_stats()

        

        # Получаем топ рефереров (первые 10, отсортированные по доходу)

        top_referrers = await database.get_admin_referral_stats(

            search_query=None,

            sort_by="total_revenue",

            sort_order="DESC",

            limit=10,

            offset=0

        )

        

        # Безопасная обработка статистики с дефолтами

        if not overall_stats:

            overall_stats = {

                "total_referrers": 0,

                "total_referrals": 0,

                "total_paid_referrals": 0,

                "total_revenue": 0.0,

                "total_cashback_paid": 0.0,

                "avg_cashback_per_referrer": 0.0

            }

        

        # Безопасное извлечение значений с дефолтами

        total_referrers = database.safe_int(overall_stats.get("total_referrers", 0))

        total_referrals = database.safe_int(overall_stats.get("total_referrals", 0))

        total_paid_referrals = database.safe_int(overall_stats.get("total_paid_referrals", 0))

        total_revenue = database.safe_float(overall_stats.get("total_revenue", 0.0))

        total_cashback_paid = database.safe_float(overall_stats.get("total_cashback_paid", 0.0))

        avg_cashback_per_referrer = database.safe_float(overall_stats.get("avg_cashback_per_referrer", 0.0))

        

        # Формируем текст с общей статистикой

        text = "📈 Реферальная статистика\n\n"

        text += "📊 Общая статистика:\n"

        text += f"• Всего рефереров: {total_referrers}\n"

        text += f"• Всего приглашённых: {total_referrals}\n"

        text += f"• Всего оплат: {total_paid_referrals}\n"

        text += f"• Общий доход: {total_revenue:.2f} ₽\n"

        text += f"• Выплачено кешбэка: {total_cashback_paid:.2f} ₽\n"

        text += f"• Средний кешбэк на реферера: {avg_cashback_per_referrer:.2f} ₽\n\n"

        

        # Топ рефереров (безопасная обработка)

        if top_referrers:

            text += "🏆 Топ рефереров:\n\n"

            for idx, stat in enumerate(top_referrers[:10], 1):

                try:

                    # Безопасное извлечение значений

                    referrer_id = stat.get("referrer_id", "N/A")

                    username = stat.get("username") or f"ID{referrer_id}"

                    invited_count = database.safe_int(stat.get("invited_count", 0))

                    paid_count = database.safe_int(stat.get("paid_count", 0))

                    conversion = database.safe_float(stat.get("conversion_percent", 0.0))

                    revenue = database.safe_float(stat.get("total_invited_revenue", 0.0))

                    cashback = database.safe_float(stat.get("total_cashback_paid", 0.0))

                    cashback_percent = database.safe_int(stat.get("current_cashback_percent", 10))

                    

                    text += f"{idx}. @{username} (ID: {referrer_id})\n"

                    text += f"   Оплативших: {paid_count} | Уровень: {cashback_percent}%\n"

                    text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽\n\n"

                except Exception as e:

                    logger.warning(f"Error processing referrer stat in admin dashboard: {e}, stat={stat}")

                    continue  # Пропускаем проблемную строку

        else:

            text += "🏆 Топ рефереров:\nРефереры не найдены.\n\n"

        

        # Клавиатура с кнопками

        keyboard = InlineKeyboardMarkup(inline_keyboard=[

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_history"), callback_data="admin:referral_history"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_top"), callback_data="admin:referral_top")

            ],

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")

            ],

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")

            ],

            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]

        ])

        

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        

        # Логируем просмотр статистики

        try:

            await database._log_audit_event_atomic_standalone(

                "admin_view_referral_stats", 

                callback.from_user.id, 

                None, 

                f"Admin viewed referral stats: {total_referrers} referrers"

            )

        except Exception as log_error:

            logger.warning(f"Error logging admin referral stats view: {log_error}")

        

    except Exception as e:

        # Структурированное логирование для разработчиков

        logger.exception(

            f"admin_referral_stats_failed: telegram_id={callback.from_user.id}, handler=callback_admin_referral_stats, error={type(e).__name__}: {e}"

        )

        

        # Graceful fallback: показываем пустую статистику, а не ошибку

        try:

            fallback_text = (

                "📈 Реферальная статистика\n\n"

                "📊 Общая статистика:\n"

                "• Всего рефереров: 0\n"

                "• Всего приглашённых: 0\n"

                "• Всего оплат: 0\n"

                "• Общий доход: 0.00 ₽\n"

                "• Выплачено кешбэка: 0.00 ₽\n"

                "• Средний кешбэк на реферера: 0.00 ₽\n\n"

                "🏆 Топ рефереров:\nРефереры не найдены.\n\n"

            )

            

            keyboard = InlineKeyboardMarkup(inline_keyboard=[

                [

                    InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_history"), callback_data="admin:referral_history"),

                    InlineKeyboardButton(text=i18n_get_text(language, "admin.referral_top"), callback_data="admin:referral_top")

                ],

                [

                    InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),

                    InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")

                ],

                [

                    InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),

                    InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")

                ],

                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]

            ])

            

            await safe_edit_text(callback.message, fallback_text, reply_markup=keyboard)

        except Exception as fallback_error:

            logger.exception(f"Error in fallback admin referral stats: {fallback_error}")

            user = await database.get_user(callback.from_user.id)

            language = await resolve_user_language(callback.from_user.id)

            await callback.answer(i18n_get_text(language, "errors.referral_stats"), show_alert=True)

@admin_stats_router.callback_query(F.data.startswith("admin:referral_sort:"))

async def callback_admin_referral_sort(callback: CallbackQuery):

    """Сортировка реферальной статистики"""

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    user = await database.get_user(callback.from_user.id)

    language = await resolve_user_language(callback.from_user.id)

    await callback.answer()

    

    try:

        # Извлекаем параметр сортировки

        sort_by = callback.data.split(":")[-1]

        

        # Получаем статистику с новой сортировкой

        stats_list = await database.get_admin_referral_stats(

            search_query=None,

            sort_by=sort_by,

            sort_order="DESC",

            limit=20,

            offset=0

        )

        

        if not stats_list:

            text = "📊 Реферальная статистика\n\nРефереры не найдены."

            keyboard = InlineKeyboardMarkup(inline_keyboard=[

                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]

            ])

            await safe_edit_text(callback.message, text, reply_markup=keyboard)

            return

        

        # Формируем текст со статистикой

        sort_labels = {

            "total_revenue": "По доходу",

            "invited_count": "По приглашениям",

            "cashback_paid": "По кешбэку"

        }

        sort_label = sort_labels.get(sort_by, "По доходу")

        

        text = f"📊 Реферальная статистика\nСортировка: {sort_label}\n\n"

        text += f"Всего рефереров: {len(stats_list)}\n\n"

        

        # Показываем топ-10 рефереров

        for idx, stat in enumerate(stats_list[:10], 1):

            # Safe extraction: use .get() to avoid KeyError

            username = stat.get("username") or f"ID{stat.get('referrer_id', 'N/A')}"

            invited_count = stat.get("invited_count", 0)

            paid_count = stat.get("paid_count", 0)

            conversion = stat.get("conversion_percent", 0.0)

            revenue = stat.get("total_invited_revenue", 0.0)

            cashback = stat.get("total_cashback_paid", 0.0)

            cashback_percent = stat.get("current_cashback_percent", 0.0)

            referrer_id = stat.get("referrer_id", "N/A")

            

            text += f"{idx}. @{username} (ID: {referrer_id})\n"

            text += f"   Приглашено: {invited_count} | Оплатили: {paid_count} ({conversion}%)\n"

            text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽ ({cashback_percent}%)\n\n"

        

        if len(stats_list) > 10:

            text += f"... и еще {len(stats_list) - 10} рефереров\n\n"

        

        # Клавиатура с кнопками фильтров и сортировки

        keyboard = InlineKeyboardMarkup(inline_keyboard=[

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")

            ],

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")

            ],

            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]

        ])

        

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        

    except Exception as e:

        logging.exception(f"Error in callback_admin_referral_sort: {e}")

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "errors.stats_sort"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:referral_search")

async def callback_admin_referral_search(callback: CallbackQuery, state: FSMContext):

    """Поиск реферальной статистики"""

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    user = await database.get_user(callback.from_user.id)

    language = await resolve_user_language(callback.from_user.id)

    await callback.answer()

    

    text = "🔍 Поиск реферальной статистики\n\nВведите telegram_id или username для поиска:"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[

        [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel"), callback_data="admin:referral_stats")]

    ])

    

    await safe_edit_text(callback.message, text, reply_markup=keyboard)

    await state.set_state(AdminReferralSearch.waiting_for_search_query)

@admin_stats_router.message(AdminReferralSearch.waiting_for_search_query)

async def process_admin_referral_search(message: Message, state: FSMContext):

    """Обработка поискового запроса"""

    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(message.from_user.id)

        await message.answer(i18n_get_text(language, "admin.access_denied"))

        await state.clear()

        return

    

    language = await resolve_user_language(message.from_user.id)

    search_query = message.text.strip()

    await state.clear()

    

    try:

        # Получаем статистику с поисковым запросом

        stats_list = await database.get_admin_referral_stats(

            search_query=search_query,

            sort_by="total_revenue",

            sort_order="DESC",

            limit=20,

            offset=0

        )

        

        if not stats_list:

            text = f"📊 Реферальная статистика\n\nПо запросу '{search_query}' ничего не найдено."

            keyboard = InlineKeyboardMarkup(inline_keyboard=[

                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]

            ])

            await message.answer(text, reply_markup=keyboard)

            return

        

        # Формируем текст со статистикой

        text = f"📊 Реферальная статистика\nПоиск: '{search_query}'\n\n"

        text += f"Найдено рефереров: {len(stats_list)}\n\n"

        

        # Показываем результаты поиска

        for idx, stat in enumerate(stats_list[:10], 1):

            # Safe extraction: use .get() to avoid KeyError

            username = stat.get("username") or f"ID{stat.get('referrer_id', 'N/A')}"

            invited_count = stat.get("invited_count", 0)

            paid_count = stat.get("paid_count", 0)

            conversion = stat.get("conversion_percent", 0.0)

            revenue = stat.get("total_invited_revenue", 0.0)

            cashback = stat.get("total_cashback_paid", 0.0)

            cashback_percent = stat.get("current_cashback_percent", 0.0)

            referrer_id = stat.get("referrer_id", "N/A")

            

            text += f"{idx}. @{username} (ID: {referrer_id})\n"

            text += f"   Приглашено: {invited_count} | Оплатили: {paid_count} ({conversion}%)\n"

            text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽ ({cashback_percent}%)\n\n"

        

        if len(stats_list) > 10:

            text += f"... и еще {len(stats_list) - 10} рефереров\n\n"

        

        # Клавиатура

        keyboard = InlineKeyboardMarkup(inline_keyboard=[

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")

            ],

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")

            ],

            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")]

        ])

        

        await message.answer(text, reply_markup=keyboard)

        

    except Exception as e:

        logging.exception(f"Error in process_admin_referral_search: {e}")

        language = await resolve_user_language(message.from_user.id)

        await message.answer(i18n_get_text(language, "errors.stats_search"))

@admin_stats_router.callback_query(F.data.startswith("admin:referral_detail:"))

async def callback_admin_referral_detail(callback: CallbackQuery):

    """Детальная информация по рефереру"""

    user = await database.get_user(callback.from_user.id)

    language = await resolve_user_language(callback.from_user.id)

    

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    await callback.answer()

    

    try:

        # Извлекаем referrer_id

        referrer_id = int(callback.data.split(":")[-1])

        

        # Получаем детальную информацию

        detail = await database.get_admin_referral_detail(referrer_id)

        

        if not detail:

            await callback.answer("Реферер не найден", show_alert=True)

            return

        

        # Формируем текст с детальной информацией

        username = detail["username"]

        invited_list = detail["invited_list"]

        

        text = f"📊 Детали реферера\n\n"

        text += f"@{username} (ID: {referrer_id})\n\n"

        text += f"Всего приглашено: {len(invited_list)}\n\n"

        

        if invited_list:

            text += "Приглашённые пользователи:\n\n"

            for idx, invited in enumerate(invited_list[:15], 1):  # Ограничение 15 записей для читаемости

                invited_username = invited["username"]

                registered_at = invited["registered_at"]

                first_payment = invited["first_payment_date"]

                purchase_amount = invited["purchase_amount"]

                cashback_amount = invited["cashback_amount"]

                

                text += f"{idx}. @{invited_username} (ID: {invited['invited_user_id']})\n"

                text += f"   Зарегистрирован: {registered_at.strftime('%Y-%m-%d') if registered_at else 'N/A'}\n"

                if first_payment:

                    text += f"   Первая оплата: {first_payment.strftime('%Y-%m-%d')}\n"

                    text += f"   Сумма: {purchase_amount:.2f} ₽ | Кешбэк: {cashback_amount:.2f} ₽\n"

                else:

                    text += f"   Оплаты нет\n"

                text += "\n"

            

            if len(invited_list) > 15:

                text += f"... и еще {len(invited_list) - 15} пользователей\n\n"

        else:

            text += "Приглашённые пользователи отсутствуют.\n\n"

        

        # Клавиатура

        keyboard = InlineKeyboardMarkup(inline_keyboard=[

            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back_to_stats"), callback_data="admin:referral_stats")]

        ])

        

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        

        # Логируем просмотр деталей

        await database._log_audit_event_atomic_standalone(

            "admin_view_referral_detail", 

            callback.from_user.id, 

            referrer_id, 

            f"Admin viewed referral detail for referrer_id={referrer_id}"

        )

        

    except Exception as e:

        logging.exception(f"Error in callback_admin_referral_detail: {e}")

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "errors.details"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:referral_history")

async def callback_admin_referral_history(callback: CallbackQuery):

    """История начислений реферального кешбэка"""

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    await callback.answer()

    

    try:

        # Получаем историю начислений (первые 20 записей)

        history = await database.get_referral_rewards_history(

            date_from=None,

            date_to=None,

            limit=20,

            offset=0

        )

        

        # Получаем общее количество для пагинации

        total_count = await database.get_referral_rewards_history_count()

        

        if not history:

            text = "📋 История начислений\n\nНачисления не найдены."

            keyboard = InlineKeyboardMarkup(inline_keyboard=[

                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]

            ])

            await safe_edit_text(callback.message, text, reply_markup=keyboard)

            return

        

        # Формируем текст с историей

        text = "📋 История начислений\n\n"

        text += f"Всего записей: {total_count}\n\n"

        

        for idx, reward in enumerate(history[:20], 1):

            referrer = reward["referrer_username"]

            buyer = reward["buyer_username"]

            purchase_amount = reward["purchase_amount"]

            percent = reward["percent"]

            reward_amount = reward["reward_amount"]

            created_at = reward["created_at"].strftime("%d.%m.%Y %H:%M") if reward["created_at"] else "N/A"

            

            text += f"{idx}. {created_at}\n"

            text += f"   Реферер: @{referrer} (ID: {reward['referrer_id']})\n"

            text += f"   Покупатель: @{buyer} (ID: {reward['buyer_id']})\n"

            text += f"   Покупка: {purchase_amount:.2f} ₽ | Кешбэк: {percent}% = {reward_amount:.2f} ₽\n\n"

        

        if total_count > 20:

            text += f"... и еще {total_count - 20} записей\n\n"

        

        # Клавиатура

        keyboard_buttons = []

        if total_count > 20:

            keyboard_buttons.append([

                InlineKeyboardButton(text=i18n_get_text(language, "admin.next_page"), callback_data="admin:referral_history:page:1")

            ])

        keyboard_buttons.append([

            InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")

        ])

        

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        

        # Логируем просмотр истории

        await database._log_audit_event_atomic_standalone(

            "admin_view_referral_history",

            callback.from_user.id,

            None,

            f"Admin viewed referral history: {len(history)} records"

        )

        

    except Exception as e:

        logging.exception(f"Error in callback_admin_referral_history: {e}")

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "errors.rewards_history"), show_alert=True)

@admin_stats_router.callback_query(F.data.startswith("admin:referral_history:page:"))

async def callback_admin_referral_history_page(callback: CallbackQuery):

    """Пагинация истории начислений"""

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    await callback.answer()

    

    try:

        # Извлекаем номер страницы

        page = int(callback.data.split(":")[-1])

        limit = 20

        offset = page * limit

        

        # Получаем историю начислений

        history = await database.get_referral_rewards_history(

            date_from=None,

            date_to=None,

            limit=limit,

            offset=offset

        )

        

        # Получаем общее количество

        total_count = await database.get_referral_rewards_history_count()

        total_pages = (total_count + limit - 1) // limit

        

        if not history:

            text = "📋 История начислений\n\nНачисления не найдены."

            keyboard = InlineKeyboardMarkup(inline_keyboard=[

                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]

            ])

            await safe_edit_text(callback.message, text, reply_markup=keyboard)

            return

        

        # Формируем текст

        text = f"📋 История начислений (стр. {page + 1}/{total_pages})\n\n"

        text += f"Всего записей: {total_count}\n\n"

        

        for idx, reward in enumerate(history, 1):

            referrer = reward["referrer_username"]

            buyer = reward["buyer_username"]

            purchase_amount = reward["purchase_amount"]

            percent = reward["percent"]

            reward_amount = reward["reward_amount"]

            created_at = reward["created_at"].strftime("%d.%m.%Y %H:%M") if reward["created_at"] else "N/A"

            

            text += f"{offset + idx}. {created_at}\n"

            text += f"   Реферер: @{referrer} (ID: {reward['referrer_id']})\n"

            text += f"   Покупатель: @{buyer} (ID: {reward['buyer_id']})\n"

            text += f"   Покупка: {purchase_amount:.2f} ₽ | Кешбэк: {percent}% = {reward_amount:.2f} ₽\n\n"

        

        # Клавиатура с пагинацией

        keyboard_buttons = []

        nav_buttons = []

        if page > 0:

            nav_buttons.append(InlineKeyboardButton(text=i18n_get_text(language, "admin.prev"), callback_data=f"admin:referral_history:page:{page - 1}"))

        if offset + limit < total_count:

            nav_buttons.append(InlineKeyboardButton(text=i18n_get_text(language, "admin.forward"), callback_data=f"admin:referral_history:page:{page + 1}"))

        if nav_buttons:

            keyboard_buttons.append(nav_buttons)

        keyboard_buttons.append([

            InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")

        ])

        

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        

    except Exception as e:

        logging.exception(f"Error in callback_admin_referral_history_page: {e}")

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "errors.rewards_history"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:referral_top")

async def callback_admin_referral_top(callback: CallbackQuery):

    """Топ рефереров - расширенный список"""

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)

        return

    

    await callback.answer()

    

    try:

        # Получаем топ рефереров (50 лучших)

        top_referrers = await database.get_admin_referral_stats(

            search_query=None,

            sort_by="total_revenue",

            sort_order="DESC",

            limit=50,

            offset=0

        )

        

        if not top_referrers:

            text = "🏆 Топ рефереров\n\nРефереры не найдены."

            keyboard = InlineKeyboardMarkup(inline_keyboard=[

                [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]

            ])

            await safe_edit_text(callback.message, text, reply_markup=keyboard)

            return

        

        # Формируем текст

        text = "🏆 Топ рефереров\n\n"

        

        for idx, stat in enumerate(top_referrers, 1):

            # Safe extraction: use .get() to avoid KeyError

            username = stat.get("username") or f"ID{stat.get('referrer_id', 'N/A')}"

            invited_count = stat.get("invited_count", 0)

            paid_count = stat.get("paid_count", 0)

            conversion = stat.get("conversion_percent", 0.0)

            revenue = stat.get("total_invited_revenue", 0.0)

            cashback = stat.get("total_cashback_paid", 0.0)

            cashback_percent = stat.get("current_cashback_percent", 0.0)

            referrer_id = stat.get("referrer_id", "N/A")

            

            text += f"{idx}. @{username} (ID: {referrer_id})\n"

            text += f"   Приглашено: {invited_count} | Оплатили: {paid_count} ({conversion}%)\n"

            text += f"   Доход: {revenue:.2f} ₽ | Кешбэк: {cashback:.2f} ₽ ({cashback_percent}%)\n\n"

        

        # Клавиатура

        keyboard = InlineKeyboardMarkup(inline_keyboard=[

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_revenue"), callback_data="admin:referral_sort:total_revenue"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_invited"), callback_data="admin:referral_sort:invited_count")

            ],

            [

                InlineKeyboardButton(text=i18n_get_text(language, "admin.sort_by_cashback"), callback_data="admin:referral_sort:cashback_paid"),

                InlineKeyboardButton(text=i18n_get_text(language, "admin.search"), callback_data="admin:referral_search")

            ],

            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:referral_stats")]

        ])

        

        await safe_edit_text(callback.message, text, reply_markup=keyboard)

        

        # Логируем просмотр топа

        await database._log_audit_event_atomic_standalone(

            "admin_view_referral_top",

            callback.from_user.id,

            None,

            f"Admin viewed top referrers: {len(top_referrers)} referrers"

        )

        

    except Exception as e:

        logging.exception(f"Error in callback_admin_referral_top: {e}")

        user = await database.get_user(callback.from_user.id)

        language = await resolve_user_language(callback.from_user.id)

        await callback.answer(i18n_get_text(language, "errors.top_referrers"), show_alert=True)

@admin_stats_router.callback_query(F.data == "admin:analytics")

async def callback_admin_analytics(callback: CallbackQuery):

    """📊 Финансовая аналитика - базовые метрики"""

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

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

    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:

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
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
    if callback.from_user.id not in config.ADMIN_TELEGRAM_IDS:
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
