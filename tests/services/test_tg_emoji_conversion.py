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

ЧТО ПРОВЕРЯЕМ

    Обе реализации ловят один и тот же набор входов, а канонический
    convert_tg_emoji экранирует метку.
"""
import re
from pathlib import Path

import pytest

from app.utils.telegram_safe import convert_tg_emoji, _TG_ADS_EMOJI_RE

# normalize_premium_emoji переехала сюда при разрезании роута рассылок на
# пакет. Читаем исходник, а не импортируем: тесту не нужен FastAPI.
DASHBOARD_ROUTE = Path("app/api/dashboard/routes/broadcasts/keyboard.py")


def _dashboard_regex():
    """Регулярка дашборда — читаем из исходника, чтобы не тянуть FastAPI."""
    src = DASHBOARD_ROUTE.read_text(encoding="utf-8")
    m = re.search(r'_MD_TG_EMOJI_RE\s*=\s*re\.compile\(\s*r"([^"]+)"', src)
    assert m, "не нашли _MD_TG_EMOJI_RE в дашборд-роуте"
    return re.compile(m.group(1))


@pytest.mark.parametrize("text", [
    "![👑](tg://emoji?id=5319247469165433798)",
    "перед ![🎮](tg://emoji?id=1) после",
    "![две строки\nв метке](tg://emoji?id=42)",
    "без эмодзи вообще",
    "<tg-emoji emoji-id=\"1\">👑</tg-emoji>",  # уже HTML, трогать нечего
])
def test_both_implementations_match_the_same_inputs(text):
    ours = bool(_TG_ADS_EMOJI_RE.search(text))
    theirs = bool(_dashboard_regex().search(text))
    assert ours == theirs, (
        f"расхождение на {text!r}: telegram_safe={ours}, дашборд={theirs} — "
        f"админ увидит в превью не то, что получат люди"
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
