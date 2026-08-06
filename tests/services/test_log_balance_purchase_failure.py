"""Отказ списания с внутреннего баланса оставляет след.

ЧТО БЫЛО СЛОМАНО

    `app/handlers/callbacks/pay_balance.py` — единственный платёжный экран,
    где деньги списываются прямо в боте, а не у внешнего провайдера. При
    неуспехе `finalize_balance_purchase` ветка заканчивалась молча:

        if not result or not result.get("success"):
            await callback.message.answer(error_text)
            await state.set_state(None)
            return

    Человек видел «ошибка обработки платежа», а в логах покупки не
    существовало вовсе — записи о старте на этом пути тоже не было, а запись
    об успехе не достигалась. Обращение «списали деньги, подписку не дали»
    разбирать было не по чему: неизвестно, дошёл ли человек до списания,
    какой тариф выбирал, сколько стоило и что вернула база. «Баланса не
    хватило в момент транзакции», «grant_access упал» и «человек вообще не
    нажимал кнопку» — неразличимы.

    У покупки с баланса нет строки в `pending_purchases`, поэтому восстановить
    состав заказа постфактум неоткуда: FSM к этому моменту очищается.

ПОЧЕМУ ЗАПИСЬ НЕ УТВЕРЖДАЕТ «ДЕНЬГИ НЕ СПИСАНЫ»

    `finalize_balance_purchase` на большинстве отказов БРОСАЕТ исключение, а
    в эту ветку попадают случаи, когда она вернула None или словарь без
    success. Утверждать исход списания отсюда нельзя, и запись обязана этого
    не делать — иначе разбор пойдёт по ложному следу.
"""
import ast
import inspect
import logging
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.callbacks import pay_balance as pb
from app.handlers.common.states import PurchaseState


FSM_DATA = {
    "tariff_type": "plus",
    "period_days": 30,
    "final_price_kopecks": 29900,
    "country": None,
}


def _state(current=PurchaseState.choose_payment_method):
    state = MagicMock()
    # get_state вызывается дважды: до чтения FSM и в проверке идемпотентности.
    state.get_state = AsyncMock(return_value=current)
    state.get_data = AsyncMock(return_value=dict(FSM_DATA))
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    return state


def _callback():
    cb = MagicMock()
    cb.from_user.id = 777
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.bot = MagicMock()
    return cb


@pytest.fixture
def paying_user(monkeypatch):
    """Баланса хватает, промо-сессии нет, всё остальное замокано."""
    monkeypatch.setattr(pb, "check_rate_limit", lambda *a, **k: (True, None))
    monkeypatch.setattr(pb, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pb, "get_promo_session", AsyncMock(return_value=None))
    monkeypatch.setattr(pb.database, "get_user_balance", AsyncMock(return_value=500.0))
    monkeypatch.setattr(pb.database, "get_subscription", AsyncMock(return_value=None))


async def _run(monkeypatch, finalize):
    monkeypatch.setattr(pb.database, "finalize_balance_purchase", finalize)
    await pb.callback_pay_balance(_callback(), _state())


@pytest.mark.asyncio
async def test_debit_failure_is_logged(monkeypatch, paying_user, caplog):
    with caplog.at_level(logging.INFO):
        await _run(monkeypatch, AsyncMock(return_value=None))

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "отказ списания с баланса не оставил ни одной записи"
    msg = errors[0]
    assert "BALANCE_PURCHASE_FAILED" in msg
    assert "777" in msg, "нет telegram_id"
    assert "plus" in msg, "нет тарифа — состав заказа восстановить неоткуда"
    assert "30" in msg, "нет срока"
    assert "299.00" in msg, "нет суммы в рублях"


@pytest.mark.asyncio
async def test_failure_record_does_not_claim_the_money_is_safe(
    monkeypatch, paying_user, caplog,
):
    """Исход списания отсюда неизвестен. Запись, утверждающая обратное,
    увела бы разбор от сверки баланса — то есть от единственного действия,
    которое здесь имеет смысл."""
    with caplog.at_level(logging.INFO):
        await _run(monkeypatch, AsyncMock(return_value={"success": False}))

    msg = next(r.getMessage() for r in caplog.records if "BALANCE_PURCHASE_FAILED" in r.getMessage())
    assert "исход списания неизвестен" in msg
    assert "сверьте баланс" in msg


@pytest.mark.asyncio
async def test_start_is_logged_before_the_money_moves(monkeypatch, paying_user, caplog):
    """Единственная запись до списания. У покупки с баланса нет строки в
    pending_purchases: упади процесс внутри транзакции — это будет
    единственный след, что человек до списания дошёл."""
    with caplog.at_level(logging.INFO):
        await _run(monkeypatch, AsyncMock(return_value=None))

    started = [r.getMessage() for r in caplog.records if "BALANCE_PURCHASE_START" in r.getMessage()]
    assert started, "старт списания с баланса не записан"
    assert "balance_rub=500.00" in started[0], (
        "без баланса на момент нажатия нельзя отличить «не хватило» от «упало»"
    )


@pytest.mark.asyncio
async def test_crash_inside_finalize_carries_the_order(monkeypatch, paying_user, caplog):
    """Основной путь отказа — исключение из finalize_balance_purchase. Запись
    стояла без единого идентификатора: трейсбэк был, а кто, что и почём
    покупал, приходилось угадывать."""
    with caplog.at_level(logging.INFO):
        await _run(monkeypatch, AsyncMock(side_effect=ValueError("insufficient funds")))

    crashed = [r.getMessage() for r in caplog.records if "BALANCE_PURCHASE_CRASHED" in r.getMessage()]
    assert crashed, "падение списания записано без маркера и контекста"
    assert "777" in crashed[0] and "plus" in crashed[0] and "299.00" in crashed[0]


def _silent_handlers(func):
    """except-блоки без записи в лог и без проброса — по дереву, а не по тексту."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        speaks = any(
            isinstance(inner, ast.Raise)
            or (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in {
                    "debug", "info", "warning", "error", "exception", "critical",
                }
            )
            for inner in ast.walk(node)
        )
        if not speaks:
            yield node


def test_only_the_stats_handler_stays_silent():
    """`except Exception: pass` вокруг синхронизации с сайтом съедал расхождение
    «в боте подписка есть, на сайте нет» без единого следа откуда.

    Молчаливым здесь остаётся ровно один перехват — вокруг записи статистики
    автоуведомлений: она идёт ПОСЛЕ выдачи подписки и отправки сообщения, и
    падение в ней ничего о покупке не сообщает. Всё остальное на денежном
    пути обязано говорить.
    """
    silent = list(_silent_handlers(pb.callback_pay_balance))
    assert len(silent) <= 1, (
        f"молчаливых перехватов на пути оплаты: {len(silent)} — "
        f"ошибка на денежном пути снова исчезает бесследно"
    )
    src = inspect.getsource(pb.callback_pay_balance)
    assert "SITE_SYNC_SCHEDULE_FAILED" in src, (
        "срыв синхронизации с сайтом снова гасится молча"
    )
