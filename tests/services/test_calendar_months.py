"""Owner decision 2026-09-14 (docs/audit/SCOPE.md «Длительность тарифов»):
paid tariff periods of 1/3/6/12/24 months (keys 30/90/180/365/730 days)
extend premium by CALENDAR months from the end of the current subscription
(or from now), clamped to the last day of a shorter month. Day grants stay days.

The one rule lives in app.services.tariffs (months_for_period / extend_expiry).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import tariffs

UTC = timezone.utc


def dt(y, m, d, hh=12, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


@pytest.mark.parametrize("period_days,months", [(30, 1), (90, 3), (180, 6), (365, 12), (730, 24)])
def test_catalog_periods_are_months(period_days, months):
    assert tariffs.months_for_period(period_days) == months


@pytest.mark.parametrize("period_days", [1, 3, 7, 14, 29, 31, 60, 360, 0, -30, None])
def test_other_periods_are_not_months(period_days):
    assert tariffs.months_for_period(period_days) is None


def test_owner_example_oct20_plus_one_month_is_nov20():
    assert tariffs.extend_expiry(dt(2026, 10, 20, 15, 30, 7), 30) == dt(2026, 11, 20, 15, 30, 7)


@pytest.mark.parametrize("base,period,expected", [
    (dt(2027, 1, 31), 30, dt(2027, 2, 28)),      # Jan 31 + 1 month → Feb 28
    (dt(2028, 1, 31), 30, dt(2028, 2, 29)),      # leap year → Feb 29
    (dt(2026, 8, 31), 180, dt(2027, 2, 28)),     # Aug 31 + 6 months → Feb 28
    (dt(2027, 8, 31), 180, dt(2028, 2, 29)),     # … → Feb 29 in a leap year
    (dt(2026, 3, 31), 30, dt(2026, 4, 30)),      # 31 → 30
    (dt(2026, 3, 31), 90, dt(2026, 6, 30)),
    (dt(2026, 11, 30), 90, dt(2027, 2, 28)),     # year boundary + clamp
    (dt(2026, 12, 15), 30, dt(2027, 1, 15)),     # year boundary
    (dt(2028, 2, 29), 365, dt(2029, 2, 28)),     # Feb 29 + 12 months
    (dt(2028, 2, 29), 730, dt(2030, 2, 28)),     # + 24 months
    (dt(2026, 9, 14), 365, dt(2027, 9, 14)),
    (dt(2026, 9, 14), 730, dt(2028, 9, 14)),
    (dt(2026, 1, 30), 30, dt(2026, 2, 28)),
])
def test_calendar_months_clamp_to_the_end_of_month(base, period, expected):
    assert tariffs.extend_expiry(base, period) == expected


def test_time_of_day_and_microseconds_are_kept():
    base = datetime(2026, 5, 31, 23, 59, 59, 999999, tzinfo=UTC)
    assert tariffs.extend_expiry(base, 30) == datetime(2026, 6, 30, 23, 59, 59, 999999, tzinfo=UTC)


def test_non_catalog_period_stays_days():
    base = dt(2026, 10, 20)
    assert tariffs.extend_expiry(base, 7) == base + timedelta(days=7)
    assert tariffs.extend_expiry(base, 360) == base + timedelta(days=360)


def test_other_timezone_is_normalized_to_utc():
    msk = timezone(timedelta(hours=3))
    base = datetime(2026, 10, 20, 2, 0, tzinfo=msk)          # = Oct 19 23:00 UTC
    out = tariffs.extend_expiry(base, 30)
    assert out.tzinfo == UTC and out == dt(2026, 11, 19, 23, 0)


def test_naive_datetime_is_refused():
    with pytest.raises(tariffs.TariffConfigError):
        tariffs.extend_expiry(datetime(2026, 10, 20, 12, 0), 30)


@pytest.mark.parametrize("period", [30, 90, 180, 365, 730, 7])
def test_premium_is_never_shortened(period):
    base = dt(2026, 1, 31)
    assert tariffs.extend_expiry(base, period) > base


@pytest.mark.parametrize("bad", [0, -30, None, "30", True])
def test_bad_period_is_refused(bad):
    with pytest.raises(tariffs.TariffConfigError):
        tariffs.extend_expiry(dt(2026, 1, 1), bad)
