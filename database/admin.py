"""Админская часть базы — точка входа. Реализация разложена по соседям.

ЧТО ЗДЕСЬ
    Почти ничего, кроме реэкспорта. Файл оставлен потому, что через
    `database.admin` эти функции годами импортировали database/__init__.py,
    database/subscriptions.py, дашборд и тесты; переписывать все обращения —
    отдельная работа с отдельными рисками.

ГДЕ ЧТО ЛЕЖИТ
    database/admin_access.py    выдача, отзыв и удаление доступа (пишет)
    database/admin_users.py     выгрузки и карточка пользователя (читает)
    database/admin_audience.py  подбор аудитории для рассылок (читает)
    database/admin_reports.py   ряды и сводки для дашборда (читает)
    database/admin_recovery.py  разбор последствий багов с датами
    database/gift_subscriptions.py  подарочные подписки
    database/balance_purchases.py   покупка и пополнение с баланса
    database/broadcasts.py      рассылки, сегменты, A/B-тесты
    database/analytics.py       выручка, LTV, ARPU, разбивки
    database/discounts.py       персональные скидки и VIP

    Разложено так, потому что в одном файле на 2318 строк лежали шесть
    вещей, которые правят по разным поводам: двухфазная выдача подписки,
    SQL для графиков, подарки и одноразовые инструменты починки данных.

ЧТО ЛЕГКО СЛОМАТЬ
    Списки ниже дублируются в database/__init__.py. Убрать отсюда имя,
    которое там перечислено, — импорт пакета упадёт при старте бота.
    А убрать имя, которое зовут через `database.admin.X`, — упадёт не при
    импорте, а в момент вызова, то есть на живом пользователе.
"""
import logging

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)

# Доступ: выдача, отзыв, полное удаление пользователя.
from database.admin_access import (  # noqa: F401,E402
    admin_grant_access_atomic,
    admin_grant_access_minutes_atomic,
    admin_revoke_access_atomic,
    admin_delete_user_complete,
)

# Пользователь глазами админа: выгрузки, история, финансовый профиль.
from database.admin_users import (  # noqa: F401,E402
    get_all_users_for_export,
    get_active_subscriptions_for_export,
    get_subscription_history,
    get_user_extended_stats,
    get_all_users_telegram_ids,
    # Плотная таблица экрана «Пользователи»: страница списка и балансы
    # пачкой. Добавлено вместе с редизайном экрана — см. admin_users.py.
    list_users_dashboard,
    get_balances_bulk,
)

# Кому можно слать: подбор аудитории для рассылок.
from database.admin_audience import (  # noqa: F401,E402
    get_eligible_no_subscription_broadcast_users,
    check_user_still_eligible_for_no_sub_broadcast,
    get_active_trial_telegram_ids,
)

# Отчёты дашборда: ряды по дням и часам, сводки, LTV, рефералка.
from database.admin_reports import (  # noqa: F401,E402
    get_daily_timeseries,
    get_hourly_timeseries,
    get_ltv,
    get_referral_analytics,
    get_daily_summary,
    get_monthly_summary,
)

# Одноразовые инструменты починки данных.
from database.admin_recovery import (  # noqa: F401,E402
    get_bypass_overwrite_victims,
    fix_bypass_overwrite_victim,
    get_premium_recovery_candidates,
    get_user_paid_subscription_history,
    get_paid_subscription_history_bulk,
    get_activated_gifts_bulk,
    get_max_subscription_end_bulk,
    get_paid_payments_via_purchases_bulk,
    get_active_premium_subscribers,
    get_subscriptions_with_far_future_expires,
    update_subscription_expires_at_bulk,
)

# Подарочные подписки. Лежали здесь по недоразумению — подарок покупает и
# активирует обычный пользователь, а не админ.
from database.gift_subscriptions import (  # noqa: F401,E402
    generate_gift_code,
    create_gift_subscription,
    get_gift_subscription,
    activate_gift_subscription,
    get_user_gifts,
)

# Скидки и VIP вынесены в database/discounts.py.
from database.discounts import (  # noqa: F401,E402
    get_user_discount,
    create_user_discount,
    has_claimed_referral_share_discount,
    record_referral_share_discount_claim,
    delete_user_discount,
    is_vip_user,
    grant_vip_status,
    revoke_vip_status,
)

# Аналитика вынесена в database/analytics.py — там только чтение.
from database.analytics import (  # noqa: F401,E402
    get_business_metrics,
    get_last_audit_logs,
    get_analytics_by_period,
    get_active_paid_subscriptions_count,
    get_revenue_for_period,
    get_revenue_today_vs_yesterday,
    get_payments_by_provider,
    get_payments_breakdown,
    get_recent_payments_feed,
    get_user_purchases,
    log_payment_error,
    get_recent_payment_errors,
    get_payment_errors_summary,
    get_traffic_stats,
    get_purchase_breakdown,
    get_extended_bot_stats,
    get_total_revenue,
    get_paying_users_count,
    get_user_ltv,
    get_average_ltv,
    get_arpu,
)

# Рассылки вынесены в database/broadcasts.py. Импортируются сюда, потому что
# на них годами ссылался код через database.admin.
from database.broadcasts import (  # noqa: F401,E402
    create_broadcast,
    get_broadcast,
    save_broadcast_discount,
    save_broadcast_gift_reveal_percent,
    get_broadcast_discount,
    insert_admin_broadcast_record,
    update_admin_broadcast_record,
    get_users_by_segment,
    log_broadcast_send,
    get_broadcast_stats,
    get_broadcast_analytics,
    get_recent_broadcasts,
    get_broadcast_message_ids,
    mark_broadcast_messages_deleted,
    get_ab_test_broadcasts,
    get_incident_settings,
    set_incident_mode,
    get_ab_test_stats,
)


# Единственная функция, оставшаяся жить здесь. Её настоящее место —
# database/pending_purchases.py, где описан весь жизненный цикл
# pending → paid → expired. Не перенесена, чтобы не трогать чужой файл в
# рамках этой разбивки; вызывающих у неё сейчас нет вовсе (см. отчёт),
# так что переезд ничем не рискует и ждёт отдельного шага.
async def expire_old_pending_purchases() -> int:
    """
    Автоматически помечает истёкшие pending покупки как expired

    Returns:
        Количество истёкших покупок
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, expire_old_pending_purchases skipped")
        return 0
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, expire_old_pending_purchases skipped")
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE status = 'pending' AND expires_at <= NOW()"
        )

        # Извлекаем количество обновлённых строк из результата
        # Формат результата: "UPDATE N"
        if result and result.startswith("UPDATE "):
            count = int(result.split()[1])
            if count > 0:
                logger.info(f"Expired {count} old pending purchases")
            return count
        return 0
