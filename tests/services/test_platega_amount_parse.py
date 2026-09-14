"""Platega callback amount / currency parsing (platega_service._extract_amount,
_extract_currency).

Risk closed: the paid amount in a Platega callback is what the underpayment
check compares against the invoice. Platega sends `paymentDetails` either as
an object or as a string ("100 RUB"); the e2e and matrix fakes send only the
object form, so the string branch and the malformed cases were never run
(coverage 09: _extract_amount 8/15, _extract_currency 4/11 lines unrun).
The rule pinned here: an ambiguous amount always resolves LOW or to the
fallback — it may cause a rejected callback (alert, manual check), never an
over-credit.
"""
from __future__ import annotations

import pytest

import platega_service as ps


@pytest.mark.parametrize("details,fallback,expected", [
    ({"amount": 199, "currency": "RUB"}, 0.0, 199.0),
    ({"amount": "199.50"}, 0.0, 199.5),
    ({"amount": None}, 150.0, 150.0),        # object without amount → flat `amount` fallback
    ({"amount": 0}, 150.0, 150.0),
    ({"amount": "abc"}, 7.0, 7.0),
    (199, 0.0, 199.0),
    (99.9, 0.0, 99.9),
    ("199 RUB", 0.0, 199.0),
    ("100.5 RUB", 0.0, 100.5),
    ("RUB 250", 0.0, 250.0),
    ("", 0.0, 0.0),
    ("RUB", 0.0, 0.0),
    (None, 42.0, 42.0),
    (["199"], 0.0, 0.0),
    # thousands separator / decimal comma: resolves low → underpayment reject, never 1990
    ("1 990.00 RUB", 0.0, 1.0),
    ("1990,00 RUB", 0.0, 0.0),
])
def test_extract_amount(details, fallback, expected):
    assert ps._extract_amount(details, fallback=fallback) == pytest.approx(expected)


@pytest.mark.parametrize("body,expected", [
    ({"currency": "rub"}, "RUB"),
    ({"currency": " usd "}, "USD"),
    ({"paymentDetails": {"amount": 1, "currency": "eur"}}, "EUR"),
    ({"paymentDetails": "100 USD"}, "USD"),
    ({"paymentDetails": "100"}, None),
    ({"paymentDetails": {"amount": 1}}, None),
    ({}, None),
    ({"currency": "RUB", "paymentDetails": "100 USD"}, "RUB"),   # flat field wins (doc §4)
])
def test_extract_currency(body, expected):
    assert ps._extract_currency(body) == expected
