"""Типы покупок и допуск на сумму: по одному списку и одному порогу.

ПОЧЕМУ ЭТО ВАЖНО

    Покупка в боте — не только подписка. Steam, Spotify, Apple ID, прокси,
    пакет ГБ, подарок, эффект на ферме, пополнение баланса создаются с
    period_days = 0, и раньше это использовали как признак: «нет срока —
    значит пополнение баланса», со списком исключений.

    Список жил в трёх местах, и в решающем — в finalize_purchase — не
    было steam и proxy. Оплата Steam на пять тысяч попала бы человеку на
    внутренний баланс, а Steam бы не пополнился: товар при этом выглядел
    оплаченным. Не срабатывало это лишь потому, что оба типа
    перехватывались раньше по дороге, в другом модуле. Любой новый путь
    оплаты, зовущий finalize_purchase напрямую, снял бы эту случайную
    защиту.

    Та же история с допуском на расхождение суммы: ±1 ₽ в сервисном слое,
    0.5% в слое БД. На покупке в 199 ₽ платёж с расхождением ровно в
    рубль проходил первую проверку и падал на второй.
"""
import inspect
from pathlib import Path

import pytest

import config


def _finalize_source() -> str:
    """Тело finalize_purchase.

    Сама finalize_purchase — тонкая обёртка, берущая advisory-лок; вся
    работа идёт в _finalize_purchase_locked, чтобы лок снимался в finally
    при любом исходе.
    """
    from database import subscriptions

    return inspect.getsource(subscriptions._finalize_purchase_locked)


# ── Типы покупок ──────────────────────────────────────────────────────

def test_balance_topup_is_decided_by_type_only():
    """Никакого угадывания по period_days."""
    src = _finalize_source()
    line = next(ln for ln in src.split("\n") if "is_balance_topup =" in ln)
    assert "period_days" not in line, f"вернулась эвристика по сроку: {line.strip()}"
    assert '"balance_topup"' in line


@pytest.mark.parametrize("purchase_type", ["steam", "proxy", "spotify"])
def test_goods_are_not_mistaken_for_a_topup(purchase_type):
    """Товар, оплаченный деньгами, не должен стать пополнением баланса."""
    assert purchase_type in config.NON_SUBSCRIPTION_PURCHASE_TYPES
    assert not config.is_subscription_purchase(purchase_type)


def test_unknown_type_is_treated_as_a_subscription():
    """Так новая покупка попадёт в штатный путь выдачи и упадёт заметно,
    а не зачислится молча на баланс."""
    assert config.is_subscription_purchase("something_new_2027") is True
    assert config.is_subscription_purchase("") is True
    assert config.is_subscription_purchase(None) is True


def test_finalize_refuses_types_it_cannot_deliver():
    """Иначе покупка молча дойдёт до конца, не выдав ничего."""
    src = _finalize_source()
    assert "UNSUPPORTED_PURCHASE_TYPE" in src
    assert "NON_SUBSCRIPTION_PURCHASE_TYPES" in src


def test_confirmation_list_stays_a_subset_of_the_shared_one():
    """Три копии списка разошлись однажды — пусть не разойдутся снова."""
    from app.services.payments.confirmation import MARK_PAID_ONLY_TYPES

    extra = set(MARK_PAID_ONLY_TYPES) - set(config.NON_SUBSCRIPTION_PURCHASE_TYPES)
    assert not extra, f"типы есть в confirmation, но не в общем списке: {sorted(extra)}"


def test_every_created_purchase_type_is_known():
    """Тип, который где-то создаётся, обязан быть в общем списке или быть
    подпиской. Иначе он снова окажется в серой зоне."""
    import re

    created = set()
    for path in list(Path("app").rglob("*.py")) + list(Path("database").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        created |= set(re.findall(r"""purchase_type=["']([a-z_]+)["']""", text))

    unknown = created - set(config.NON_SUBSCRIPTION_PURCHASE_TYPES) - {"subscription"}
    assert not unknown, (
        f"типы создаются, но нигде не описаны: {sorted(unknown)} — "
        f"добавьте их в config.NON_SUBSCRIPTION_PURCHASE_TYPES или убедитесь, "
        f"что это подписка"
    )


# ── Допуск на расхождение суммы ───────────────────────────────────────

@pytest.mark.parametrize("expected,tolerance", [
    (149.0, 0.745),    # 0.5%
    (1199.0, 5.995),   # 0.5%
    (50.0, 0.50),      # нижняя граница
    (10.0, 0.50),      # нижняя граница
])
def test_tolerance_is_percentage_with_a_floor(expected, tolerance):
    assert config.payment_amount_tolerance(expected) == pytest.approx(tolerance)


@pytest.mark.asyncio
async def test_both_layers_use_the_same_threshold():
    """Зона, где платёж проходит одну проверку и падает на второй,
    существовать не должна."""
    from app.services.payments.service import validate_payment_amount
    from app.services.payments.exceptions import PaymentAmountMismatchError

    expected = 199.0
    tol = config.payment_amount_tolerance(expected)

    # Внутри допуска — проходит. Берём с запасом в копейку: сравнивать
    # float ровно по границе бессмысленно, 199.0 + 0.995 не даёт 199.995.
    assert await validate_payment_amount(expected + tol - 0.01, expected) is True
    # За границей — отвергается.
    with pytest.raises(PaymentAmountMismatchError):
        await validate_payment_amount(expected + tol + 0.01, expected)

    # Тот же порог в слое БД.
    src = _finalize_source()
    assert "config.payment_amount_tolerance(expected_amount_rubles)" in src, (
        "слой БД снова считает допуск по своей формуле"
    )


@pytest.mark.asyncio
async def test_explicit_tolerance_still_wins():
    """Вызывающий может задать свой допуск — это не сломано."""
    from app.services.payments.service import validate_payment_amount
    from app.services.payments.exceptions import PaymentAmountMismatchError

    assert await validate_payment_amount(1005.0, 1000.0, tolerance=10.0) is True
    with pytest.raises(PaymentAmountMismatchError):
        await validate_payment_amount(1005.0, 1000.0, tolerance=1.0)
