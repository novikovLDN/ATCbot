"""Реферальная программа — точка входа. Реализация разложена по соседям.

ЧТО ЗДЕСЬ
    Ничего, кроме реэкспорта. Файл оставлен потому, что через
    `database.referrals` рефералку импортируют database/__init__.py,
    database/users.py и тесты; менять эти импорты — отдельная работа.

ГДЕ ЧТО ЛЕЖИТ
    database/referral_codes.py   — код приглашения и привязка «кто кого привёл»
                                   (единственное место, где пишется referrer_id)
    database/referral_rates.py   — сколько процентов положено: тир, floor, фикс
    database/referral_stats.py   — витрины для экрана и API, только чтение
    database/referral_reward.py  — начисление кешбэка, единственная работа с деньгами

    Разложено так, потому что раньше в одном файле на 1325 строк лежали
    четыре разные вещи с разными правилами: привязка обязана быть
    неизменяемой, ставку правят чаще всего, витрины не пишут ничего, а
    начисление живёт в чужой транзакции и обязано падать наружу.

ГЛАВНОЕ ПРАВИЛО ПРОГРАММЫ
    Итоговый процент всегда берут через get_effective_cashback_percent —
    она учитывает и прогрессивную шкалу, и индивидуальный фикс. Считать
    процент напрямую от количества рефералов значит молча игнорировать
    индивидуальные условия.

ЧТО ЛЕГКО СЛОМАТЬ
    Список ниже дублируется в database/__init__.py и database/users.py.
    Убрать отсюда имя, которое там перечислено, — импорт пакета упадёт при
    старте бота.
"""
from database.referral_codes import (  # noqa: F401
    generate_referral_code,
    create_user,
    get_user_referral_code,
    find_user_by_referral_code,
    register_referral,
    mark_referral_active,
    _mark_referral_active_internal,
)
from database.referral_rates import (  # noqa: F401
    get_referral_cashback_percent,
    get_cashback_fixed_percent,
    set_cashback_fixed_percent,
    clear_cashback_fixed_percent,
    get_effective_cashback_percent,
    calculate_referral_percent,
    calculate_referral_level,
)
from database.referral_stats import (  # noqa: F401
    get_referral_stats,
    get_referral_level_info,
    get_total_cashback_earned,
    get_referral_metrics,
    get_referral_statistics,
)
from database.referral_reward import process_referral_reward  # noqa: F401

__all__ = [
    "generate_referral_code",
    "create_user",
    "get_user_referral_code",
    "find_user_by_referral_code",
    "register_referral",
    "mark_referral_active",
    "_mark_referral_active_internal",
    "get_referral_stats",
    "get_referral_cashback_percent",
    "get_cashback_fixed_percent",
    "set_cashback_fixed_percent",
    "clear_cashback_fixed_percent",
    "get_effective_cashback_percent",
    "calculate_referral_percent",
    "get_referral_level_info",
    "get_total_cashback_earned",
    "get_referral_metrics",
    "calculate_referral_level",
    "get_referral_statistics",
    "process_referral_reward",
]
