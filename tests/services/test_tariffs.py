"""T1 — app.services.tariffs: pure tariff catalog (plan §A.1 + owner decisions G0).

GB expectations are hardcoded from the owner's rules (docs/audit/SCOPE.md);
prices are read from config so a price change never breaks these tests.
"""
import dataclasses

import pytest

import config
from app.services import tariffs
from app.services.tariffs import Entitlement, TariffConfigError

GIB = 1024 ** 3
MIB = 1024 ** 2


# ── config still matches the owner's rules ─────────────────────────────

def test_config_matches_owner_gb_rules():
    for key in ("basic", "plus"):
        assert set(config.TRAFFIC_LIMITS[key].values()) == {10 * GIB}
    expected_combo_gb = {30: 75, 90: 200, 180: 400, 365: 800, 730: 1500}
    for key in ("combo_basic", "combo_plus"):
        got = {p: row["gb"] for p, row in config.COMBO_TARIFFS[key].items()}
        assert got == expected_combo_gb
    assert config.TRIAL_BYPASS_MB == 500


# ── tariff_key / legacy biz normalization ──────────────────────────────

@pytest.mark.parametrize("tariff, is_combo, expected", [
    ("basic", False, "basic"),
    ("plus", False, "plus"),
    ("basic", True, "combo_basic"),
    ("plus", True, "combo_plus"),
    ("combo_basic", False, "combo_basic"),
    ("combo_plus", True, "combo_plus"),
    ("biz_team", False, "plus"),        # legacy biz_* is Plus
    ("biz_team", True, "combo_plus"),
])
def test_tariff_key(tariff, is_combo, expected):
    assert tariffs.tariff_key(tariff, is_combo) == expected


@pytest.mark.parametrize("tariff, is_combo", [
    ("gold", False),
    ("", False),
    ("gold", True),
])
def test_tariff_key_rejects_unknown(tariff, is_combo):
    with pytest.raises(TariffConfigError):
        tariffs.tariff_key(tariff, is_combo)


@pytest.mark.parametrize("tariff, is_combo, expected", [
    ("biz_starter", False, "plus"),
    ("biz_ultimate", False, "plus"),
    ("basic", False, "basic"),
    ("plus", True, "combo_plus"),
])
def test_tariff_key_maps_legacy_biz_to_plus(tariff, is_combo, expected):
    assert tariffs.tariff_key(tariff, is_combo) == expected


def test_legacy_biz_renewal_gets_plus_entitlement():
    ent = tariffs.for_purchase(tariffs.tariff_key("biz_team", False), 30)
    assert ent == Entitlement("plus", 30, "plus", 10 * GIB)


# ── for_purchase ───────────────────────────────────────────────────────

def test_basic_30():
    assert tariffs.for_purchase("basic", 30) == Entitlement("basic", 30, "basic", 10 * GIB)


def test_plus_365():
    assert tariffs.for_purchase("plus", 365) == Entitlement("plus", 365, "plus", 10 * GIB)


def test_combo_basic_30():
    ent = tariffs.for_purchase("combo_basic", 30)
    assert ent == Entitlement("combo_basic", 30, "basic", 75 * GIB)


def test_combo_plus_90():
    ent = tariffs.for_purchase("combo_plus", 90)
    assert ent == Entitlement("combo_plus", 90, "plus", 200 * GIB)


@pytest.mark.parametrize("key", ["combo_basic", "combo_plus"])
def test_combo_730(key):
    ent = tariffs.for_purchase(key, 730)
    assert ent.bypass_bytes == 1500 * GIB
    assert ent.premium_days == 730


def test_combo_has_no_extra_10gb():
    # combo 30d is exactly the table value, not table + TRAFFIC_LIMITS
    assert tariffs.for_purchase("combo_basic", 30).bypass_bytes == 75 * GIB


@pytest.mark.parametrize("key, period", [
    ("combo_basic", 45),
    ("combo_plus", 1),
    ("basic", 730),
    ("plus", 7),
])
def test_unknown_period_raises(key, period):
    with pytest.raises(TariffConfigError):
        tariffs.for_purchase(key, period)


@pytest.mark.parametrize("key", ["biz_starter", "biz_team", "biz_ultimate"])
def test_biz_is_not_a_catalog_key(key):
    """for_purchase takes catalog keys only; legacy biz is mapped by tariff_key()."""
    with pytest.raises(TariffConfigError):
        tariffs.for_purchase(key, 30)


@pytest.mark.parametrize("key", ["gold", "trial", "pack", "grant", ""])
def test_unknown_key_not_purchasable(key):
    with pytest.raises(TariffConfigError):
        tariffs.for_purchase(key, 30)


# ── packs ──────────────────────────────────────────────────────────────

def test_pack_15():
    assert tariffs.for_pack(15) == Entitlement("pack", 0, None, 15 * GIB)


def test_pack_5000_extended():
    ent = tariffs.for_pack(5000)
    assert ent.bypass_bytes == 5000 * GIB
    assert ent.premium_days == 0
    assert ent.premium_tier is None


@pytest.mark.parametrize("gb", sorted(set(config.TRAFFIC_PACKS) | set(config.TRAFFIC_PACKS_EXTENDED)))
def test_every_catalog_pack_size(gb):
    assert tariffs.for_pack(gb).bypass_bytes == gb * GIB


@pytest.mark.parametrize("gb", [0, -15, 16, 7])
def test_pack_unknown_size_raises(gb):
    with pytest.raises(TariffConfigError):
        tariffs.for_pack(gb)


@pytest.mark.parametrize("tariff, gb", [
    ("traffic_15gb", 15),
    ("bypass_15gb", 15),
    ("bypass_5000gb", 5000),
    ("traffic_200gb", 200),
])
def test_parse_pack_gb(tariff, gb):
    assert tariffs.parse_pack_gb(tariff) == gb
    assert tariffs.for_pack(tariffs.parse_pack_gb(tariff)).bypass_bytes == gb * GIB


@pytest.mark.parametrize("tariff", [
    "basic", "traffic_gb", "bypass_xgb", "traffic_15", "15gb", "bypass_-5gb", "", None,
])
def test_parse_pack_gb_rejects_garbage(tariff):
    with pytest.raises(TariffConfigError):
        tariffs.parse_pack_gb(tariff)


# ── trial / grant / bypass gift ────────────────────────────────────────

def test_trial():
    assert tariffs.for_trial() == Entitlement("trial", 3, "basic", 500 * MIB)


def test_trial_custom_days():
    assert tariffs.for_trial(days=5).premium_days == 5


@pytest.mark.parametrize("tariff, tier", [
    ("basic", "basic"),
    ("plus", "plus"),
    ("combo_plus", "plus"),
    ("biz_team", "plus"),
])
def test_grant_is_premium_only(tariff, tier):
    ent = tariffs.for_grant(tariff, 7)
    assert ent == Entitlement("grant", 7, tier, 0)


@pytest.mark.parametrize("tariff, days", [("gold", 7), ("basic", 0), ("basic", -1)])
def test_grant_invalid(tariff, days):
    with pytest.raises(TariffConfigError):
        tariffs.for_grant(tariff, days)


def test_bypass_gift():
    assert tariffs.for_bypass_gift(20) == Entitlement("bypass_gift", 0, None, 20 * GIB)


@pytest.mark.parametrize("gb", [0, -1])
def test_bypass_gift_invalid(gb):
    with pytest.raises(TariffConfigError):
        tariffs.for_bypass_gift(gb)


@pytest.mark.parametrize("bad", [True, 1.5, "10"])
def test_non_int_amounts_rejected(bad):
    with pytest.raises(TariffConfigError):
        tariffs.for_bypass_gift(bad)
    with pytest.raises(TariffConfigError):
        tariffs.for_purchase("basic", bad)


# ── renewal price ──────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["basic", "plus"])
def test_renewal_price_base(key):
    for period, row in config.TARIFFS[key].items():
        assert tariffs.renewal_price_rub(key, period) == row["price"]


@pytest.mark.parametrize("key", ["combo_basic", "combo_plus"])
def test_renewal_price_combo(key):
    for period, row in config.COMBO_TARIFFS[key].items():
        assert tariffs.renewal_price_rub(key, period) == row["price"]


def test_renewal_price_combo_differs_from_basic():
    assert tariffs.renewal_price_rub("combo_basic", 30) == config.COMBO_TARIFFS["combo_basic"][30]["price"]
    assert tariffs.renewal_price_rub("combo_basic", 30) != config.TARIFFS["basic"][30]["price"]


def test_renewal_price_legacy_biz_via_mapping():
    key = tariffs.tariff_key("biz_pro", False)
    assert tariffs.renewal_price_rub(key, 30) == config.TARIFFS["plus"][30]["price"]


@pytest.mark.parametrize("key, period", [
    ("biz_pro", 30), ("basic", 730), ("combo_basic", 45), ("gold", 30),
])
def test_renewal_price_invalid(key, period):
    with pytest.raises(TariffConfigError):
        tariffs.renewal_price_rub(key, period)


# ── Entitlement is immutable ───────────────────────────────────────────

def test_entitlement_frozen():
    ent = tariffs.for_pack(15)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ent.bypass_bytes = 1  # type: ignore[misc]


# ── normalize_tier / normalize_payment_tariff (owner 2026-09-14) ───────

@pytest.mark.parametrize("value, expected", [
    ("biz_starter", "plus"), ("biz_ultimate", "plus"), (" BIZ_team ", "plus"),
    ("basic", "basic"), ("plus", "plus"), ("trial", "trial"), (None, None), ("", ""),
])
def test_normalize_tier(value, expected):
    assert tariffs.normalize_tier(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("biz_team_30", "plus_30"), ("biz_pro_730", "plus_730"), ("biz_team", "plus"),
    ("basic_30", "basic_30"), ("plus_90", "plus_90"), ("3", "3"), (None, None),
])
def test_normalize_payment_tariff(value, expected):
    assert tariffs.normalize_payment_tariff(value) == expected


def test_config_has_no_business_tariffs():
    assert config.VALID_SUBSCRIPTION_TYPES == ("basic", "plus")
    assert not [k for k in config.TARIFFS if k.startswith("biz_")]
    assert not [k for k in config.TARIFFS_STARS if k.startswith("biz_")]
    for name in ("BIZ_TARIFFS", "BIZ_COUNTRIES", "BIZ_TIER_SPECS", "is_biz_tariff", "get_biz_price"):
        assert not hasattr(config, name)
