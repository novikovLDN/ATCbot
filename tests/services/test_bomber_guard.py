"""Бомбер: пейволл как у остальных игр и никакого залипшего FSM-состояния.

ЧТО БЫЛО

    callback_game_bomber стартовал партию без единой проверки — в отличие
    от боулинга, кубиков, меню игр и фермы, где подписка проверяется. Меню
    игр за пейволлом, но старое сообщение с инлайн-клавиатурой живёт в чате
    вечно: подписка истекла или возвращена, а кнопка «💣 Бомбер» в
    позавчерашнем сообщении по-прежнему открывает игру.

    Второе: выйти из бомбера можно двумя путями — «🚩 Завершить» (state
    чистится) и «🔙 К играм» (state оставался). BomberState.playing висел
    после ухода и мешал бы любому сценарию, который смотрит на состояние.

ПОЧЕМУ ИМЕННО ТАК

    Игру не удаляли: она переведена на 7 языков, стоит в меню и ничего не
    ломает экономически (ни ставки, ни приза) — это продуктовое решение
    владельца, а не дефект. Здесь закрыты только два технических хвоста.

    Алерт про подписку отправляется ПЕРВЫМ ответом на callback: Telegram
    учитывает ровно один answer на запрос, после пустого answer() алерт до
    пользователя не доходит.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


def _callback(telegram_id: int = 777):
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = telegram_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.bot = MagicMock()
    return cb


def _state(current=None):
    st = MagicMock()
    st.get_state = AsyncMock(return_value=current)
    st.set_state = AsyncMock()
    st.update_data = AsyncMock()
    st.clear = AsyncMock()
    return st


@pytest.fixture
def game(monkeypatch):
    import app.handlers.game as game

    monkeypatch.setattr(
        game, "ensure_db_ready_callback", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        game, "resolve_user_language", AsyncMock(return_value="ru"),
    )
    monkeypatch.setattr(game, "safe_edit_text", AsyncMock())
    return game


@pytest.mark.asyncio
async def test_bomber_blocks_user_without_subscription(game, monkeypatch):
    monkeypatch.setattr(game.database, "get_subscription", AsyncMock(return_value=None))
    cb, st = _callback(), _state()

    await game.callback_game_bomber(cb, st)

    st.set_state.assert_not_awaited()
    assert not game.safe_edit_text.called, "поле бомбера отрисовалось неплательщику"
    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    # Первый и единственный ответ — содержательный, иначе алерт не покажется.
    assert cb.answer.await_args.args and cb.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_bomber_starts_for_active_subscriber(game, monkeypatch):
    monkeypatch.setattr(
        game.database, "get_subscription",
        AsyncMock(return_value={"telegram_id": 777, "status": "active"}),
    )
    cb, st = _callback(), _state()

    await game.callback_game_bomber(cb, st)

    st.set_state.assert_awaited_once()
    st.update_data.assert_awaited_once()
    mines = st.update_data.await_args.kwargs["mines"]
    assert len(set(mines)) == 3 and all(0 <= m < 25 for m in mines)
    assert game.safe_edit_text.called


@pytest.mark.asyncio
async def test_db_error_does_not_lock_out_player(game, monkeypatch):
    """Сбой базы не повод отказать плательщику: партия ничего не стоит."""
    monkeypatch.setattr(
        game.database, "get_subscription", AsyncMock(side_effect=RuntimeError("pool is down")),
    )
    cb, st = _callback(), _state()

    await game.callback_game_bomber(cb, st)

    st.set_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_games_menu_clears_stale_bomber_state(game, monkeypatch):
    """Уход из партии кнопкой «К играм» не должен оставлять FSM в игре."""
    from app.handlers.common.states import BomberState

    monkeypatch.setattr(
        game.database, "get_subscription",
        AsyncMock(return_value={"telegram_id": 777, "status": "active"}),
    )
    monkeypatch.setattr(
        "app.handlers.common.screens._send_screen_photo", AsyncMock(),
    )
    cb, st = _callback(), _state(current=BomberState.playing.state)
    cb.message.delete = AsyncMock()

    await game.callback_games_menu(cb, st)

    st.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_games_menu_does_not_touch_foreign_state(game, monkeypatch):
    """Чужой сценарий (ввод суммы, промокод) рвать нельзя — чистим только бомбер."""
    monkeypatch.setattr(
        game.database, "get_subscription",
        AsyncMock(return_value={"telegram_id": 777, "status": "active"}),
    )
    monkeypatch.setattr(
        "app.handlers.common.screens._send_screen_photo", AsyncMock(),
    )
    cb, st = _callback(), _state(current="WithdrawState:amount")
    cb.message.delete = AsyncMock()

    await game.callback_games_menu(cb, st)

    st.clear.assert_not_awaited()
