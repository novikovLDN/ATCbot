"""The main menu is a photo whose caption is the welcome text with the
dashboard incident banner on top. The dashboard accepts up to 2000 chars of
HTML for the banner, a caption holds 1024: a long banner (or a stray `<`)
made every main-menu send_photo fail with 400 for every user while the
incident mode was on. The banner is now fitted into the caption and invalid
HTML is shown as plain text.
"""
from __future__ import annotations

import pytest

import database
from app.handlers.common import utils
from app.i18n import LANGUAGES
from app.utils.telegram_html import CAPTION_LIMIT, telegram_html_errors, visible_length


def _incident(monkeypatch, text):
    async def fake():
        return {"is_active": True, "incident_text": text}

    monkeypatch.setattr(database, "DB_READY", True, raising=False)
    monkeypatch.setattr(database, "get_incident_settings", fake, raising=False)


def _longest_welcome(lang):
    return max((v for k, v in LANGUAGES[lang].items() if k.startswith("main.welcome")), key=visible_length)


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("incident", [
    "⚠️ " + "Перебои с оплатой через СБП. " * 70,           # 2000 chars of plain text
    "<b>Перебои</b> " + "с оплатой " * 190,                 # long valid HTML
    "Скорость < 1 Мбит & оплата <i>недоступна",             # invalid HTML
])
async def test_main_caption_with_incident_fits_and_parses(monkeypatch, lang, incident):
    _incident(monkeypatch, incident[:2000])
    caption = await utils.format_text_with_incident(_longest_welcome(lang), lang)
    assert visible_length(caption) <= CAPTION_LIMIT
    assert telegram_html_errors(caption) == []
    assert caption.endswith(_longest_welcome(lang))


async def test_short_valid_html_incident_is_kept_as_is(monkeypatch):
    _incident(monkeypatch, "⚠️ <b>Перебои</b> с оплатой через СБП.")
    caption = await utils.format_text_with_incident("welcome", "ru")
    assert "⚠️ <b>Перебои</b> с оплатой через СБП." in caption
    assert caption.endswith("welcome")


async def test_no_incident_returns_text_unchanged(monkeypatch):
    async def fake():
        return {"is_active": False, "incident_text": "x"}

    monkeypatch.setattr(database, "DB_READY", True, raising=False)
    monkeypatch.setattr(database, "get_incident_settings", fake, raising=False)
    assert await utils.format_text_with_incident("welcome", "ru") == "welcome"
