"""HOW_IT_WORKS P2: user-pressed discount buttons must not lower a bigger
existing personal discount. The broadcast discount buttons call
create_user_discount(keep_max=True); the SQL itself is tested on Postgres in
tests/db/test_user_discount_keep_max.py. The dashboard admin keeps the plain
overwrite.

The −15 % buttons (trial / paid ending) no longer create a personal discount
at all (owner 2026-09-14): they use the period's ONE 72 h −15 % window, never
extend it, and name its end (tests/services/test_minus15_window.py).
"""
from datetime import datetime, timedelta, timezone
import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

import database

ROOT = pathlib.Path(__file__).resolve().parents[2]
OFFER = {"expires_at": datetime(2026, 9, 17, 11, 30, tzinfo=timezone.utc) + timedelta(0),
         "discount_percent": 15, "remaining_text": "2д"}


@pytest.mark.parametrize("handler", ["callback_trial_discount_15", "callback_paid_discount_15"])
async def test_minus_15_buttons_keep_the_bigger_discount(monkeypatch, handler):
    from app.handlers.callbacks import navigation
    import app.handlers.common.screens as screens

    create = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "create_user_discount", create)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(return_value={"discount_percent": 15}))
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value=OFFER))
    monkeypatch.setattr(navigation, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(screens, "_open_buy_screen", AsyncMock())
    callback = MagicMock()
    callback.from_user.id = 7
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    await getattr(navigation, handler)(callback, MagicMock())
    create.assert_not_awaited()        # no personal discount: a bigger one can never be lowered


async def _press(monkeypatch, handler, *, lang, current):
    from app.handlers.callbacks import navigation
    import app.handlers.common.screens as screens

    monkeypatch.setattr(database, "create_user_discount", AsyncMock(return_value=True))
    monkeypatch.setattr(database, "get_special_offer_info", AsyncMock(return_value=OFFER))
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(
        return_value={"discount_percent": current} if current is not None else None))
    monkeypatch.setattr(navigation, "resolve_user_language", AsyncMock(return_value=lang))
    monkeypatch.setattr(screens, "_open_buy_screen", AsyncMock())
    callback = MagicMock()
    callback.from_user.id = 7
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    await getattr(navigation, handler)(callback, MagicMock())
    return callback.message.answer.await_args.args[0]


@pytest.mark.parametrize("handler", ["callback_trial_discount_15", "callback_paid_discount_15"])
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_minus_15_with_a_bigger_discount_says_the_bigger_one_stays(monkeypatch, handler, lang):
    """A bigger active discount is kept (keep_max) — the message must not claim
    «Скидка 15% применена»; it says the user's current bigger discount stays."""
    from app.i18n import get_text

    text = await _press(monkeypatch, handler, lang=lang, current=30)

    assert text == get_text(lang, "main.discount_bigger_kept", percent=30)
    assert "30%" in text and "15%" not in text


@pytest.mark.parametrize("handler", ["callback_trial_discount_15", "callback_paid_discount_15"])
@pytest.mark.parametrize("current", [15, None])
async def test_minus_15_applied_still_says_15(monkeypatch, handler, current):
    from app.i18n import get_text

    text = await _press(monkeypatch, handler, lang="ru", current=current)

    from app.services.notifications.special_offer import format_deadline
    assert text == get_text("ru", "main.discount_applied_choose_tariff",
                            deadline=format_deadline("ru", OFFER["expires_at"]))


def _calls(path):
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "create_user_discount":
            yield node


def test_every_broadcast_button_discount_keeps_the_max():
    calls = list(_calls("app/handlers/payments/broadcast_offers.py"))
    assert calls
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "keep_max" in kw and getattr(kw["keep_max"], "value", None) is True, ast.dump(call)[:200]


def test_dashboard_admin_still_overwrites():
    for call in _calls("app/api/dashboard/routes/users.py"):
        assert "keep_max" not in {k.arg for k in call.keywords}
