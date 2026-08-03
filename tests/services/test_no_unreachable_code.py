"""Код после return в той же ветке не выполнится никогда.

ПОЧЕМУ ЭТОТ ТЕСТ СУЩЕСТВУЕТ

    При выносе выдачи товаров в отдельный модуль тела всех семи функций
    (подарок, Premium, звёзды, Steam, Spotify, Apple ID, пакет ГБ)
    остались на лишнем уровне отступа — внутри guard-clause, после
    `return False`:

        if classify_purchase(pending) != "gift":
            return False

            # эти 40 строк не выполнятся никогда
            gift_result = await database.finalize_purchase(...)

    Функции всегда возвращали False, ни один товар не выдавался
    автоматически. Питон на такое не ругается, тесты тоже молчали:
    выдача требует базы и Telegram, и юнит-тестом её не звали.

    Заметить это удалось только обходом дерева разбора. Ловушка
    неочевидная и повторяемая — поэтому проверка стоит для всего
    прикладного кода, а не для одного файла.

ЧТО СЧИТАЕТСЯ НАРУШЕНИЕМ

    Оператор после return / raise / continue / break в ТОМ ЖЕ блоке.
    Код в соседних ветках (else, следующий if) недостижимым не является
    и тестом не задевается.
"""
import ast
from pathlib import Path

import pytest


TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)

ROOTS = [Path("app"), Path("database")]


def _python_files():
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            yield path


def _unreachable_in(path: Path):
    """Вернуть [(функция, строка терминатора, строк после него)]."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    found = []

    def enclosing_name(node):
        return getattr(node, "_owner", "<модуль>")

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        name = getattr(fn, "name", "<модуль>")
        for node in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list) or len(block) < 2:
                    continue
                for i, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, TERMINATORS):
                        tail = block[i + 1:]
                        found.append((
                            name, stmt.lineno,
                            tail[-1].end_lineno - tail[0].lineno + 1,
                        ))
    return found


@pytest.mark.parametrize("path", list(_python_files()), ids=lambda p: str(p))
def test_no_statements_after_a_terminator(path):
    problems = _unreachable_in(path)
    assert not problems, "\n".join(
        f"{path}:{line} — в {func}() после выхода из блока осталось "
        f"{count} строк, они не выполнятся"
        for func, line, count in problems
    )


def test_the_check_actually_catches_the_original_defect():
    """Страховка от теста, который проходит по недосмотру."""
    broken = ast.parse(
        "async def deliver(x):\n"
        "    if x != 'gift':\n"
        "        return False\n"
        "\n"
        "        result = await finalize(x)\n"
        "        return True\n"
    )
    hits = []
    for node in ast.walk(broken):
        block = getattr(node, "body", None)
        if isinstance(block, list) and len(block) >= 2:
            for i, stmt in enumerate(block[:-1]):
                if isinstance(stmt, TERMINATORS):
                    hits.append(stmt.lineno)
    assert hits == [3], f"проверка не видит исходный дефект: {hits}"
