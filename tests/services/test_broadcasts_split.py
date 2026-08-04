"""Разбивка database/broadcasts.py ничего не потеряла.

Файл был на 1096 строк и держал четыре вещи, которые правят по разным
поводам: паспорт рассылки (текст, медиа, скидки), подбор получателей,
отчёты по доставке и — совсем не по теме — режим инцидента. Один только
подбор сегментов занимал 508 строк, больше чем всё остальное вместе.
Разрезан на broadcast_records / broadcast_segments / broadcast_analytics /
incident_mode, а сам broadcasts.py оставлен фасадом: через него рассылки
импортируют database/__init__.py и database/admin.py.

Риск здесь не «кнопка замолчит», а «имя пропало из реэкспорта»: список
функций продублирован в трёх местах, и потеря одного имени роняет импорт
пакета при старте бота. Плюс проверяем, что фасад отдаёт ту же функцию, а
не копию, — иначе моки в тестах промахнутся мимо реального вызова.
"""
import ast
from pathlib import Path

import pytest

DB = Path("database")

# Взято из файла до разрезания. Публичный набор не менялся.
BROADCAST_API = {
    "create_broadcast",
    "get_broadcast",
    "save_broadcast_discount",
    "save_broadcast_gift_reveal_percent",
    "get_broadcast_discount",
    "insert_admin_broadcast_record",
    "update_admin_broadcast_record",
    "get_users_by_segment",
    "log_broadcast_send",
    "get_broadcast_stats",
    "get_broadcast_analytics",
    "get_recent_broadcasts",
    "get_broadcast_message_ids",
    "mark_broadcast_messages_deleted",
    "get_ab_test_broadcasts",
    "get_ab_test_stats",
    "get_incident_settings",
    "set_incident_mode",
}

SPLIT_MODULES = [
    "broadcast_records.py",
    "broadcast_segments.py",
    "broadcast_analytics.py",
    "incident_mode.py",
]


def _toplevel_functions(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_no_broadcast_function_was_lost():
    """Каждая функция обязана найтись ровно в одном новом модуле."""
    seen = {}
    for name in SPLIT_MODULES:
        for func in _toplevel_functions(DB / name):
            assert func not in seen, f"{func} объявлена дважды: {seen[func]} и {name}"
            seen[func] = name
    missing = BROADCAST_API - set(seen)
    assert not missing, f"функции рассылок пропали: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(BROADCAST_API))
def test_api_visible_from_all_three_entrypoints(name):
    """Список имён продублирован в broadcasts.py, database/__init__.py и
    database/admin.py. Потеря имени в любом из трёх роняет старт бота."""
    import database
    import database.admin as admin
    import database.broadcasts as broadcasts

    for module in (database, broadcasts, admin):
        assert hasattr(module, name), f"{name} не виден из {module.__name__}"


@pytest.mark.parametrize("name", sorted(BROADCAST_API))
def test_reexport_gives_the_same_object(name):
    """Фасад обязан отдавать ту же функцию, а не копию: иначе тесты,
    которые патчат database.broadcasts.<имя>, будут зелёными на сломанном
    коде."""
    import database
    import database.broadcasts as broadcasts

    assert getattr(database, name) is getattr(broadcasts, name)


def test_split_modules_import_cleanly():
    """Потерянный при переносе импорт иначе всплывёт только на проде."""
    import importlib

    for name in SPLIT_MODULES:
        importlib.import_module(f"database.{name[:-3]}")


def test_facade_holds_no_logic():
    """broadcasts.py оставлен ТОЛЬКО ради импортов. Функция, дописанная
    сюда, снова начнёт растить файл, который мы и резали."""
    tree = ast.parse((DB / "broadcasts.py").read_text(encoding="utf-8"))
    funcs = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not funcs, f"в фасаде появилась логика: {funcs}"


def test_segments_module_holds_the_whole_chain():
    """Подбор получателей намеренно оставлен ОДНОЙ функцией: сегменты
    сравнивают друг с другом глазами, и разложенные по файлам условия
    сравнить невозможно."""
    funcs = _toplevel_functions(DB / "broadcast_segments.py")
    assert funcs == {"get_users_by_segment"}


def test_segment_windows_are_half_open():
    """Сегменты «N дней назад» — полуинтервалы, а не «всё, что старше».
    Замена на простое сравнение превращает точечную рассылку в рассылку
    по всей истории."""
    src = (DB / "broadcast_segments.py").read_text(encoding="utf-8")
    assert "BETWEEN" in src or ">= $" in src
    # хотя бы один сегмент обязан ограничивать окно с обеих сторон
    assert src.count("AND s.expires_at <") + src.count("AND s.expires_at >") >= 2


def test_deleted_messages_still_count_as_delivered():
    """Статус 'deleted' — «доставлено, но потом удалили». Продажа до
    удаления никуда не делась; выкинуть их из знаменателя значит завысить
    конверсию."""
    src = (DB / "broadcast_analytics.py").read_text(encoding="utf-8")
    assert "delivered = sent + deleted" in src
    assert "IN ('sent', 'deleted')" in src


def test_payment_statuses_stay_both():
    """У разных провайдеров прижилось по-разному: 'paid' и 'approved'.
    Оставить один статус значит потерять часть выручки в отчёте."""
    src = (DB / "broadcast_analytics.py").read_text(encoding="utf-8")
    assert "'paid', 'approved'" in src


def test_incident_mode_is_fail_safe():
    """Авария в БД не должна ни вешать баннер об аварии, ни ронять экраны,
    которые его спрашивают."""
    src = (DB / "incident_mode.py").read_text(encoding="utf-8")
    assert src.count('{"is_active": False, "incident_text": None}') >= 4
    assert "ORDER BY id LIMIT 1" in src


def test_incident_mode_does_not_depend_on_broadcasts():
    """Режим инцидента уехал из рассылок не формально: он не должен знать
    про них ничего, иначе переезд был косметическим."""
    src = (DB / "incident_mode.py").read_text(encoding="utf-8")
    for sibling in ("broadcast_records", "broadcast_segments", "broadcast_analytics"):
        assert sibling not in src
