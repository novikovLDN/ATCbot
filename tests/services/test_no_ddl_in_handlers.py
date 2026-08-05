"""Схему не правят из обработчика.

ЧТО БЫЛО

    Внутри /start, на ветке привязки сайта QoDev, выполнялся
    `ALTER TABLE users ADD COLUMN IF NOT EXISTS site_linked ...`. Ровно то
    же самое делал админский экран связок.

ПОЧЕМУ ЭТО ОПАСНО

    ALTER TABLE берёт ACCESS EXCLUSIVE — самую тяжёлую блокировку. Он
    ждёт завершения ВСЕХ текущих запросов к users и всё это время не
    пускает новые: любое чтение профиля, любая покупка, любой /start
    выстраиваются в очередь за ним. То есть один человек, привязывающий
    сайт, мог остановить бота целиком — и длительность этой остановки
    определял не он, а самый долгий чужой запрос в этот момент.

    `IF NOT EXISTS` не спасает: проверка происходит уже ПОД взятой
    блокировкой, и «ничего не делать» стоит ровно столько же, сколько
    настоящее изменение.

ЧТО ПРОВЕРЯЕМ

    В обработчиках, воркерах и API нет DDL. Место для схемы — слой базы
    (database/), где она выполняется один раз на старте.
"""
import ast
import re
from pathlib import Path

import pytest


# Где DDL недопустим. database/ намеренно не в списке: там ему и место.
FORBIDDEN_ROOTS = [
    Path("app/handlers"),
    Path("app/workers"),
    Path("app/api"),
    Path("app/services"),
]

_DDL_RE = re.compile(
    r"\b(ALTER\s+TABLE|CREATE\s+TABLE|DROP\s+TABLE|CREATE\s+INDEX|DROP\s+INDEX)\b",
    re.IGNORECASE,
)


def _python_files():
    for root in FORBIDDEN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _string_literals(tree):
    """Только строковые литералы: докстринги и комментарии не в счёт.

    Комментарий «ALTER TABLE отсюда убран» — объяснение, а не запрос.
    Ходить по литералам, а не по тексту файла, — единственный способ их
    различить, не сочиняя правил про решётки.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def _docstring_nodes(tree):
    """Докстринги модуля, классов и функций — их исключаем из проверки."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


@pytest.mark.parametrize("path", list(_python_files()), ids=str)
def test_no_ddl_outside_the_database_layer(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)

    offenders = [
        (node.lineno, _DDL_RE.search(node.value).group(0))
        for node in _string_literals(tree)
        if id(node) not in docstrings and _DDL_RE.search(node.value)
    ]

    assert not offenders, (
        f"{path} правит схему на горячем пути: {offenders}. ALTER/CREATE "
        f"берёт ACCESS EXCLUSIVE и останавливает всю работу с таблицей — "
        f"перенесите в слой базы (database/legacy_schema.py)."
    )


def test_the_column_that_started_this_lives_in_migrations():
    """Колонку завели в миграциях — иначе воркер синхронизации упадёт.

    Именно в миграциях, а не в database/legacy_schema.py: тот блок
    выполняется только под LEGACY_SCHEMA_BOOTSTRAP=1 (аварийный бутстрап
    для локальной разработки) и на боевой базе не сработает вовсе.
    """
    sql = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(Path("migrations").glob("*.sql"))
    )
    assert "site_linked" in sql, (
        "site_linked убрали из /start, но не завели в migrations/: "
        "app/workers/site_sync_worker.py читает эту колонку без проверки"
    )
