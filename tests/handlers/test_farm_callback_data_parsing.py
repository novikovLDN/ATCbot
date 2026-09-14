"""R4 (docs/audit/11_telegram_runtime.md §5): the farm_* callback handlers did
int(callback.data.split("_")[-1]) — a forged or corrupted callback_data raised
ValueError, the error boundary logged an unhandled exception and the user got
a generic toast. Now the plot id is parsed defensively before anything else:
the callback is answered once with the "session expired" text in the user's
language, nothing is read or written.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from app.handlers import game
from app.i18n import get_text

TG = 6161

CASES = [
    (game.callback_farm_choose_plant, "farm_choose_x"),
    (game.callback_farm_choose_plant, "farm_choose_"),
    (game.callback_farm_plant, "farm_plant_x_tomato"),
    (game.callback_farm_plant, "farm_plant_0"),
    (game.callback_farm_plant, "farm_plant_0_banana"),
    (game.callback_farm_water, "farm_water_abc"),
    (game.callback_farm_water, "farm_water_²"),
    (game.callback_farm_fert, "farm_fert_1e9"),
    (game.callback_farm_harvest, "farm_harvest_-1x"),
    (game.callback_farm_remove, "farm_remove_zz"),
    (game.callback_farm_remove, "farm_remove_confirm_zz"),
    (game.callback_farm_dig, "farm_dig_x"),
    (game.callback_farm_dig_confirm, "farm_dig_confirm_x"),
]


@pytest.fixture
def world(monkeypatch):
    async def get_user(telegram_id):
        return {"telegram_id": telegram_id, "language": "en"}

    async def ready(*a, **k):
        return True

    async def no_storm():
        return None

    touched = AsyncMock()
    monkeypatch.setattr(database, "get_user", get_user)
    monkeypatch.setattr(game, "ensure_db_ready_callback", ready)
    monkeypatch.setattr(game, "_get_imminent_storm", no_storm)
    for name in ("get_pool", "get_farm_data", "update_farm_plot_atomic",
                 "harvest_plot_atomic", "save_farm_plots"):
        monkeypatch.setattr(database, name, touched)
    return touched


@pytest.mark.parametrize("handler,data", CASES, ids=[d for _, d in CASES])
async def test_malformed_farm_callback_is_answered_not_raised(world, handler, data):
    cb = SimpleNamespace(data=data, from_user=SimpleNamespace(id=TG), answer=AsyncMock(), message=MagicMock())

    await handler(cb, MagicMock())          # must not raise

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.args[0] == get_text("en", "errors.session_expired")
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    world.assert_not_awaited()              # no farm read / write on a forged button
