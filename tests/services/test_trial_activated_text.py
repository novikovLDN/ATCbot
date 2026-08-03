"""Активация триала не должна ходить в БД за ссылкой, которую никто не покажет.

Дефект (app/handlers/callbacks/subscription.py:282): на пути активации
триала импортировался html.escape, дёргался
get_user_primary_subscription_url, результат экранировался и уходил в
get_text(..., 'trial.activated', sub_url=...). Плейсхолдера {sub_url}
нет ни в одном из семи словарей, а лишние kwargs str.format молча
глотает — то есть был лишний поход в БД ровно там, где юзер ждёт
ответа, ради значения, которое никогда не рендерилось.

Решили НЕ добавлять ссылку в тексты, а убрать вычисление:
  • ключи в боте не отдаём — по этой же причине снесён экран
    get_sub_key;
  • get_user_primary_subscription_url теперь может законно вернуть
    пустую строку, подставлять её в сообщение об успехе нечего;
  • на экран установки юзер попадает кнопкой «Настроить устройство».
"""
import re
from pathlib import Path

import pytest

from app.i18n import LANGUAGES, get_text

HANDLER = Path("app/handlers/callbacks/subscription.py")
ALL_LANGS = sorted(LANGUAGES)


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_no_language_declares_sub_url_placeholder(lang):
    """Если кто-то добавит {sub_url} в словарь, но не вернёт вычисление,
    юзер увидит сырой плейсхолдер — тест ловит рассинхрон в обе стороны."""
    text = LANGUAGES[lang].get("trial.activated")
    if text is None:
        pytest.skip(f"{lang}: ключа нет, работает фолбэк")
    assert "{sub_url}" not in text


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_trial_activated_renders_without_leftover_placeholders(lang):
    """Сообщение об активации собирается ровно одним аргументом."""
    rendered = get_text(lang, "trial.activated", expires_date="01.01.2027")
    assert "{" not in rendered and "}" not in rendered, (
        f"{lang}: в тексте остался неподставленный плейсхолдер: {rendered!r}"
    )


def test_handler_no_longer_fetches_subscription_url_on_trial_path():
    """Сетевой/БД-вызов ради выброшенного значения — это задержка на
    самом чувствительном экране."""
    # Комментарии не считаем — в самом обработчике объяснено, почему
    # вызов убрали, и имя функции там упоминается намеренно.
    code_lines = [
        line for line in HANDLER.read_text(encoding="utf-8").split("\n")
        if not line.lstrip().startswith("#")
    ]
    offenders = [line.strip() for line in code_lines if "get_user_primary_subscription_url" in line]
    assert not offenders, (
        f"в обработчике снова считается sub_url, которого нет ни в одном словаре: {offenders}"
    )


def test_trial_activated_call_passes_only_expires_date():
    source = HANDLER.read_text(encoding="utf-8")
    calls = re.findall(r'get_text\(\s*language,\s*"trial\.activated"[^)]*\)', source)
    assert calls, "вызов trial.activated пропал — экран активации сломан"
    for call in calls:
        assert "sub_url" not in call, f"sub_url снова передаётся: {call}"
        assert "expires_date" in call, f"потерян expires_date: {call}"
