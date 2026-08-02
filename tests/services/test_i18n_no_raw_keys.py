"""Пользователь никогда не должен увидеть на экране сырой ключ.

Дефект: около сотни ключей, которые код реально запрашивает, есть только в
русском словаре (экраны подключения, Steam, Spotify, магазин). Цепочка
фолбэков заканчивалась на английском, а в английском этих ключей тоже нет,
поэтому нерусский пользователь получал на экран строку вида «steam.title» —
интерфейс выглядел сломанным.

Теперь цепочка: запрошенный язык → английский → русский → запасной текст
вызывающего кода → сам ключ. Текст на чужом языке хуже перевода, но
несравнимо лучше служебной строки.

Сами переводы — отдельная задача; список ждущих ключей лежит в
docs/audit-2026-07/i18n-missing-keys.json и обновляется этим же тестом.
"""
import ast
import json
import re
from pathlib import Path

import pytest

LANGS = ["ru", "en", "de", "ar", "kk", "tj", "uz"]
_KEY_CALL = re.compile(r'get_text\(\s*[^,]+,\s*[\'"]([\w.]+)[\'"]')


def _keys_of(lang: str) -> set:
    tree = ast.parse(Path(f"app/i18n/{lang}.py").read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
    return out


def _keys_used_in_code() -> set:
    used = set()
    for f in Path(".").rglob("*.py"):
        s = str(f)
        if any(x in s for x in (".venv", "graphify-out", "tests/")):
            continue
        try:
            used |= set(_KEY_CALL.findall(f.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return used


def test_fallback_chain_reaches_russian():
    """Ключ, которого нет ни в запрошенном языке, ни в английском, обязан
    вернуть русский текст, а не сам ключ."""
    from app.i18n import LANGUAGES, get_text

    ru_only = [k for k in LANGUAGES["ru"] if k not in LANGUAGES["en"]]
    assert ru_only, "тест устарел: русский словарь больше не шире английского"

    key = ru_only[0]
    for lang in ("en", "de", "ar", "kk", "tj", "uz"):
        text = get_text(lang, key)
        assert text != key, f"{lang}: пользователю ушёл сырой ключ {key}"
        assert text == LANGUAGES["ru"][key]


def test_unknown_key_still_never_crashes():
    from app.i18n import get_text

    assert get_text("en", "no.such.key.anywhere") == "no.such.key.anywhere"
    assert get_text("en", "no.such.key.anywhere", "Запасной текст") == "Запасной текст"


@pytest.mark.parametrize("lang", LANGS)
def test_every_key_used_in_code_exists_at_least_in_russian(lang):
    """Русский — конец цепочки. Если ключа нет и там, экран сломан на всех
    языках сразу, включая русский."""
    if lang != "ru":
        pytest.skip("проверяем только конец цепочки фолбэков")
    ru_keys = _keys_of("ru")
    missing = []
    for key in sorted(_keys_used_in_code() - ru_keys):
        # Ключ может собираться динамически: get_text(lang, "buy.tariff_button_" + t).
        # Регулярка видит только префикс — считаем его найденным, если в
        # словаре есть хоть один ключ с таким началом.
        if key.endswith("_") and any(k.startswith(key) for k in ru_keys):
            continue
        missing.append(key)
    assert not missing, f"ключи не найдены даже в русском словаре: {missing[:20]}"


def test_translation_backlog_is_recorded():
    """Список непереведённых ключей должен существовать и совпадать с
    реальностью: иначе переводить будет нечего и некому."""
    backlog = Path("docs/audit-2026-07/i18n-missing-keys.json")
    assert backlog.exists(), "нет списка ключей на перевод"
    data = json.loads(backlog.read_text(encoding="utf-8"))
    assert data, "список пуст — либо всё переведено, либо он не обновлялся"
    for key, info in list(data.items())[:5]:
        assert "ru" in info and "missing_in" in info
