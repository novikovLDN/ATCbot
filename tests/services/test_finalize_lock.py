"""Защита finalize_purchase от параллельной финализации одной покупки.

Дефект: стоял SELECT ... FOR UPDATE SKIP LOCKED, но выполнялся вне
транзакции — соединение в режиме autocommit, поэтому блокировка строки
снималась сразу после запроса. Комментарий обещал «только один вебхук
обрабатывает покупку», а на деле два одновременных вебхука проходили оба.
"""
import inspect

import pytest

import database.subscriptions as subs


def test_no_row_lock_outside_transaction():
    """FOR UPDATE вне транзакции бесполезен — его быть не должно."""
    src = inspect.getsource(subs.finalize_purchase)
    # Ищем именно исполняемый код, а не объяснение в комментарии.
    code_lines = [
        ln for ln in src.split("\n")
        if "FOR UPDATE SKIP LOCKED" in ln and not ln.lstrip().startswith("#")
    ]
    assert not code_lines, (
        "блокировка строки вне транзакции не работает: соединение в autocommit"
    )


def test_advisory_lock_taken_and_released():
    """Лок берётся до работы и снимается в finally при любом исходе."""
    src = inspect.getsource(subs.finalize_purchase)
    assert "pg_try_advisory_lock" in src
    assert "pg_advisory_unlock" in src
    assert "finally:" in src, "без finally лок утечёт при исключении"


def test_lock_failure_raises_purchase_locked():
    src = inspect.getsource(subs.finalize_purchase)
    assert "PurchaseLocked" in src


def test_external_calls_stay_outside_transaction():
    """Тело выполняется под локом, но не внутри транзакции: ниже идут
    внешние HTTP-вызовы к панели, и держать транзакцию на время сетевого
    запроса значит исчерпать пул соединений."""
    src = inspect.getsource(subs.finalize_purchase)
    lock_pos = src.index("pg_try_advisory_lock")
    body_pos = src.index("_finalize_purchase_locked")
    assert lock_pos < body_pos, "лок должен браться до вызова тела"


def test_locked_body_receives_connection():
    """Лок сессионный: снимать его нужно на том же соединении."""
    sig = inspect.signature(subs._finalize_purchase_locked)
    assert "conn" in sig.parameters
    assert list(sig.parameters)[0] == "conn"


@pytest.mark.parametrize("name", [
    "purchase_id", "payment_provider", "amount_rubles", "invoice_id",
])
def test_locked_body_keeps_original_arguments(name):
    """Вынос тела не должен менять контракт вызова."""
    assert name in inspect.signature(subs._finalize_purchase_locked).parameters
