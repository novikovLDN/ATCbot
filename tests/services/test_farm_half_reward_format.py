"""Половина награды считается одним способом и показывается с копейками.

ЧТО БЫЛО

    Витрина раннего сбора и фактическое начисление считали половину награды
    по-разному. Кнопка — `reward // 200`, то есть сразу в рублях, с
    отбрасыванием копеек. Начисление — `reward // 2`, в копейках. Для наград
    с нечётным числом сотен копеек это расходилось:

        вишня 2700 → кнопка «+13 ₽», на баланс 13,50 ₽
        дуб    5300 → кнопка «+26 ₽», на баланс 26,50 ₽
        лимон  3600 → кнопка «+18 ₽», на баланс 18,00 ₽ (совпало случайно)

    Сейчас расхождение в пользу пользователя, поэтому жалоб нет. Но витрина
    и касса расходятся, а при обратном знаке округления это уже обращения в
    поддержку.

ЧТО ЗАКРЕПЛЯЕТ ТЕСТ

    Единственный способ посчитать половину — farm_half_reward_kopecks, и
    показывать её надо через format_kopecks_rub (два знака, как во всей
    остальной денежной части бота: «{amount:.2f} ₽» в словарях i18n).
    Тест ловит возврат старой формулы `// 200` для любой культуры.
"""
import pytest

from app.handlers.game import (
    PLANT_TYPES,
    farm_half_reward_kopecks,
    format_kopecks_rub,
)


@pytest.mark.parametrize("plant_key", sorted(PLANT_TYPES))
def test_showcase_matches_credited_amount(plant_key):
    """Показанное на кнопке = зачисленное на баланс, для каждой культуры."""
    reward = PLANT_TYPES[plant_key]["reward"]

    credited_kopecks = farm_half_reward_kopecks(reward)
    shown_rub = float(format_kopecks_rub(credited_kopecks))

    assert round(shown_rub * 100) == credited_kopecks, (
        f"{plant_key}: показываем {shown_rub} ₽, начисляем {credited_kopecks} коп."
    )


@pytest.mark.parametrize("plant_key", sorted(PLANT_TYPES))
def test_old_rub_rounding_is_not_used(plant_key):
    """Старая формула `reward // 200` не должна вернуться под видом округления."""
    reward = PLANT_TYPES[plant_key]["reward"]
    old_rub = reward // 200
    new_rub = farm_half_reward_kopecks(reward) / 100

    assert new_rub >= old_rub
    if reward % 200:
        assert new_rub > old_rub, (
            f"{plant_key}: половина награды снова теряет 50 копеек"
        )


def test_kopecks_with_tail_are_visible():
    assert format_kopecks_rub(2650) == "26.50"
    assert format_kopecks_rub(1800) == "18.00"
    assert format_kopecks_rub(0) == "0.00"


def test_half_of_odd_hundred_keeps_fifty_kopecks():
    assert farm_half_reward_kopecks(5300) == 2650
    assert farm_half_reward_kopecks(2700) == 1350
    assert farm_half_reward_kopecks(3600) == 1800


# ──────────────────────────────────────────────────────────────────────
#  Витрина и касса на живых экранах
#
#  Хелперы выше существовали, но ферма их не звала: кнопка по-прежнему
#  считала reward // 200, а алерт после сбора — half // 100. Тесты ниже
#  проверяют именно подключение, а не саму формулу.
# ──────────────────────────────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

OAK_REWARD = 5300      # копейки
OAK_HALF_SHOWN = "26.50"


def _growing_oak_plots(now):
    return [{
        "plot_id": 0, "status": "growing", "plant_type": "oak",
        "planted_at": now.isoformat(),
        "ready_at": (now + timedelta(days=30)).isoformat(),
        "dead_at": (now + timedelta(days=31)).isoformat(),
        "storm_shielded": False,
    }]


@pytest.mark.asyncio
async def test_early_harvest_button_shows_the_credited_amount(monkeypatch):
    """Кнопка «Собрать незрелым» называет ровно то, что уйдёт на баланс."""
    import app.handlers.farm.screen as screen

    assert PLANT_TYPES["oak"]["reward"] == OAK_REWARD, "награда дуба изменилась"

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(screen, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(screen, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(screen, "_get_imminent_storm", AsyncMock(return_value={
        "id": 1, "scheduled_at": now + timedelta(hours=5),
        "announced_at": now, "executed_at": None,
    }))
    monkeypatch.setattr(screen.database, "save_farm_plots", AsyncMock())

    callback = MagicMock()
    callback.from_user = MagicMock()
    callback.from_user.id = 777
    callback.answer = AsyncMock()
    callback.message = MagicMock()

    await screen._render_farm(
        callback, MagicMock(), _growing_oak_plots(now), plot_count=1, balance=0,
    )

    keyboard = screen.safe_edit_text.await_args.kwargs["reply_markup"]
    early = [
        b.text for row in keyboard.inline_keyboard for b in row
        if b.callback_data and b.callback_data.startswith("farm_early:")
    ]
    assert early, "кнопка раннего сбора не отрисовалась"
    assert OAK_HALF_SHOWN in early[0], (
        f"на кнопке не та сумма: {early[0]!r}, ждали {OAK_HALF_SHOWN}"
    )


@pytest.mark.asyncio
async def test_early_harvest_credits_and_reports_the_same_amount(monkeypatch):
    """Начисление и алерт после сбора — те же копейки, что на кнопке."""
    import app.handlers.farm.storm as storm

    now = datetime.now(timezone.utc)
    plot = _growing_oak_plots(now)[0]

    monkeypatch.setattr(storm, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(storm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(storm, "_render_farm", AsyncMock())
    monkeypatch.setattr(storm, "_get_imminent_storm", AsyncMock(return_value={
        "id": 1, "scheduled_at": now + timedelta(hours=5),
        "announced_at": now, "executed_at": None,
    }))
    monkeypatch.setattr(
        storm, "_find_growing_plot", AsyncMock(return_value=([plot], 1, 0, plot)),
    )
    harvest = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(storm.database, "harvest_plot_atomic", harvest)
    monkeypatch.setattr(storm.database, "get_pool", AsyncMock(return_value=MagicMock()))

    callback = MagicMock()
    callback.data = "farm_early:0"
    callback.from_user = MagicMock()
    callback.from_user.id = 777
    callback.answer = AsyncMock()
    callback.message = MagicMock()

    await storm.callback_farm_early_harvest(callback)

    credited = harvest.await_args.kwargs["reward_kopecks"]
    assert credited == farm_half_reward_kopecks(OAK_REWARD) == 2650

    alert = callback.answer.await_args.args[0]
    assert OAK_HALF_SHOWN in alert, (
        f"в алерте не та сумма: {alert!r}, начислено {credited} коп."
    )
