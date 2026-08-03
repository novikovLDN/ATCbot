"""Штатный валидатор i18n не должен считать казахский и таджикский браком.

Дефект (validate_language_content.py:21): секция «Cyrillic violations»
считала нарушением ЛЮБУЮ кириллицу вне ru.py, исключая только три
ключа lang.button_ru/kk/tj. Но казахский и таджикский пишутся
кириллицей — это их родная письменность. На живом прогоне выходило
1068 «нарушений», почти все на совершенно нормальных строках
(«Мақұлдау», «Қатысушы құқықтары жеткіліксіз»).

Почему это важно: скрипт из-за этого ВСЕГДА заканчивался
VALIDATION FAILED, поэтому его нельзя было повесить гейтом в CI, а
реальные находки (расхождения ключей, английские заглушки) тонули в
тысяче ложных срабатываний. Проверка, которая всегда красная, — это
проверка, которую перестают читать.
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

validator = importlib.import_module("validate_language_content")


def test_kk_and_tj_are_not_checked_for_cyrillic():
    assert "kk" not in validator.CYRILLIC_CHECK_LANGS
    assert "tj" not in validator.CYRILLIC_CHECK_LANGS


def test_latin_and_arabic_languages_are_still_checked():
    """Русская строка, забытая в en/de/uz/ar, — настоящий дефект, и
    ловить его по-прежнему надо."""
    assert validator.CYRILLIC_CHECK_LANGS == frozenset({"en", "de", "ar", "uz"})


def test_real_kazakh_and_tajik_strings_are_not_reported():
    from app.i18n import LANGUAGES

    violations = validator.detect_cyrillic(LANGUAGES)
    reported_langs = {code for code, _key, _preview in violations}
    assert not (reported_langs & {"kk", "tj"}), (
        "казахский/таджикский снова помечены как нарушение кириллицы"
    )


def test_planted_russian_string_in_german_is_still_caught():
    """Проверка не должна выродиться в «ничего не проверяем»."""
    languages = {
        "ru": {"x": "Оплата"},
        "de": {"x": "Оплата"},          # забытый русский текст в немецком
        "kk": {"x": "Төлем"},           # нормальный казахский
    }
    violations = validator.detect_cyrillic(languages)
    assert [(c, k) for c, k, _ in violations] == [("de", "x")]


@pytest.mark.parametrize("key", sorted(validator.CYRILLIC_ALLOWED_KEYS))
def test_native_language_names_stay_whitelisted(key):
    """«🇷🇺 Русский» и «🇰🇿 Қазақша» в селекторе языка пишутся на родном
    языке во всех словарях — это не нарушение."""
    languages = {"de": {key: "🇷🇺 Русский"}}
    assert validator.detect_cyrillic(languages) == []
