"""Поведение get_text при отсутствующих ключах и плейсхолдерах.

Дефект: третьим позиционным параметром стоял strict, поэтому 99 вызовов вида
get_text(lang, key, "Запасной текст") молча теряли запасной текст, и
пользователь видел сырой ключ вместо сообщения.
"""
import pytest

from app.i18n import get_text, LANGUAGES


MISSING_KEY = "zzz.definitely.missing.key"


def test_missing_key_uses_provided_default():
    """Главный дефект: запасной текст должен использоваться, а не игнорироваться."""
    assert get_text("ru", MISSING_KEY, "Запасной текст") == "Запасной текст"


def test_missing_key_without_default_returns_key():
    """Прежнее поведение сохранено, когда запасного текста нет."""
    assert get_text("ru", MISSING_KEY) == MISSING_KEY


def test_default_supports_placeholders():
    result = get_text("ru", MISSING_KEY, "Осталось {days} дней", days=3)
    assert result == "Осталось 3 дней"


def test_existing_key_ignores_default():
    """Настоящий перевод важнее запасного текста."""
    real_key = next(iter(LANGUAGES["ru"]))
    assert get_text("ru", real_key, "не должно появиться") != "не должно появиться"


def test_empty_default_falls_back_to_key():
    assert get_text("ru", MISSING_KEY, "") == MISSING_KEY


def test_unknown_language_falls_back_to_default_language():
    real_key = next(iter(LANGUAGES["ru"]))
    assert get_text("xx", real_key) == LANGUAGES["ru"][real_key]


def test_missing_placeholder_does_not_raise():
    """Плохой плейсхолдер не должен ронять обработчик — пользователь
    получит неформатированный текст вместо отсутствия ответа."""
    result = get_text("ru", MISSING_KEY, "Привет, {name}!", wrong_arg="x")
    assert result == "Привет, {name}!"


def test_database_unavailable_key_has_usable_output():
    """errors.database_unavailable отсутствует в ru — запасной текст обязателен.

    Без него пользователь игр видел строку 'errors.database_unavailable'.
    """
    out = get_text("ru", "errors.database_unavailable", "База данных временно недоступна")
    assert out == "База данных временно недоступна"
    assert "errors." not in out
