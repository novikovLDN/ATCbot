"""Чистка общего слоя хендлеров: фасад, мёртвая ветка, ложная оптимизация.

Три правки, каждая — след недоделанного рефакторинга. Все три безвредны в
рантайме и ровно поэтому жили годами: ни одна не даёт ни ошибки, ни строки
в логах.

1. app/handlers/common/__init__.py реэкспортировал 38 имён, которыми никто
   не пользовался — все модули ходят прямо в подмодули. Вреден он был
   тем, что для анализаторов мёртвого кода выглядел живым потребителем и
   прятал восемь давно не вызываемых билдеров клавиатур и две функции
   utils.

2. В get_main_menu_keyboard стояла ветка `if not offer_shown and not
   trial_available: pass` — задуманное отдельное меню так и не написали, а
   флаг offer_shown после неё не читался.

3. safe_edit_text «экономил» вызовы Bot API, сравнивая message.text (уже
   отрендеренный Telegram'ом, БЕЗ тегов) с новым текстом, где есть <b>,
   <blockquote>, <tg-emoji>. Для экранов с разметкой равенство не
   выполнялось никогда. Чинить сравнение опаснее, чем убрать: заработав,
   оно начало бы глушить обновление экранов, где меняется только
   разметка — такой дефект в боте уже был (экран политики
   конфиденциальности, см. докстринг get_about_keyboard).
"""
import ast
import inspect
from pathlib import Path

import pytest

COMMON = Path("app/handlers/common")


def test_facade_reexports_nothing():
    import app.handlers.common as common

    exported = [
        name for name in dir(common)
        if not name.startswith("_") and name not in {"guards", "utils", "keyboards", "screens", "states"}
    ]
    assert not exported, (
        f"фасад снова реэкспортирует имена и прячет мёртвый код: {exported}"
    )


@pytest.mark.parametrize("name", ["detect_platform", "format_promo_stats_text"])
def test_functions_kept_alive_only_by_the_facade_are_gone(name):
    """detect_platform всегда возвращала 'unknown' (Bot API не сообщает
    платформу), format_promo_stats_text — вторая копия форматтера из
    app/handlers/admin/stats.py."""
    import app.handlers.common.utils as utils

    assert not hasattr(utils, name)


@pytest.mark.parametrize("name", [
    # Строила кнопки broadcast_type:info|maintenance|security|promo, для
    # которых во всём репозитории нет обработчика. Живой поток рассылок —
    # в веб-дашборде.
    "get_broadcast_type_keyboard",
    # Просила ключи buy.tariff_button_<tariff> (basic, plus, biz_pro…),
    # а в словарях есть только buy.tariff_button_1/_3/_6/_12 — устаревшие
    # ключи по ПЕРИОДАМ и с ценами, которых давно нет в config.TARIFFS.
    # Вернись она в оборот — экран выбора тарифа стал бы списком
    # «buy.tariff_button_biz_pro».
    "get_tariff_keyboard",
    # Экран выдачи ключей в боте удалён вместе с ними.
    "get_profile_keyboard_old",
    "get_profile_keyboard_with_copy",
    "get_vpn_key_keyboard",
    "get_connect_button",
])
def test_dead_keyboard_builders_stay_removed(name):
    import app.handlers.common.keyboards as keyboards

    assert not hasattr(keyboards, name)


def test_main_menu_has_no_empty_branch():
    """Разбираем ast, а не текст: в комментариях ветка описана намеренно."""
    tree = ast.parse((COMMON / "keyboards.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "get_main_menu_keyboard"
    )
    empty = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.If)
        and all(isinstance(s, ast.Pass) for s in n.body)
        and not n.orelse
    ]
    assert not empty, f"пустая ветка-заглушка на строках {empty}"

    names = {
        n.id for n in ast.walk(fn)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    assert "offer_shown" not in names, "вернулся флаг, который никто не читает"


def test_safe_edit_text_does_not_compare_plaintext_with_html():
    """Сравнение message.text с HTML-текстом не работает по определению."""
    src = inspect.getsource(
        __import__("app.handlers.common.utils", fromlist=["safe_edit_text"]).safe_edit_text
    )
    assert "current_text" not in src, (
        "вернулась сверка отрендеренного текста с HTML — она либо не "
        "работает, либо глушит обновление экранов с разметкой"
    )
