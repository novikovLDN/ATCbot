"""Subscriber / funnel definitions (database/metrics.py) on fixture rows."""
import pytest

from database import metrics as m


class TestSubscriptionKind:
    @pytest.mark.parametrize("source,bypass,kind", [
        ("payment", False, "paid"),
        ("gift", False, "gift"),
        ("trial", False, "trial"),
        ("admin", False, "granted"),
        ("referral", False, "granted"),
        (None, False, "granted"),
        ("payment", True, "bypass_only"),
        ("bypass_only", False, "bypass_only"),
    ])
    def test_kind(self, source, bypass, kind):
        assert m.subscription_kind(source, bypass) == kind


ACTIVE_ROWS = [
    {"source": "payment", "bypass_only": False, "sub_type": "basic", "auto_renew": True, "count": 40, "expiring": 5},
    {"source": "payment", "bypass_only": False, "sub_type": "plus", "auto_renew": False, "count": 20, "expiring": 3},
    {"source": "admin", "bypass_only": False, "sub_type": "plus", "auto_renew": False, "count": 7, "expiring": 1},
    {"source": "trial", "bypass_only": False, "sub_type": "basic", "auto_renew": False, "count": 30, "expiring": 30},
    {"source": "gift", "bypass_only": False, "sub_type": "basic", "auto_renew": False, "count": 3, "expiring": 0},
    {"source": "payment", "bypass_only": True, "sub_type": "basic", "auto_renew": False, "count": 12, "expiring": 0},
    {"source": "payment", "bypass_only": False, "sub_type": "biz_team", "auto_renew": False, "count": 1, "expiring": 0},
]


class TestActiveSummary:
    def test_counts(self):
        s = m.summarize_active(ACTIVE_ROWS)
        assert s["total"] == 113
        assert s["with_access"] == 101
        assert s["paid"] == 61
        assert s["by_kind"]["granted"] == 7
        assert s["by_tariff"] == {"basic": 73, "plus": 28}  # legacy biz_team counts as plus

    def test_old_active_paid_counted_admin_grants(self):
        """get_active_paid_subscriptions_count excluded only trials and
        bypass-only rows: admin grants and gifts inflated "paid"."""
        old = sum(r["count"] for r in ACTIVE_ROWS
                  if r["source"] != "trial" and not r["bypass_only"])
        assert old == 71
        assert m.summarize_active(ACTIVE_ROWS)["paid"] == 61

    def test_old_active_subs_counted_bypass_only(self):
        """/stats/overview `active_subs` = expires_at > now, bypass-only and
        trials included."""
        assert m.summarize_active(ACTIVE_ROWS)["total"] == 113

    def test_auto_renew_share_paid_only(self):
        s = m.summarize_active(ACTIVE_ROWS)
        assert s["auto_renew_paid"] == 40
        assert s["auto_renew_share"] == round(40 / 61 * 100, 1)

    def test_expiring_excludes_bypass_only(self):
        s = m.summarize_active(ACTIVE_ROWS)
        assert s["expiring_7d"]["total"] == 39
        assert s["expiring_7d"]["auto_renew_on"] == 5


class TestRenewals:
    def test_rate_excludes_open(self):
        s = m.summarize_renewals({"ending": 20, "renewed": 12, "churned": 4, "open": 4}, 3)
        assert s["renewal_rate"] == 75.0
        assert s["churn_rate"] == 25.0

    def test_nothing_decided(self):
        s = m.summarize_renewals({"ending": 2, "renewed": 0, "churned": 0, "open": 2}, 3)
        assert s["renewal_rate"] is None


class TestFunnel:
    def test_steps_and_unrecorded_tariff_view(self):
        steps = m.funnel_steps({"started": 200, "trial": 120, "invoiced": 50, "paid": 20})
        by_key = {s["key"]: s for s in steps}
        assert by_key["tariff_view"]["recorded"] is False
        assert by_key["tariff_view"]["users"] is None
        assert by_key["tariff_view"]["note"]
        assert by_key["invoiced"]["of_start"] == 25.0
        # of_prev skips the unrecorded step and the side-branch trial.
        assert by_key["invoiced"]["of_prev"] == 25.0
        assert by_key["paid"]["of_prev"] == 40.0
        assert by_key["paid"]["of_start"] == 10.0

    def test_empty(self):
        steps = m.funnel_steps({})
        assert steps[0]["users"] == 0
        assert steps[0]["of_start"] is None


class TestTariffFamily:
    def test_family(self):
        assert m.tariff_family("biz_pro") == "plus"
        assert m.tariff_family(None) == "basic"
        assert m.tariff_family("weird") == "other"
