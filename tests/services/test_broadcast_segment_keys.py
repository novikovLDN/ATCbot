"""Broadcast segment keys (database/segments.py): the `<base>:<window>` grammar,
bounds, labels and the catalog. One parser for every consumer."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import tests.conftest  # noqa: F401  (env before config)
from database import segments as seg

UTC = timezone.utc


@pytest.mark.parametrize("raw,n,unit", [
    ("1d", 1, "d"), ("30d", 30, "d"), ("3650d", 3650, "d"),
    ("1m", 1, "m"), ("6m", 6, "m"), ("120m", 120, "m"),
    ("any", None, None),
])
def test_valid_windows(raw, n, unit):
    w = seg.parse_window(raw)
    assert (w.n, w.unit) == (n, unit) and w.key() == raw


@pytest.mark.parametrize("raw", [
    "", "0d", "0m", "3651d", "121m", "07d", "-1d", "1.5d", "1w", "1y", "d", "30", "ANY",
    " 30d", "30d ", "30 d", "1e3d", "99999d", "30D",
])
def test_invalid_windows(raw):
    with pytest.raises(seg.SegmentKeyError):
        seg.parse_window(raw)


def test_parse_keys():
    assert seg.parse_segment_key("paid_lapsed_any") == ("paid_lapsed_any", None)
    assert seg.parse_segment_key("bypass_only_now") == ("bypass_only_now", None)
    base, w = seg.parse_segment_key("paid_ended:6m")
    assert base == "paid_ended" and (w.n, w.unit) == (6, "m")
    assert seg.parse_segment_key("trial_ended:any")[1].is_any


@pytest.mark.parametrize("key", [
    "", "nope", "paid_ended", "paid_ended:", "paid_ended:0d", "nope:30d", "paid_lapsed_any:30d",
    "paid_expiring:any", "paid_expiring_manual:any", "inactive:any", "paid_ended:30d:1",
    None, 42,
])
def test_invalid_keys_raise_value_error(key):
    with pytest.raises(ValueError):
        seg.validate_segment_key(key)


def test_days_and_calendar_months_like_postgres():
    now = datetime(2026, 3, 31, 12, 0, tzinfo=UTC)
    assert seg.parse_window("30d").shift(now, -1) == datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    # 31 March − 1 month = 28 February (Postgres clamps to the month's end)
    assert seg.parse_window("1m").shift(now, -1) == datetime(2026, 2, 28, 12, 0, tzinfo=UTC)
    assert seg.parse_window("12m").shift(now, -1) == datetime(2025, 3, 31, 12, 0, tzinfo=UTC)
    assert seg.parse_window("13m").shift(now, -1) == datetime(2025, 2, 28, 12, 0, tzinfo=UTC)
    assert seg.parse_window("1m").shift(datetime(2024, 1, 31, tzinfo=UTC), +1) == datetime(2024, 2, 29, tzinfo=UTC)
    assert seg.parse_window("7d").shift(now, +1) == datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    assert seg.parse_window("any").shift(now, -1).year == 1900


@pytest.mark.parametrize("key,label", [
    ("paid_ended:6m", "Платная истекла, не продлил за последние 6 месяцев"),
    ("paid_ended:any", "Платная истекла, не продлил за всё время"),
    ("paid_ended:1d", "Платная истекла, не продлил за последний день"),
    ("trial_ended:1m", "Пробный закончился, не купил за последний месяц"),
    ("cold_start:21d", "Нажал /start и ничего за последние 21 день"),
    ("bought_sub:3d", "Купил подписку за последние 3 дня"),
    ("any_ended:12m", "Любая подписка истекла за последние 12 месяцев"),
    ("paid_expiring:7d", "Платная заканчивается в ближайшие 7 дней"),
    ("paid_expiring_manual:2m", "Платная заканчивается, автопродление выключено в ближайшие 2 месяца"),
    ("inactive:30d", "Не заходил в бот 30 дней и дольше"),
    ("paid_lapsed_any", "Платная — когда-либо платил, сейчас не активен"),
    ("gift_active", "Подарок — сейчас активен"),
    ("something_old", "something_old"),
    ("paid_ended:0d", "paid_ended:0d"),
])
def test_labels(key, label):
    assert seg.segment_label(key) == label


def test_catalog_shape_and_every_key_known():
    items = seg.catalog()
    keys = [i["key"] for i in items]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(seg.FIXED_KEYS) | set(seg.PARAMETRIC)
    for i in items:
        assert i["label"] and i["description"] and i["group"]
        if i["parametric"]:
            seg.validate_segment_key(f"{i['key']}:{i['default_window']}")
            assert i["units"] == ["d", "m"] and i["direction"] in ("past", "future", "idle")
    # parametric bases come first inside their group
    by_group: dict = {}
    for i in items:
        by_group.setdefault(i["group"], []).append(i["parametric"])
    for flags in by_group.values():
        assert flags == sorted(flags, reverse=True)
    # a parametric key never collides with a fixed key
    assert not set(seg.PARAMETRIC) & set(seg.FIXED_KEYS)


def test_every_new_fixed_key_is_in_the_catalog():
    assert set(seg.NEW_FIXED_SQL) <= seg.FIXED_KEYS


def test_resolver_has_a_branch_for_every_legacy_fixed_key():
    """A catalog key the resolver does not know would silently give []."""
    import inspect

    from database import admin
    src = inspect.getsource(admin._segment_user_ids) + repr(admin._FUNNEL_OLD_BASE_SEGMENTS)
    for key in seg.FIXED_KEYS - set(seg.NEW_FIXED_SQL):
        assert f'"{key}"' in src or f"'{key}'" in src, key


def test_sql_takes_parameters_not_formatted_windows():
    for base, sql in seg._PARAM_SQL.items():
        assert "$1" in sql, base
        assert "INTERVAL '" not in sql.replace("INTERVAL '3 days'", ""), base
