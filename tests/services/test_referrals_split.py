"""Разбивка рефералки на четыре модуля ничего не потеряла.

database/referrals.py был на 1325 строк и держал четыре разные вещи с
несовместимыми правилами: привязка «кто кого привёл» обязана быть
неизменяемой, ставку кешбэка правят чаще всего остального, витрины для
экрана вообще ничего не пишут, а начисление денег живёт в чужой транзакции
и обязано падать наружу. Разрезан на referral_codes / referral_rates /
referral_stats / referral_reward, а сам referrals.py оставлен фасадом —
через него рефералку импортируют database/__init__.py и database/users.py.

Главный риск такой операции здесь не «кнопка замолчит», а «имя пропало из
реэкспорта»: список функций продублирован в трёх местах, и потеря одного
имени роняет импорт пакета при старте бота. Поэтому проверяем, что весь
набор виден из всех трёх точек входа и что это ОДИН И ТОТ ЖЕ объект, а не
копия — иначе моки в тестах и патчи начнут промахиваться.
"""
import ast
from pathlib import Path

import pytest

DB = Path("database")

# Взято из файла до разрезания. Публичный набор рефералки не менялся.
REFERRAL_API = {
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
}

SPLIT_MODULES = [
    "referral_codes.py",
    "referral_rates.py",
    "referral_stats.py",
    "referral_reward.py",
]


def _toplevel_functions(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_no_referral_function_was_lost():
    """Каждая функция обязана найтись ровно в одном новом модуле."""
    seen = {}
    for name in SPLIT_MODULES:
        for func in _toplevel_functions(DB / name):
            assert func not in seen, f"{func} объявлена дважды: {seen.get(func)} и {name}"
            seen[func] = name
    missing = REFERRAL_API - set(seen)
    assert not missing, f"функции рефералки пропали: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(REFERRAL_API))
def test_api_visible_from_all_three_entrypoints(name):
    """Список имён продублирован в referrals.py, database/__init__.py и
    database/users.py. Потеря имени в любом из трёх роняет старт бота."""
    import database
    import database.referrals as refs
    import database.users as users

    for module in (database, refs, users):
        assert hasattr(module, name), f"{name} не виден из {module.__name__}"


@pytest.mark.parametrize("name", sorted(REFERRAL_API))
def test_reexport_gives_the_same_object(name):
    """Фасад обязан отдавать ту же функцию, а не копию: иначе тесты,
    которые патчат database.referrals.<имя>, промахнутся мимо реального
    вызова и будут зелёными на сломанном коде."""
    import database
    import database.referrals as refs

    assert getattr(database, name) is getattr(refs, name)


def test_split_modules_import_cleanly():
    """Каждый модуль обязан импортироваться сам по себе — потерянный при
    переносе импорт иначе всплывёт только на проде."""
    import importlib

    for name in SPLIT_MODULES:
        importlib.import_module(f"database.{name[:-3]}")


def test_facade_holds_no_logic():
    """referrals.py оставлен ТОЛЬКО ради импортов. Любая функция,
    дописанная сюда, снова начнёт растить файл, который мы и резали."""
    tree = ast.parse((DB / "referrals.py").read_text(encoding="utf-8"))
    funcs = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not funcs, f"в фасаде появилась логика: {funcs}"


def test_money_module_does_not_depend_on_siblings():
    """Начисление кешбэка — финансовая операция. Оно не должно тянуть
    витрины и расчёт ставки: иначе правка экрана сможет задеть деньги."""
    src = (DB / "referral_reward.py").read_text(encoding="utf-8")
    for sibling in ("referral_stats", "referral_rates", "referral_codes", "referrals"):
        assert f"database.{sibling}" not in src


def test_reward_still_raises_on_db_errors():
    """Контракт на ошибки: бизнес-проверки возвращают success=False, а сбои
    БД пробрасываются, чтобы транзакция вызывающего откатилась. Проглотить
    сбой значит оставить зачисленный баланс без записи в истории."""
    src = (DB / "referral_reward.py").read_text(encoding="utf-8")
    assert "raise  # Re-raise to cause transaction rollback" in src
    assert "ON CONFLICT (buyer_id, purchase_id)" in src


def test_link_is_written_only_once():
    """Связь referrer→referred неизменяема: UPDATE идёт с условием
    «ещё не заполнено». Без условия пользователь сможет переназначить
    пригласившего и увести чужой кешбэк."""
    src = (DB / "referral_codes.py").read_text(encoding="utf-8")
    assert "AND referrer_id IS NULL" in src
    assert "AND referred_by IS NULL" in src


def test_referral_code_is_still_deterministic():
    """Алгоритм кода не должен был поменяться при переносе: иначе у всех
    существующих пользователей сменятся коды и порвутся разосланные ссылки."""
    from database.referral_codes import generate_referral_code

    first = generate_referral_code(12345)
    assert first and generate_referral_code(12345) == first
    assert generate_referral_code(54321) != first
