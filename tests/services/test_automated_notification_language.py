"""Язык автоуведомлений.

Дефект: в таблице automated_notifications лежит только русский текст
(default_text_ru + админский custom_text_ru), а типовой вызов выглядел так:

    text = (await get_notification_text(key)) or i18n.get_text(language, key)

Строка в БД существует всегда — sync_registry_to_db() создаёт её при старте
бота, — поэтому левая часть никогда не была None, и i18n-фолбэк был мёртвым
кодом. Все автонапоминания уходили на русском независимо от языка
пользователя.

Теперь get_notification_text принимает language и для не-'ru' возвращает
None, чтобы вызывающий код взял перевод из i18n.
"""
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_non_russian_language_returns_none(monkeypatch):
    from app.services.automated_notifications import helper

    async def fake_row(_key):
        return {"is_enabled": True, "text": "русский текст", "trigger_config": {}}

    monkeypatch.setattr(helper, "get_row", fake_row)

    for lang in ("en", "de", "EN", " es "):
        assert await helper.get_notification_text("k", language=lang) is None, (
            f"для языка {lang!r} вернулся русский текст из БД"
        )


@pytest.mark.asyncio
async def test_russian_still_uses_db_text(monkeypatch):
    from app.services.automated_notifications import helper

    async def fake_row(_key):
        return {"is_enabled": True, "text": "русский текст", "trigger_config": {}}

    monkeypatch.setattr(helper, "get_row", fake_row)

    assert await helper.get_notification_text("k", language="ru") == "русский текст"
    # Дефолт параметра — ru, старые вызовы без языка не ломаются.
    assert await helper.get_notification_text("k") == "русский текст"


@pytest.mark.asyncio
async def test_disabled_notification_returns_none_for_russian(monkeypatch):
    """Выключенное админом уведомление по-прежнему даёт None."""
    from app.services.automated_notifications import helper

    async def fake_row(_key):
        return {"is_enabled": False, "text": "русский текст", "trigger_config": {}}

    monkeypatch.setattr(helper, "get_row", fake_row)
    assert await helper.get_notification_text("k", language="ru") is None


@pytest.mark.asyncio
async def test_params_are_rendered_for_russian(monkeypatch):
    from app.services.automated_notifications import helper

    async def fake_row(_key):
        return {"is_enabled": True, "text": "Осталось {days} дней", "trigger_config": {}}

    monkeypatch.setattr(helper, "get_row", fake_row)
    text = await helper.get_notification_text(
        "k", language="ru", params={"days": 3},
    )
    assert text == "Осталось 3 дней"


def test_all_call_sites_pass_language():
    """Вызов без language снова сделает фолбэк недостижимым, поэтому
    проверяем каждый рантайм-вызов в коде бота."""
    files = [
        Path("reminders.py"),
        Path("trial_notifications.py"),
        Path("app/handlers/callbacks/payments_callbacks.py"),
    ]
    offenders = []
    for f in files:
        for num, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            called = ("await get_notification_text(" in line
                      or "await _get_text_impl(" in line
                      or "await _autonotif_text(" in line)
            if called and "language=" not in line:
                offenders.append(f"{f}:{num}: {line.strip()}")
    assert not offenders, "вызовы без language:\n" + "\n".join(offenders)
