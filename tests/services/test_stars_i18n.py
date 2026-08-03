"""Экран покупки Telegram Stars должен говорить на языке пользователя.

Дефект: весь сценарий покупки звёзд был собран из русских литералов —
заголовок, кнопки выбора получателя, подсказка про @username, экран
подтверждения, названия способов оплаты, текст успеха и даже title/description
инвойса Telegram. Язык пользователя в обработчиках вычислялся и уходил
ровно в две кнопки «Назад».

Человек с языком en/de/ar/kk/tj/uz попадал из переведённого меню на русский
платёжный экран. Для арабского это ещё и ломает направление текста. Хуже
всего инвойс: пользователь подтверждает списание денег по описанию, которое
не может прочитать.

Тест сторожит два условия: в коде экрана не осталось русских литералов,
уходящих на экран, и каждый запрошенный ключ stars.* существует во всех семи
словарях (иначе фолбэк вернёт русский текст — то, с чего начали).
"""
import ast
import re
from pathlib import Path

import pytest

from app.i18n import LANGUAGES, get_text

LANGS = ["ru", "en", "de", "ar", "kk", "tj", "uz"]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
SRC = Path("app/handlers/payments/telegram_stars_purchase.py")

_KEY_LITERAL = re.compile(r'i18n_get_text\(\s*\w+,\s*[\'"]([\w.]+)[\'"]')

# Сообщение админу локализовать незачем: адресат один и говорит по-русски.
_ADMIN_ONLY_FUNC = "send_stars_success"


def _tree():
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                ids.add(id(node.body[0].value))
    return ids


def _admin_message_ids(tree):
    """Строки внутри переменной admin_text — они уходят одному админу."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "admin_text" in targets:
                for sub in ast.walk(node.value):
                    ids.add(id(sub))
    return ids


def test_no_russian_literals_on_stars_screens():
    """Ни одной русской строки в коде экрана, кроме сообщения админу."""
    tree = _tree()
    skip = _docstring_ids(tree) | _admin_message_ids(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        if CYRILLIC.search(node.value):
            offenders.append(f"line {node.lineno}: {node.value[:60]!r}")
    assert not offenders, (
        "русские литералы на экране покупки Stars:\n" + "\n".join(offenders)
    )


def _requested_keys():
    return sorted(set(_KEY_LITERAL.findall(SRC.read_text(encoding="utf-8"))))


def test_screen_actually_requests_stars_keys():
    """Защита от пустого теста: ключи stars.* действительно запрашиваются."""
    keys = [k for k in _requested_keys() if k.startswith("stars.")]
    assert len(keys) >= 15, f"ключей stars.* подозрительно мало: {keys}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_requested_key_exists_in_every_language(lang):
    """Пропущенный ключ = молчаливый откат на русский для всех языков."""
    missing = [k for k in _requested_keys() if k not in LANGUAGES[lang]]
    assert not missing, f"в словаре {lang} нет ключей: {missing}"


@pytest.mark.parametrize("lang", [ln for ln in LANGS if ln != "ru"])
def test_non_russian_screens_are_not_russian(lang):
    """Ключевые экраны у нерусского пользователя не должны быть кириллицей.

    Для de/ar/kk/tj/uz значения временно английские — это осознанный выбор:
    выдуманный перевод платёжного экрана опаснее честного английского.
    """
    for key in ("stars.choose_pack", "stars.confirm", "stars.success",
                "stars.invoice_description"):
        text = get_text(lang, key, stars=100, username="@user", price=199)
        assert not CYRILLIC.search(text), f"{lang}/{key} остался русским: {text[:60]!r}"


def test_placeholders_render_without_leftovers():
    """Плейсхолдеры подставляются, а не уезжают на экран как {stars}."""
    text = get_text("en", "stars.confirm", stars=250, username="@bob", price=479)
    assert "250" in text and "@bob" in text and "479" in text
    assert "{" not in text


def test_invoice_title_fits_telegram_limit():
    """Telegram режет title инвойса на 32 символах — проверяем все языки."""
    for lang in LANGS:
        title = get_text(lang, "stars.invoice_title")
        assert len(title) <= 32, f"{lang}: заголовок инвойса длиннее 32 символов"
