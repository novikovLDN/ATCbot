"""Сверка подписок с панелью — точка входа. Реализация разложена по соседям.

ЧТО ЗДЕСЬ
    Почти ничего, кроме реэкспорта. Файл оставлен потому, что через
    `database.reconciliation` эти функции импортируют database/__init__.py,
    роуты дашборда и тесты; переписывать все обращения — отдельная работа
    с отдельными рисками.

ГДЕ ЧТО ЛЕЖИТ
    database/reconciliation_panel.py       всё общение с панелью Remnawave:
                                           разбор дат, чтение expireAt,
                                           кэшированный полный скан
    database/reconciliation_expiry.py      арифметика сроков — ни базы, ни
                                           панели, только вычисления
    database/reconciliation_candidates.py  верхний экран: кто раздут
    database/reconciliation_detail.py      карточка одного пользователя
    database/reconciliation_fix.py         «Исправить» — единственное место,
                                           где сверка что-то меняет
    database/reconciliation_audit.py       журналы: чтение обоих и запись
                                           превышений из вотчдога

    Разложено так, потому что в одном файле на 1106 строк лежали пять
    вещей, которые правят по разным поводам: HTTP-кэш панели, формула
    ожидаемого срока, два экрана дашборда, записывающая операция и SQL
    журналов.

ЧТО ЛЕГКО СЛОМАТЬ
    Список ниже дублируется в database/__init__.py. Убрать отсюда имя,
    которое там перечислено, — импорт пакета упадёт при старте бота.
    А убрать имя, которое зовут через `database.reconciliation.X`, —
    упадёт не при импорте, а в момент вызова.

    Здесь НЕТ реэкспорта `_panel_scan_cache`: это изменяемый глобал
    reconciliation_panel. Псевдоним отсюда был бы снимком на момент
    импорта — присвоение в фасад не влияло бы на реальный кэш, а чтение
    вечно возвращало бы None. Сброс кэша делается функцией
    `invalidate_panel_scan_cache()`, она работает откуда угодно.
"""
from __future__ import annotations

# Панель: скан с кэшем и чтение expireAt по одному юзеру.
# `_fetch_panel_expires_at` держим видимой отсюда намеренно — на неё
# смотрит tests/services/test_dead_helpers_removed.py как на живого соседа
# удалённой пакетной обёртки.
from database.reconciliation_panel import (  # noqa: F401
    invalidate_panel_scan_cache,
    _fetch_panel_expires_at,
    _parse_remnawave_dt,
    _scan_panel_for_over_issuance,
    _PREMIUM_USERNAME_RE,
)

# Арифметика сроков. clamp_recomputed_expiry проверяется отдельным тестом
# как чистая функция (tests/test_reconciliation_clamp.py).
from database.reconciliation_expiry import (  # noqa: F401
    clamp_recomputed_expiry,
    _extract_period_days_from_tariff,
    _from_db_utc_str,
    _simulate_expiry_from_payments,
)

# Экраны дашборда: список кандидатов и карточка пользователя.
from database.reconciliation_candidates import (  # noqa: F401
    find_over_issuance_candidates,
    _EIGHT_YEARS,
)
from database.reconciliation_detail import get_reconciliation_detail  # noqa: F401

# Единственная записывающая операция сверки.
from database.reconciliation_fix import apply_reconciliation_fix  # noqa: F401

# Журналы.
from database.reconciliation_audit import (  # noqa: F401
    list_reconciliation_log,
    list_over_issuance_log,
    record_over_issuance,
)

__all__ = [
    "find_over_issuance_candidates",
    "invalidate_panel_scan_cache",
    "get_reconciliation_detail",
    "apply_reconciliation_fix",
    "list_reconciliation_log",
    "list_over_issuance_log",
    "record_over_issuance",
]
