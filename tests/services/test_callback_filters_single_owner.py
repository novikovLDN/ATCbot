"""Один callback_data — один обработчик, и только живые значения в фильтрах.

ПОЧЕМУ ЭТО ВАЖНО

    aiogram отдаёт апдейт ПЕРВОМУ подошедшему обработчику и на этом
    останавливается. Второй обработчик того же callback_data не падает и
    ничего не пишет в лог — он просто никогда не выполняется.

    Так и вышло с noop: он был объявлен дважды — в
    app/handlers/callbacks/navigation.py и в app/handlers/admin/base.py.
    В app/handlers/__init__.py callbacks_router подключается раньше
    admin_router, значит побеждала navigation-версия, а админская (с
    тостом «Операция уже выполняется...») была мертва. Пока копия лежала
    в дереве, любое добавленное в неё поведение молча не сработало бы.

    Обратная беда — значения в фильтре, которых не выставляет ни одна
    кнопка: copy_key_menu / copy_key / copy_key_plus остались от удалённой
    выдачи ключей в боте. Они не ломают рантайм, но при поиске мёртвых
    кнопок дают ложный след «обработчик есть, значит кнопка живая».
"""
import ast
import re
from pathlib import Path

import pytest


APP = Path("app")


def _python_files():
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" not in str(path):
            yield path


def _exact_data_values(decorator: ast.Call):
    """Строки из фильтров F.data == "x" внутри одного декоратора."""
    values = set()
    for node in ast.walk(decorator):
        if not isinstance(node, ast.Compare):
            continue
        if not (len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)):
            continue
        left, right = node.left, node.comparators[0]
        if not (isinstance(left, ast.Attribute) and left.attr == "data"):
            continue
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            values.add(right.value)
    return values


def _registered_exact_callbacks():
    """{callback_data: [файл:строка функции, ...]} для фильтров F.data == "x".

    Считаем по ФУНКЦИЯМ, а не по декораторам: на одном обработчике часто
    висят два декоратора с тем же callback_data и разным StateFilter (см.
    callback_profile в callbacks/subscription.py) — это одна точка входа,
    а не дубль.

    Читаем через ast, а не импортом роутеров: импорт тянет БД и Telegram,
    а нам нужен только факт объявления. Текстовый grep тут не годится —
    он ловит ещё и комментарии о том, что обработчик УДАЛЁН.
    """
    found = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            values = set()
            for dec in fn.decorator_list:
                if isinstance(dec, ast.Call):
                    values |= _exact_data_values(dec)
            for value in values:
                found.setdefault(value, []).append(f"{path}:{fn.lineno} ({fn.name})")
    return found


def test_noop_has_exactly_one_handler():
    places = _registered_exact_callbacks().get("noop", [])
    assert len(places) == 1, (
        f"noop объявлен {len(places)} раз: {places}. Побеждает только тот, "
        f"чей роутер подключён раньше, остальные мертвы"
    )


def test_no_exact_callback_is_registered_twice():
    duplicates = {
        data: places
        for data, places in _registered_exact_callbacks().items()
        if len(places) > 1
    }
    assert not duplicates, (
        f"один callback_data обслуживают несколько обработчиков, "
        f"выполнится только первый: {duplicates}"
    )


def _all_callback_data_produced():
    """Все строковые callback_data, которые код где-либо ставит на кнопку."""
    produced = set()
    pattern = re.compile(r'callback_data\s*=\s*f?["\']([^"\'{]+)["\']')
    for path in _python_files():
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            produced.add(m.group(1))
    return produced


def test_copy_vpn_key_is_produced_and_handled_once():
    """Единственное живое значение фильтра — copy_vpn_key.

    Его выставляет get_reissue_notification_keyboard (уведомление о
    перевыпуске ключа), и обслуживает ровно один обработчик.
    """
    assert "copy_vpn_key" in _all_callback_data_produced(), (
        "кнопку copy_vpn_key перестали выставлять — обработчик стал мёртвым"
    )
    assert len(_registered_exact_callbacks().get("copy_vpn_key", [])) == 1


@pytest.mark.parametrize("dead", ["copy_key_menu", "copy_key_plus"])
def test_dead_copy_key_values_stay_out(dead):
    hits = [
        str(p) for p in _python_files()
        if re.search(rf'["\']{dead}["\']', p.read_text(encoding="utf-8"))
    ]
    assert not hits, f"{dead} вернулся, хотя такой кнопки не существует: {hits}"
