"""Owner decision 2026-09-14 (HOW_IT_WORKS P2 «change tariff: text vs behaviour»):
the behaviour stays — the new tariff applies IMMEDIATELY, to the whole
remaining subscription (database/subscriptions.py grant_access switch). The
screens promised «Новый тариф начнёт действовать после окончания текущей
подписки»; they now say it applies right away, in RU and EN (i18n, not a
hard-coded Russian string)."""
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from app.i18n import get_text

SRC = pathlib.Path(__file__).resolve().parents[2] / "app/handlers/payments/callbacks.py"
OLD = "после окончания текущей подписки"


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_switch_menu_says_the_change_is_immediate(monkeypatch, lang):
    from app.handlers.payments import callbacks as cb
    monkeypatch.setattr(cb, "resolve_user_language", AsyncMock(return_value=lang))
    monkeypatch.setattr(database, "get_subscription", AsyncMock(return_value={"subscription_type": "basic"}))
    edit = AsyncMock()
    monkeypatch.setattr(cb, "safe_edit_text", edit)
    callback = MagicMock()
    callback.from_user.id = 1
    callback.answer = AsyncMock()
    await cb.callback_switch_tariff_menu(callback, MagicMock())
    text = edit.await_args.args[1]
    assert OLD not in text
    assert get_text(lang, "tariff_switch.applies_now") in text


def test_applies_now_text_says_immediately():
    assert "сразу" in get_text("ru", "tariff_switch.applies_now")
    assert "right after" in get_text("en", "tariff_switch.applies_now")


def test_no_screen_promises_the_change_after_the_current_period():
    src = SRC.read_text()
    assert OLD not in src
    # menu + the tariff screen (one variable shared by the basic/plus and combo texts)
    assert src.count("tariff_switch.applies_now") >= 2
    assert src.count("{applies_now}") == 2
