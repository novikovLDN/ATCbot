"""Аналитика — точка входа. Реализация разложена по соседям.

ЧТО ЗДЕСЬ
    Ничего, кроме реэкспорта. Файл оставлен потому, что через
    `database.analytics` эти функции импортируют database/__init__.py,
    database/admin.py и полдюжины тестов; переписывать все обращения —
    отдельная работа с отдельными рисками.

ГДЕ ЧТО ЛЕЖИТ
    database/analytics_revenue.py   деньги: выручка, разрезы оплат, LTV, ARPU
    database/analytics_payments.py  ленты покупок и журнал ошибок оплаты
    database/analytics_stats.py     счётчики: пользователи, подписки, audit_log

    Разложено так, потому что в одном файле на 1265 строк лежали три вещи
    с разной ценой ошибки. В деньгах ошибка меняет цифру, по которой
    принимают решения, и обязана подчиняться одному правилу — считать
    только внешние поступления. В лентах ошибка светит на экран лишнюю
    колонку (у Spotify в promo_code лежит пароль клиента). В счётчиках
    ошибка сдвигает сутки. Правят их по разным поводам.

ГДЕ ИСКАТЬ ОПРЕДЕЛЕНИЕ ВЫРУЧКИ
    В database/analytics_revenue.py, рядом с запросами: константа
    REVENUE_EXTERNAL_ONLY_SQL и комментарий над ней. Коротко — выручка это
    ВНЕШНИЕ ПОСТУПЛЕНИЯ, строки с payment_provider='balance' в неё не
    входят, иначе одни и те же рубли считаются два-три раза.

ЧТО ЛЕГКО СЛОМАТЬ
    Список ниже дублируется в database/__init__.py и database/admin.py.
    Убрать отсюда имя, которое там перечислено, — импорт пакета упадёт при
    старте бота.

    Тесты, подменяющие get_pool, обязаны подменять его в модуле, где
    функция ОБЪЯВЛЕНА, а не здесь: реэкспорт даёт ту же функцию с
    globals() модуля-реализации, и подмена на фасаде до неё не дойдёт.
"""
from database.analytics_revenue import (  # noqa: F401
    REVENUE_EXTERNAL_ONLY_SQL,
    get_revenue_for_period,
    get_payments_by_provider,
    get_payments_breakdown,
    get_purchase_breakdown,
    get_traffic_stats,
    get_total_revenue,
    get_paying_users_count,
    get_user_ltv,
    get_average_ltv,
    get_arpu,
)
from database.analytics_payments import (  # noqa: F401
    get_recent_payments_feed,
    get_user_purchases,
    log_payment_error,
    get_recent_payment_errors,
    get_payment_errors_summary,
)
from database.analytics_stats import (  # noqa: F401
    get_business_metrics,
    get_last_audit_logs,
    get_analytics_by_period,
    get_active_paid_subscriptions_count,
    get_extended_bot_stats,
)

__all__ = [
    "REVENUE_EXTERNAL_ONLY_SQL",
    "get_revenue_for_period",
    "get_payments_by_provider",
    "get_payments_breakdown",
    "get_purchase_breakdown",
    "get_traffic_stats",
    "get_total_revenue",
    "get_paying_users_count",
    "get_user_ltv",
    "get_average_ltv",
    "get_arpu",
    "get_recent_payments_feed",
    "get_user_purchases",
    "log_payment_error",
    "get_recent_payment_errors",
    "get_payment_errors_summary",
    "get_business_metrics",
    "get_last_audit_logs",
    "get_analytics_by_period",
    "get_active_paid_subscriptions_count",
    "get_extended_bot_stats",
]
