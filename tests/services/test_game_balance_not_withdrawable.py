"""Заработанное в мини-играх нельзя вывести на карту.

Дефект: награда за урожай начислялась через общий increase_balance в
users.balance — тот же счёт, что пополнения картой и реферальные выплаты.
Признака происхождения у денег не было, и намайненное на ферме уходило в
заявку на вывод: игра фактически печатала выводимые деньги, а единственным
барьером было ручное одобрение админом, который этого не видел.

Правило учёта (database.users.get_balance_breakdown): любая трата внутри
бота сначала съедает игровые деньги и только потом реальные; выводы в
списание игровой доли не входят — иначе один вывод «отмывал» бы следующую
порцию фарма.
"""
from unittest.mock import AsyncMock

import pytest


class FakeConn:
    """Отдаёт заранее заданные суммы, как один SQL-запрос из breakdown."""

    def __init__(self, balance, game_credits, internal_spend):
        self._row = {
            "balance": balance,
            "game_credits": game_credits,
            "internal_spend": internal_spend,
        }

    async def fetchrow(self, *_args, **_kwargs):
        return self._row


async def _breakdown(balance, game_credits, internal_spend):
    from database.users import get_balance_breakdown
    return await get_balance_breakdown(
        1, conn=FakeConn(balance, game_credits, internal_spend),
    )


@pytest.mark.asyncio
async def test_pure_game_balance_is_not_withdrawable():
    """Ничего не вносил, всё нафармил — вывести нельзя ничего."""
    b = await _breakdown(balance=80_000, game_credits=80_000, internal_spend=0)
    assert b["withdrawable"] == 0
    assert b["game_locked"] == 80_000


@pytest.mark.asyncio
async def test_real_money_stays_withdrawable():
    """Пополнил картой 1000 ₽, нафармил 800 ₽ — выводится ровно 1000 ₽."""
    b = await _breakdown(balance=180_000, game_credits=80_000, internal_spend=0)
    assert b["withdrawable"] == 100_000
    assert b["game_locked"] == 80_000


@pytest.mark.asyncio
async def test_internal_spending_burns_game_money_first():
    """Потратил 800 ₽ на подписку — игровые сгорели первыми, реальные целы."""
    b = await _breakdown(
        balance=100_000, game_credits=80_000, internal_spend=80_000,
    )
    assert b["game_locked"] == 0
    assert b["withdrawable"] == 100_000


@pytest.mark.asyncio
async def test_no_game_earnings_means_full_balance_withdrawable():
    b = await _breakdown(balance=50_000, game_credits=0, internal_spend=0)
    assert b["withdrawable"] == 50_000
    assert b["game_locked"] == 0


@pytest.mark.asyncio
async def test_game_locked_never_exceeds_balance():
    """Нафармил и потратил больше, чем осталось: блокировка не может быть
    больше самого баланса, иначе выводимое ушло бы в минус."""
    b = await _breakdown(balance=10_000, game_credits=90_000, internal_spend=0)
    assert b["game_locked"] == 10_000
    assert b["withdrawable"] == 0


@pytest.mark.asyncio
async def test_withdrawal_request_rejects_game_funds(monkeypatch):
    """Заявка на вывод отклоняется до списания баланса."""
    from database import users as users_mod

    executed = []

    class Conn:
        def transaction(self):
            class T:
                async def __aenter__(_s): return None
                async def __aexit__(_s, *a): return False
            return T()

        async def execute(self, sql, *args):
            executed.append(sql)

        async def fetchrow(self, sql, *args):
            return {"balance": 100_000}

    class Pool:
        def acquire(self):
            class C:
                async def __aenter__(_s): return Conn()
                async def __aexit__(_s, *a): return False
            return C()

    monkeypatch.setattr(users_mod._core, "DB_READY", True, raising=False)
    monkeypatch.setattr(users_mod, "get_pool", AsyncMock(return_value=Pool()))
    monkeypatch.setattr(
        users_mod, "get_balance_breakdown",
        AsyncMock(return_value={
            "balance": 100_000, "withdrawable": 0,
            "game_locked": 100_000, "game_credits": 100_000,
        }),
    )

    wid = await users_mod.create_withdrawal_request(
        1, "user", 50_000, "+79990000000",
    )

    assert wid is None, "намайненные деньги ушли в заявку на вывод"
    assert not any("UPDATE users SET balance" in s for s in executed), (
        "баланс списан несмотря на отказ"
    )
