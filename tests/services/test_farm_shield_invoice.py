"""Плёнка от шторма: честная сумма в счёте и запас времени на оплату.

Дефект 1 — деньги. Когда на балансе не хватало, экран писал «На балансе: 18 ₽
(не хватает 12 ₽)», а счёт и в Lava, и в СБП выставлялся на ПОЛНУЮ стоимость
плёнки. Человек читал «не хватает 12 ₽», жал «Картой» и получал счёт на 30 ₽,
причём его 18 ₽ оставались нетронутыми. Комбинированной оплаты в проекте нет,
поэтому экран обязан называть ту же сумму, которая уйдёт в счёт.

Дефект 2 — время. Счёт на плёнку можно было создать за минуту до удара.
Оплата картой или через СБП — это уход на страницу платёжки и ожидание
вебхука; за минуту он не успеет, шторм отработает раньше, грядка погибнет, а
деньги уже списаны. Дальше это разбирает поддержка вручную. Оплату с баланса
это не касается: она мгновенная и доступна до самого удара.
"""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.handlers.farm as farm

OAK_REWARD = 5300          # копейки, дуб
OAK_SHIELD_KOPECKS = 3000  # плёнка на дуб — 30 ₽
BALANCE_SHORT = 1800       # 18 ₽ на балансе: не хватает 12 ₽


def _storm(minutes_ahead: int):
    return {
        "id": 1,
        "scheduled_at": datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead),
        "announced_at": datetime.now(timezone.utc) - timedelta(hours=20),
        "executed_at": None,
    }


def _callback(data="farm_shield:0", telegram_id=555):
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = telegram_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    return cb


def _plot(shielded=False):
    return {"plot_id": 0, "status": "growing", "plant_type": "oak",
            "storm_shielded": shielded}


@pytest.fixture
def env(monkeypatch):
    """Общая обвязка: база готова, язык русский, грядка с дубом растёт."""
    monkeypatch.setattr(farm, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(farm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(farm, "_render_farm", AsyncMock())
    monkeypatch.setattr(farm, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(farm.database, "get_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        farm, "_find_growing_plot",
        AsyncMock(return_value=([_plot()], 1, BALANCE_SHORT, _plot())),
    )
    return monkeypatch


def _rub_amounts(text: str):
    """Все суммы в рублях, названные на экране."""
    return {m.replace(",", ".") for m in re.findall(r"(\d+(?:[.,]\d+)?)\s*₽", text)}


class TestInvoiceAmountMatchesScreen:
    @pytest.mark.asyncio
    async def test_screen_never_promises_the_shortfall(self, env):
        """Недостающий остаток не должен выглядеть суммой к оплате."""
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(180)))
        cb = _callback()

        await farm.callback_farm_shield(cb)

        text = farm.safe_edit_text.await_args.args[1]
        amounts = _rub_amounts(text)
        assert "12.00" not in amounts and "12" not in amounts, (
            f"на экране обещан недостающий остаток вместо полной цены: {amounts}"
        )
        assert "30" in amounts, f"полная цена плёнки не названа: {amounts}"

    @pytest.mark.asyncio
    async def test_screen_says_balance_is_not_charged(self, env):
        """Человек должен заранее знать, что его 18 ₽ останутся на месте."""
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(180)))

        await farm.callback_farm_shield(_callback())

        text = farm.safe_edit_text.await_args.args[1]
        assert "не списывается" in text

    @pytest.mark.asyncio
    async def test_card_invoice_equals_full_price(self, env, monkeypatch):
        """Счёт в Lava — ровно та сумма, что показана на экране."""
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(180)))
        create = AsyncMock(return_value="p-1")
        monkeypatch.setattr(farm.database, "create_pending_purchase", create)
        monkeypatch.setattr(
            farm.database, "update_pending_purchase_invoice_id", AsyncMock(),
        )
        lava = MagicMock()
        lava.is_enabled = MagicMock(return_value=True)
        lava.create_invoice = AsyncMock(
            return_value={"invoice_id": "i-1", "payment_url": "https://pay"}
        )
        monkeypatch.setitem(__import__("sys").modules, "lava_service", lava)

        await farm.callback_farm_shield_lava(_callback("farm_shield_lava:0"))

        assert create.await_args.kwargs["price_kopecks"] == OAK_SHIELD_KOPECKS


class TestInvoiceLeadTime:
    def test_threshold_is_sane(self):
        assert 5 <= farm.SHIELD_INVOICE_MIN_LEAD_MINUTES <= 120

    @pytest.mark.asyncio
    async def test_no_payment_screen_a_minute_before_the_storm(self, env):
        """Экран оплаты не показываем — платёж заведомо опоздает."""
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(1)))
        cb = _callback()

        await farm.callback_farm_shield(cb)

        farm.safe_edit_text.assert_not_awaited()
        cb.answer.assert_awaited_once()
        alert = cb.answer.await_args.args[0]
        assert str(farm.SHIELD_INVOICE_MIN_LEAD_MINUTES) in alert, (
            "человеку не сказали, сколько времени нужно на оплату"
        )
        assert cb.answer.await_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_card_invoice_blocked_too(self, env, monkeypatch):
        """Старый экран оплаты живёт в чате — счёт не должен создаться и с него."""
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(2)))
        create = AsyncMock(return_value="p-1")
        monkeypatch.setattr(farm.database, "create_pending_purchase", create)

        await farm.callback_farm_shield_lava(_callback("farm_shield_lava:0"))

        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sbp_invoice_blocked_too(self, env, monkeypatch):
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(2)))
        create = AsyncMock(return_value="p-1")
        monkeypatch.setattr(farm.database, "create_pending_purchase", create)

        await farm.callback_farm_shield_sbp(_callback("farm_shield_sbp:0"))

        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_balance_payment_still_works_at_the_last_minute(self, env, monkeypatch):
        """Списание с баланса мгновенное — запрещать его нельзя.

        Иначе вместо одной починки получаем другую поломку: человек с деньгами
        на балансе теряет грядку, хотя мог спасти её одним нажатием.
        """
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(1)))
        monkeypatch.setattr(
            farm, "_find_growing_plot",
            AsyncMock(return_value=([_plot()], 1, OAK_SHIELD_KOPECKS, _plot())),
        )
        apply_shield = AsyncMock(return_value=(True, "ok"))
        monkeypatch.setattr(farm.database, "apply_storm_shield_atomic", apply_shield)
        cb = _callback()

        await farm.callback_farm_shield(cb)

        apply_shield.assert_awaited_once()
        assert apply_shield.await_args.kwargs["deduct_balance"] is True


class TestFailureReasonNotLeaked:
    @pytest.mark.asyncio
    async def test_internal_reason_not_shown_to_user(self, env, monkeypatch):
        """Раньше в алерт уходило «Не удалось накрыть: plot_not_growing» —
        английский служебный код посреди русского интерфейса."""
        env.setattr(farm, "_get_imminent_storm", AsyncMock(return_value=_storm(180)))
        monkeypatch.setattr(
            farm, "_find_growing_plot",
            AsyncMock(return_value=([_plot()], 1, OAK_SHIELD_KOPECKS, _plot())),
        )
        monkeypatch.setattr(
            farm.database, "apply_storm_shield_atomic",
            AsyncMock(return_value=(False, "plot_not_growing")),
        )
        cb = _callback()

        await farm.callback_farm_shield(cb)

        assert "plot_not_growing" not in cb.answer.await_args.args[0]
