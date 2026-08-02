"""Списки типов покупок и тарифов для CHECK-констрейнтов.

Дефект: при расхождении схемы create_pending_purchase пересоздавала CHECK
по списку, записанному прямо в коде запроса. В списке не было steam, proxy
и farm_effect, поэтому одно срабатывание «починки» навсегда делало эти
покупки невозможными — механизм восстановления ломал рабочие сценарии.
"""
import pytest

from database.subscriptions import (
    PURCHASE_TYPES,
    TARIFF_PREFIXES,
    TARIFF_VALUES,
    _PURCHASE_TYPES_SQL,
    _TARIFF_PREFIXES_SQL,
)

# Типы, фактически создаваемые кодом бота.
USED_IN_CODE = {
    "subscription", "balance_topup", "gift", "telegram_premium",
    "telegram_stars", "traffic_pack", "apple_id", "spotify",
    "steam", "proxy", "farm_effect",
}


@pytest.mark.parametrize("purchase_type", sorted(USED_IN_CODE))
def test_every_used_type_is_allowed(purchase_type):
    assert purchase_type in PURCHASE_TYPES, (
        f"{purchase_type} создаётся кодом, но отсутствует в CHECK — "
        "покупка станет невозможной после восстановления констрейнта"
    )


def test_previously_missing_types_present():
    """Ровно те три типа, из-за которых находка была P0."""
    for t in ("steam", "proxy", "farm_effect"):
        assert t in PURCHASE_TYPES


def test_no_duplicates():
    assert len(PURCHASE_TYPES) == len(set(PURCHASE_TYPES))
    assert len(TARIFF_VALUES) == len(set(TARIFF_VALUES))


@pytest.mark.parametrize("prefix", ["steam_", "spotify_", "apple_id_", "traffic_", "bypass_"])
def test_tariff_prefixes_cover_product_families(prefix):
    assert prefix in TARIFF_PREFIXES


def test_sql_fragments_are_quoted_correctly():
    assert "'steam'" in _PURCHASE_TYPES_SQL
    assert "'farm_effect'" in _PURCHASE_TYPES_SQL
    assert _PURCHASE_TYPES_SQL.count("'") == len(PURCHASE_TYPES) * 2
    assert "tariff LIKE 'steam_%'" in _TARIFF_PREFIXES_SQL
    assert " OR " in _TARIFF_PREFIXES_SQL
