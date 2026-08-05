"""Разбивка database/reconciliation.py ничего не потеряла.

Файл был на 1106 строк и держал пять вещей, которые правят по разным
поводам: HTTP-кэш скана панели, арифметику ожидаемого срока, два экрана
дашборда, единственную записывающую операцию («Исправить») и SQL журналов.
Разрезан на reconciliation_panel / _expiry / _candidates / _detail / _fix /
_audit, а сам reconciliation.py оставлен фасадом: через него сверку
импортируют database/__init__.py, роуты дашборда и тесты.

РИСК ТАКОЙ ОПЕРАЦИИ
    Не «кнопка замолчит», а «имя пропало из реэкспорта»: список функций
    продублирован в database/__init__.py, и потеря одного имени роняет
    импорт пакета при старте бота.

    Второй риск тоньше: фасад должен отдавать ТУ ЖЕ функцию, а не копию, —
    иначе моки в тестах промахнутся мимо реального вызова.

    Третий — расчёт ожидаемого срока. Его зовут двое: экран (показать) и
    «Исправить» (применить). Если они разъедутся на разные реализации,
    админ увидит одно число, а кнопка отнимет другое, и в логах об этом не
    будет ни строки.
"""
import ast
from pathlib import Path

import pytest

DB = Path("database")

# Взято из файла ДО разрезания (__all__ + то, что зовут снаружи по имени).
PUBLIC_API = {
    "find_over_issuance_candidates",
    "invalidate_panel_scan_cache",
    "get_reconciliation_detail",
    "apply_reconciliation_fix",
    "list_reconciliation_log",
    "list_over_issuance_log",
    "record_over_issuance",
}

# Внутренние имена, которые до разрезания были доступны как
# `database.reconciliation.X` и на которые смотрят соседние тесты.
INTERNALS_KEPT = {
    "clamp_recomputed_expiry",
    "_fetch_panel_expires_at",
    "_extract_period_days_from_tariff",
    "_simulate_expiry_from_payments",
    "_parse_remnawave_dt",
}

SPLIT_MODULES = [
    "reconciliation_panel.py",
    "reconciliation_expiry.py",
    "reconciliation_candidates.py",
    "reconciliation_detail.py",
    "reconciliation_fix.py",
    "reconciliation_audit.py",
]


def _toplevel_functions(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("module", SPLIT_MODULES)
def test_split_module_exists_and_has_a_docstring(module):
    path = DB / module
    assert path.exists(), f"{module} потерян"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), f"{module} без докстринга: непонятно, что здесь"


def test_facade_reexports_everything():
    import database.reconciliation as rec

    missing = sorted(n for n in PUBLIC_API | INTERNALS_KEPT if not hasattr(rec, n))
    assert not missing, f"потерян реэкспорт: {missing}"


def test_facade_all_did_not_change():
    import database.reconciliation as rec

    assert set(rec.__all__) == PUBLIC_API


def test_database_package_still_exports_the_same_names():
    """database/__init__.py тянет эти имена напрямую — падает при старте бота."""
    import database

    missing = sorted(n for n in PUBLIC_API - {"invalidate_panel_scan_cache"}
                     if not hasattr(database, n))
    assert not missing, f"пакет database потерял: {missing}"


def test_facade_hands_out_the_same_objects_not_copies():
    """Мок, поставленный на модуль-реализацию, обязан быть виден через фасад."""
    import database.reconciliation as rec
    import database.reconciliation_audit as audit
    import database.reconciliation_candidates as candidates
    import database.reconciliation_detail as detail
    import database.reconciliation_fix as fix

    assert rec.find_over_issuance_candidates is candidates.find_over_issuance_candidates
    assert rec.get_reconciliation_detail is detail.get_reconciliation_detail
    assert rec.apply_reconciliation_fix is fix.apply_reconciliation_fix
    assert rec.record_over_issuance is audit.record_over_issuance


def test_facade_holds_no_logic():
    """Фасад — только импорты и __all__: логика уехала в модули."""
    functions = _toplevel_functions(DB / "reconciliation.py")
    assert not functions, f"в фасаде осталась логика: {sorted(functions)}"


def test_screen_and_fix_share_one_expiry_calculation():
    """Показанное и сделанное считает одна и та же функция."""
    import database.reconciliation_detail as detail
    import database.reconciliation_expiry as expiry
    import database.reconciliation_fix as fix

    assert detail._simulate_expiry_from_payments is expiry._simulate_expiry_from_payments
    assert fix._simulate_expiry_from_payments is expiry._simulate_expiry_from_payments


def test_expiry_module_touches_neither_db_nor_panel():
    """Арифметика сроков должна оставаться проверяемой без моков."""
    text = (DB / "reconciliation_expiry.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = [m for m in imported if "database.core" in m or "remnawave" in m]
    assert not forbidden, f"расчёты потянули за собой инфраструктуру: {forbidden}"


def test_panel_cache_lives_in_one_place():
    """Кэш скана — глобал reconciliation_panel.

    Псевдоним в фасаде был бы снимком на момент импорта: присвоение туда не
    влияло бы на реальный кэш, а чтение вечно возвращало бы None — то есть
    «сбросил кэш» выглядело бы сделанным, ничего не изменив.
    """
    import database.reconciliation as rec
    import database.reconciliation_panel as panel

    assert hasattr(panel, "_panel_scan_cache")
    assert not hasattr(rec, "_panel_scan_cache"), (
        "фасад завёл собственную копию кэша — сброс перестанет работать"
    )


def test_fix_invalidates_the_candidate_cache():
    """После удавшегося патча панели список кандидатов обязан протухнуть.

    Без этого админ жмёт «Исправить», обновляет экран и до 10 минут видит
    того же человека — выглядит как «кнопка не сработала».
    """
    import database.reconciliation_fix as fix
    import database.reconciliation_panel as panel

    assert fix.invalidate_panel_scan_cache is panel.invalidate_panel_scan_cache
    assert "invalidate_panel_scan_cache" in (
        (DB / "reconciliation_fix.py").read_text(encoding="utf-8")
    )
