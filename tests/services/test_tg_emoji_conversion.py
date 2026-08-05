"""Premium-эмодзи конвертируются одинаково на всех путях отправки.

ЧТО БЫЛО

    Одну и ту же задачу — перевести Ads-формат ![👑](tg://emoji?id=123) в
    HTML-тег <tg-emoji> — решали две независимые реализации:

      • app/utils/telegram_safe.py:convert_tg_emoji — через неё идут
        safe_send_message, safe_edit_text и broadcast_delivery;
      • app/api/dashboard/routes/broadcasts/keyboard.py:normalize_premium_emoji —
        через неё идут создание рассылки в дашборде и планировщик.

    Метка ловилась по-разному: `.+?` без re.DOTALL не переходит на новую
    строку, `[^\\]]+?` — переходит. Админ вставляет разметку с переносом
    внутри скобок, видит в дашборде одно, а люди получают другое.

    Ни одна из двух не экранировала метку, хотя результат уходит с
    parse_mode="HTML": символ & или < внутри ![...] ронял разбор всего
    сообщения, и рассылка падала целиком.

ЧТО СДЕЛАНО

    Копия из дашборда убрана: normalize_premium_emoji теперь второе имя
    для convert_tg_emoji. Расходиться больше нечему, и путь дашборда
    получил экранирование метки, которого у него не было.

ЧТО ПРОВЕРЯЕМ

    Реализация ровно одна, оба имени ведут к ней, метка экранируется.
"""
import ast
from pathlib import Path

import pytest

from app.utils.telegram_safe import convert_tg_emoji

# normalize_premium_emoji переехала сюда при разрезании роута рассылок на
# пакет.
DASHBOARD_KEYBOARD = Path("app/api/dashboard/routes/broadcasts/keyboard.py")


def test_dashboard_name_points_at_the_canonical_implementation():
    from app.api.dashboard.routes.broadcasts.keyboard import (
        normalize_premium_emoji,
    )

    assert normalize_premium_emoji is convert_tg_emoji, (
        "у дашборда снова своя конвертация premium-эмодзи — она разойдётся "
        "с той, через которую сообщение уходит на самом деле"
    )


def test_no_second_regex_grew_back():
    """Своя re.compile в дашборде = своя реализация, даже если имя одно."""
    tree = ast.parse(DASHBOARD_KEYBOARD.read_text(encoding="utf-8"))
    compiles = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "compile"
    ]
    assert not compiles, (
        "в keyboard.py снова компилируется своя регулярка — "
        f"строк {[n.lineno for n in compiles]}"
    )


@pytest.mark.parametrize("text", [
    "![👑](tg://emoji?id=5319247469165433798)",
    "перед ![🎮](tg://emoji?id=1) после",
    "![две строки\nв метке](tg://emoji?id=42)",
    "без эмодзи вообще",
    "<tg-emoji emoji-id=\"1\">👑</tg-emoji>",  # уже HTML, трогать нечего
])
def test_dashboard_path_converts_exactly_like_the_send_path(text):
    from app.api.dashboard.routes.broadcasts.keyboard import (
        normalize_premium_emoji,
    )

    assert normalize_premium_emoji(text) == convert_tg_emoji(text), (
        f"расхождение на {text!r} — админ увидит в превью не то, "
        f"что получат люди"
    )


def test_dashboard_path_escapes_the_label():
    """Ровно тот дефект, ради которого копия и убрана."""
    from app.api.dashboard.routes.broadcasts.keyboard import (
        normalize_premium_emoji,
    )

    out = normalize_premium_emoji("![A & B <c>](tg://emoji?id=7)")
    assert "&amp;" in out and "&lt;c&gt;" in out, (
        "метка не экранирована: один такой символ роняет разбор всего "
        "сообщения, и рассылка падает целиком"
    )


def test_conversion_produces_html_tag():
    out = convert_tg_emoji("![👑](tg://emoji?id=5319247469165433798)")
    assert out == '<tg-emoji emoji-id="5319247469165433798">👑</tg-emoji>'


def test_label_is_escaped():
    """& и < в метке ломали parse_mode=HTML и роняли всю отправку."""
    out = convert_tg_emoji("![A & B <c>](tg://emoji?id=7)")
    assert "&amp;" in out and "&lt;c&gt;" in out
    assert "<c>" not in out


def test_idempotent_on_already_converted_html():
    html = '<tg-emoji emoji-id="7">👑</tg-emoji>'
    assert convert_tg_emoji(html) == html


def test_empty_text_survives():
    assert convert_tg_emoji("") == ""
    assert convert_tg_emoji(None) is None
