"""Флага PURCHASE_FLOW_REMNAWAVE больше нет — и getattr к нему тоже.

ЧТО БЫЛО

    Флаг читали через `getattr(config, "PURCHASE_FLOW_REMNAWAVE", <дефолт>)`
    в пяти местах, и дефолты не совпадали: где-то True, где-то False.
    Пока config объявляет атрибут, расхождение не видно. Но стоит подменить
    config моком (а в тестах его подменяют), и одна ветка считает cut-over
    включённым, другая — выключенным: откат пойдёт дёргать выведенный из
    эксплуатации xray, хотя провижининг шёл через Remnawave.

    Флаг снят вместе с веткой xray — Remnawave единственный бэкенд.

ЗАЧЕМ ТЕСТ

    Ловит возврат любого `getattr(config, "PURCHASE_FLOW_REMNAWAVE", ...)`:
    именно такая строка снова принесёт молчаливый дефолт вместо явного
    поведения. Проверяем текстом по дереву, а не импортом, — важен сам факт
    отсутствия обращений, а не значение атрибута.
"""
from pathlib import Path

FLAG = "PURCHASE_FLOW_REMNAWAVE"
SKIP_DIRS = (".venv", "graphify-out", "__pycache__", "node_modules", "tests/")


def _sources():
    for path in Path(".").rglob("*.py"):
        s = str(path)
        if any(x in s for x in SKIP_DIRS):
            continue
        yield path


def test_flag_is_not_declared_in_config():
    import config

    assert not hasattr(config, FLAG), (
        "флаг вернулся в config — вместе с ним вернётся и ветка xray"
    )


def test_no_getattr_on_removed_flag():
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if FLAG in text:
            offenders.append(str(path))
    assert not offenders, (
        f"обращения к снятому флагу {FLAG}: {offenders} — "
        f"getattr с дефолтом снова разъедется между модулями"
    )
