"""P2: a recurring scheduled broadcast fired once per worker tick for every
missed slot. mark_ran_and_reschedule computed the next run from the OLD
scheduled_at, so after N days of downtime (or a schedule created with a start
date in the past) a daily broadcast went out N times, one per minute.
The next run is now the first slot strictly after now.
"""
from datetime import datetime, timedelta, timezone

from database.scheduled_broadcasts import _next_run_after

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)   # a Monday


def test_daily_after_downtime_skips_missed_slots():
    nxt = _next_run_after(NOW - timedelta(days=5, hours=2), "daily", now=NOW)
    assert NOW < nxt <= NOW + timedelta(days=1)
    assert nxt.time() == (NOW - timedelta(hours=2)).time(), "keeps the time of day"


def test_weekly_after_downtime_skips_missed_slots():
    nxt = _next_run_after(NOW - timedelta(days=20), "weekly", now=NOW)
    assert NOW < nxt <= NOW + timedelta(days=7)


def test_weekdays_never_lands_on_weekend():
    nxt = _next_run_after(NOW - timedelta(days=9), "weekdays", now=NOW)
    assert nxt > NOW and nxt.weekday() < 5


def test_on_time_run_unchanged():
    assert _next_run_after(NOW, "daily", now=NOW) == NOW + timedelta(days=1)
    assert _next_run_after(NOW, "once", now=NOW) is None
