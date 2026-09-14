"""Revenue definitions — classification and Moscow day boundaries.

Pure functions only. These pin down the two corrections that make the
dashboard's money figures reconcile at all:

  * revenue is cash-in: a top-up counts (money arrived), a purchase paid
    from that balance does not (the same ruble, moving internally);
  * shop GMV (and proxy, game) is not VPN revenue (a 5000 ₽ Steam top-up in the same
    bucket as a 300 ₽ subscription makes the average order value
    uninterpretable).
"""
from datetime import datetime, timedelta, timezone

import pytest

from database import revenue as rev

MSK = timezone(timedelta(hours=3))


class TestRevenueClass:
    def test_subscription_and_gift_are_recurring(self):
        assert rev.revenue_class("subscription") == "subscription"
        assert rev.revenue_class("gift") == "subscription"

    def test_traffic_pack_is_its_own_class(self):
        assert rev.revenue_class("traffic_pack") == "traffic"

    @pytest.mark.parametrize(
        "ptype", ["telegram_premium", "telegram_stars", "steam", "apple_id", "spotify"]
    )
    def test_shop_is_separated(self, ptype):
        """Price is not margin on these — they must never land in the
        headline figure."""
        assert rev.revenue_class(ptype) == "shop"

    def test_proxy_and_game_are_their_own_lines(self):
        """Owner decision: proxy and game are separate lines — not VPN
        revenue, not shop, not 'other'."""
        assert rev.revenue_class("proxy") == "proxy"
        assert rev.revenue_class("farm_effect") == "game"
        for cls in rev.SEPARATE_CLASSES:
            assert cls not in rev.CASH_IN_CLASSES

    def test_balance_topup_is_its_own_class(self):
        """Real cash-in, but deferred: keeping it out of "subscription"
        stops wallet loading from looking like subscription growth."""
        assert rev.revenue_class("balance_topup") == "topup"

    def test_balance_funded_rows_are_excluded_by_provider(self):
        """Auto-renewals and balance purchases are filtered by provider,
        not by type — they are ordinary subscriptions that happen to be
        paid from money already counted."""
        assert rev.BALANCE_PROVIDER == "balance"

    def test_unknown_type_does_not_crash(self):
        assert rev.revenue_class("something_new_in_2027") == "other"

    def test_topups_never_counted_as_product_sales(self):
        """Guard against a future edit folding top-ups into subscriptions."""
        for ptype in rev.TOPUP_TYPES:
            assert rev.revenue_class(ptype) not in ("subscription", "traffic")

    def test_classes_are_disjoint(self):
        groups = [
            set(rev.SUBSCRIPTION_TYPES), set(rev.TRAFFIC_TYPES),
            set(rev.SHOP_TYPES), set(rev.PROXY_TYPES), set(rev.GAME_TYPES), set(rev.TOPUP_TYPES),
        ]
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                assert not (a & b), f"purchase type in two classes: {a & b}"


class TestMoscowDayStart:
    def test_returns_utc_instant(self):
        got = rev.msk_day_start(datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc))
        assert got.tzinfo == timezone.utc

    def test_midday_utc(self):
        # 12:00 UTC = 15:00 MSK on the 20th → MSK midnight is 20 Jul 00:00
        # MSK = 19 Jul 21:00 UTC.
        got = rev.msk_day_start(datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc))
        assert got == datetime(2026, 7, 19, 21, 0, tzinfo=timezone.utc)

    def test_late_utc_evening_is_already_tomorrow_in_moscow(self):
        """The bug this replaces: the server computed 'today' in UTC while
        the browser computed it in MSK, so the same label showed two
        different numbers for sales made between 21:00 and 24:00 UTC."""
        got = rev.msk_day_start(datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc))
        assert got == datetime(2026, 7, 20, 21, 0, tzinfo=timezone.utc)
        assert got.astimezone(MSK).day == 21
        assert got.astimezone(MSK).hour == 0

    def test_exactly_at_moscow_midnight(self):
        moment = datetime(2026, 7, 20, 21, 0, tzinfo=timezone.utc)  # 21 Jul 00:00 MSK
        assert rev.msk_day_start(moment) == moment

    def test_one_second_before_moscow_midnight(self):
        moment = datetime(2026, 7, 20, 20, 59, 59, tzinfo=timezone.utc)
        got = rev.msk_day_start(moment)
        assert got.astimezone(MSK).day == 20

    def test_days_ago_walks_back_whole_days(self):
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        today = rev.msk_day_start(now)
        assert rev.msk_day_start(now, days_ago=1) == today - timedelta(days=1)
        assert rev.msk_day_start(now, days_ago=7) == today - timedelta(days=7)

    def test_every_boundary_is_moscow_midnight(self):
        now = datetime(2026, 7, 20, 15, 30, tzinfo=timezone.utc)
        for ago in range(0, 40):
            msk = rev.msk_day_start(now, days_ago=ago).astimezone(MSK)
            assert (msk.hour, msk.minute, msk.second) == (0, 0, 0), ago

    def test_windows_are_contiguous_and_non_overlapping(self):
        """A 30-day window and its preceding 30-day window must abut
        exactly — otherwise the delta double-counts or skips a day."""
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        days = 30
        window_start = rev.msk_day_start(now, days_ago=days - 1)
        prev_start = rev.msk_day_start(now, days_ago=(days * 2) - 1)
        assert prev_start < window_start
        assert (window_start - prev_start).days == days


# ── Canonical definitions on fixture data (dashboard v3) ─────────────
#
# One Moscow window of real-shaped rows. The same rows are pushed through
# the definitions each screen used before v3 and through the canonical
# one, so the numbers quoted in docs/dashboard/metrics.md are pinned here.

from datetime import date  # noqa: E402

PAID_FACTS = [
    # purchase_type, provider, tariff, is_combo, count, kopecks (sum)
    {"purchase_type": "subscription", "provider": "platega", "tariff": "basic", "is_combo": False, "count": 3, "kopecks": 89_700},
    {"purchase_type": "subscription", "provider": "wata", "tariff": "plus", "is_combo": True, "count": 1, "kopecks": 59_900},
    {"purchase_type": "subscription", "provider": "telegram_payment", "tariff": "basic", "is_combo": True, "count": 1, "kopecks": 39_900},
    {"purchase_type": "gift", "provider": "telegram_stars", "tariff": "basic", "is_combo": False, "count": 1, "kopecks": 29_900},
    {"purchase_type": "traffic_pack", "provider": "platega", "tariff": "traffic_15gb", "is_combo": False, "count": 2, "kopecks": 39_800},
    {"purchase_type": "balance_topup", "provider": "platega", "tariff": None, "is_combo": False, "count": 1, "kopecks": 100_000},
    {"purchase_type": "steam", "provider": None, "tariff": None, "is_combo": False, "count": 1, "kopecks": 500_000},
    {"purchase_type": "spotify", "provider": None, "tariff": "spotify_1m", "is_combo": False, "count": 1, "kopecks": 30_000},
    {"purchase_type": "farm_effect", "provider": "cryptobot", "tariff": None, "is_combo": False, "count": 1, "kopecks": 9_900},
]
# Top-ups paid inside Telegram: only in balance_transactions.
NATIVE_TOPUPS = [
    {"purchase_type": "balance_topup", "provider": "telegram", "tariff": None, "is_combo": False, "count": 1, "kopecks": 50_000},
    {"purchase_type": "balance_topup", "provider": "telegram_stars", "tariff": None, "is_combo": False, "count": 1, "kopecks": 20_000},
]
# `payments` rows (legacy /stats/revenue source): every subscription/traffic
# payment above, every top-up (both paths), plus two purchases paid FROM the
# balance (a Basic bought with the wallet and an auto-renewal).
LEGACY_PAYMENTS_APPROVED_KOPECKS = (
    89_700 + 59_900 + 39_900 + 29_900 + 39_800  # products
    + 100_000 + 50_000 + 20_000                  # top-ups
    + 29_900 + 29_900                            # balance purchase + auto-renew
)


class TestCanonicalRevenueOnFixture:
    def test_old_raw_pending_sum(self):
        """/payments/revenue before v3: raw SUM over paid pending_purchases —
        shop GMV and game inside, Telegram top-ups missing."""
        assert sum(f["kopecks"] for f in PAID_FACTS) == 899_100

    def test_v2_kpi_missed_native_topups(self):
        v2 = rev.summarize_facts(PAID_FACTS)
        assert v2["net_kopecks"] == 359_200

    def test_canonical_net(self):
        s = rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)
        # subscriptions 219 400 + traffic 39 800 + top-ups 170 000
        assert s["net_kopecks"] == 429_200
        assert s["by_class"]["topup"] == {"count": 3, "kopecks": 170_000}

    def test_canonical_gross_includes_shop_and_game(self):
        s = rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)
        assert s["gross_kopecks"] == 969_100
        assert s["shop_kopecks"] == 530_000
        assert s["game_kopecks"] == 9_900
        assert s["proxy_kopecks"] == 0
        assert s["vpn_kopecks"] == s["net_kopecks"] == 429_200
        # Turnover = VPN + shop + proxy + game (+ other): the lines add up.
        assert s["gross_kopecks"] == (
            s["vpn_kopecks"] + s["shop_kopecks"] + s["proxy_kopecks"]
            + s["game_kopecks"] + s["other_kopecks"]
        )

    def test_legacy_payments_table_double_counts_wallet(self):
        """/stats/revenue and /stats/overview summed `payments`: the wallet
        spend (59 800) is counted on top of the top-up that funded it."""
        s = rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)
        assert LEGACY_PAYMENTS_APPROVED_KOPECKS - s["net_kopecks"] == 59_800

    def test_spotify_is_shop_not_other(self):
        """v2 left spotify out of every class — it landed in 'other'."""
        assert rev.revenue_class("spotify") == "shop"
        s = rev.summarize_facts(PAID_FACTS)
        assert "other" not in s["by_class"]

    def test_products(self):
        s = rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)
        p = s["by_product"]
        assert p["basic"] == {"count": 3, "kopecks": 89_700}
        assert p["combo_plus"] == {"count": 1, "kopecks": 59_900}
        assert p["combo_basic"] == {"count": 1, "kopecks": 39_900}
        assert p["gift"]["count"] == 1
        assert p["traffic_pack"]["kopecks"] == 39_800
        assert p["shop_steam"]["kopecks"] == 500_000

    def test_traffic_gb_sold(self):
        s = rev.summarize_facts(PAID_FACTS)
        assert s["traffic_gb_sold"] == 30
        assert s["traffic_packs_sold"] == 2

    def test_providers_normalised(self):
        s = rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)
        prov = s["by_provider"]
        # Telegram top-ups ("telegram") and Telegram subscriptions
        # ("telegram_payment") are one provider on the dashboard.
        assert prov["telegram_payment"]["kopecks"] == 39_900 + 50_000
        assert prov["unknown"]["kopecks"] == 530_000  # shop rows carry no provider
        assert s["stars"] == {"count": 2, "kopecks": 29_900 + 20_000}

    def test_balance_provider_rows_are_internal(self):
        s = rev.summarize_facts([
            {"purchase_type": "subscription", "provider": "balance", "tariff": "basic",
             "is_combo": False, "count": 1, "kopecks": 29_900},
        ])
        assert s["net_kopecks"] == 0
        assert s["balance_funded"] == {"count": 1, "kopecks": 29_900}

    def test_avg_check_is_net_only(self):
        s = rev.summarize_facts(PAID_FACTS + NATIVE_TOPUPS)
        assert s["avg_check_kopecks"] == 429_200 // s["net_count"]


class TestProductKey:
    @pytest.mark.parametrize("ptype,tariff,combo,expected", [
        ("subscription", "basic", False, "basic"),
        ("subscription", "plus", False, "plus"),
        ("subscription", "basic", True, "combo_basic"),
        ("subscription", "plus", True, "combo_plus"),
        ("subscription", "biz_team", False, "plus"),
        (None, None, False, "basic"),
        ("gift", "plus", False, "gift"),
        ("traffic_pack", "bypass_50gb", False, "traffic_pack"),
        ("balance_topup", None, False, "topup"),
        ("apple_id", "apple_id_us_10", False, "shop_apple_id"),
        ("proxy", None, False, "proxy"),
        ("farm_effect", None, False, "game"),
        ("mystery", None, False, "other"),
    ])
    def test_product_key(self, ptype, tariff, combo, expected):
        assert rev.product_key(ptype, tariff, combo) == expected

    @pytest.mark.parametrize("tariff,gb", [
        ("traffic_15gb", 15), ("bypass_5000gb", 5000), ("basic", None), (None, None),
    ])
    def test_pack_gb(self, tariff, gb):
        assert rev.pack_gb(tariff) == gb


class TestRatios:
    def test_delta(self):
        assert rev.delta_pct(150, 100) == 50.0
        assert rev.delta_pct(50, 0) is None
        assert rev.delta_pct(0, 100) == -100.0

    def test_success_rate_ignores_pending(self):
        assert rev.success_rate(8, 2) == 80.0
        assert rev.success_rate(0, 0) is None

    def test_per_user(self):
        assert rev.per_user(1000, 3) == 333
        assert rev.per_user(1000, 0) == 0


class TestSeriesBuckets:
    def test_week_starts_monday(self):
        assert rev.bucket_start(date(2026, 9, 13), "week") == date(2026, 9, 7)

    def test_month_buckets_walk_back(self):
        assert rev.bucket_starts(date(2026, 3, 15), "month", 3) == [
            date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1),
        ]

    def test_fill_series_emits_empty_buckets(self):
        starts = rev.bucket_starts(date(2026, 9, 13), "day", 3)
        out = rev.fill_series(
            [{"bucket": date(2026, 9, 12), "kopecks": 500, "count": 1},
             {"bucket": date(2026, 9, 12), "kopecks": 250, "count": 1}],
            starts,
        )
        assert [p["kopecks"] for p in out] == [0, 750, 0]
        assert out[-1]["date"] == "2026-09-13"

    def test_window_uses_moscow_midnight(self):
        now = datetime(2026, 9, 13, 22, 30, tzinfo=timezone.utc)  # 14 Sep 01:30 MSK
        w = rev.window(7, now)
        assert w["today"].astimezone(MSK).day == 14
        assert (w["until"] - w["since"]) < timedelta(days=7)
        # Like-for-like previous window: same length, same time of day,
        # shifted back exactly 7 days (was: 7 full days vs 6 days + part
        # of today, which biased every delta downwards during the day).
        assert w["prev_since"] == w["since"] - timedelta(days=7)
        assert w["prev_until"] == w["until"] - timedelta(days=7)
        assert (w["prev_until"] - w["prev_since"]) == (w["until"] - w["since"])

    def test_today_compares_with_yesterday_up_to_the_same_time(self):
        now = datetime(2026, 9, 13, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK
        w = rev.window(1, now)
        assert w["since"].astimezone(MSK).strftime("%d %H:%M") == "13 00:00"
        assert w["prev_since"].astimezone(MSK).strftime("%d %H:%M") == "12 00:00"
        assert w["prev_until"].astimezone(MSK).strftime("%d %H:%M") == "12 10:00"


class TestCohortMatrix:
    def test_cumulative_ltv_per_payer(self):
        jul, aug = date(2026, 7, 1), date(2026, 8, 1)
        rows = [
            {"cohort": jul, "offset": 0, "kopecks": 60_000},
            {"cohort": jul, "offset": 2, "kopecks": 30_000},
            {"cohort": aug, "offset": 0, "kopecks": 10_000},
        ]
        out = rev.cohort_matrix(rows, {jul: 2, aug: 1}, months=4, today=date(2026, 9, 13))
        assert out[0]["ltv_kopecks"] == [30_000, 30_000, 45_000, None]
        # August cohort: months 0 and 1 observable, 2+ are the future.
        assert out[1]["ltv_kopecks"] == [10_000, 10_000, None, None]


class TestTotalsQueries:
    """totals() must merge Telegram top-ups and pass naive-UTC params."""

    async def test_totals_merges_native_topups(self, monkeypatch):
        seen_params = []

        seen_sql = []

        class Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class Conn:
            def transaction(self, **kw):
                seen_sql.append(("tx", kw))
                return Tx()

            async def execute(self, sql, *args):
                seen_sql.append(("execute", sql))

            async def fetch(self, sql, *args):
                seen_params.extend(a for a in args if isinstance(a, datetime))
                if "balance_transactions" in sql:
                    return [{"provider": "telegram", "count": 1, "kopecks": 50_000}]
                return [PAID_FACTS[0]]

        class Acq:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *a):
                return False

        class Pool:
            def acquire(self):
                return Acq()

        async def fake_pool():
            return Pool()

        monkeypatch.setattr(rev, "get_pool", fake_pool)
        out = await rev.totals(datetime(2026, 9, 1, tzinfo=timezone.utc),
                               datetime(2026, 9, 2, tzinfo=timezone.utc))
        assert out["net_kopecks"] == 89_700 + 50_000
        assert all(p.tzinfo is None for p in seen_params)
        # Dashboard reads run read-only with a statement timeout (database/readonly.py).
        assert ("tx", {"readonly": True}) in seen_sql
        assert any(k == "execute" and "statement_timeout" in s for k, s in seen_sql)


class TestSeparateLines:
    def test_proxy_row_is_its_own_line(self):
        s = rev.summarize_facts([
            {"purchase_type": "proxy", "provider": "platega", "tariff": None,
             "is_combo": False, "count": 2, "kopecks": 20_000},
        ])
        assert s["proxy_kopecks"] == 20_000
        assert s["net_kopecks"] == 0
        assert s["shop_kopecks"] == 0
        assert s["gross_kopecks"] == 20_000
        assert "other" not in s["by_class"]
