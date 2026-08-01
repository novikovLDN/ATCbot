"""Маршрутизация оплаченных покупок.

Дефект, ради которого написаны эти тесты: у покупки Spotify не было
собственной ветки в process_successful_payment, поэтому оплата картой
через Telegram Payments проваливалась в финализацию VPN-подписки —
деньги списывались, Spotify не выдавался, админ заказа не видел.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def classify():
    """classify_purchase — чистая функция, окружение готовит conftest."""
    from app.handlers.payments.payments_messages import classify_purchase
    return classify_purchase


class TestClassifyPurchase:
    def test_spotify_by_purchase_type(self, classify):
        """Главный дефект: spotify не должен попадать в ветку подписки."""
        assert classify({"purchase_type": "spotify", "tariff": "spotify_ind_1m"}) == "spotify"

    def test_spotify_by_tariff_prefix(self, classify):
        """Тип может быть не проставлен — опознаём по префиксу тарифа."""
        assert classify({"purchase_type": "", "tariff": "spotify_fam_3m"}) == "spotify"

    def test_steam_by_purchase_type(self, classify):
        assert classify({"purchase_type": "steam"}) == "steam"

    def test_steam_by_tariff_prefix(self, classify):
        assert classify({"tariff": "steam_500"}) == "steam"

    def test_apple_id_by_tariff_prefix(self, classify):
        assert classify({"tariff": "apple_id_usa_50"}) == "apple_id"

    def test_telegram_premium(self, classify):
        assert classify({"purchase_type": "telegram_premium"}) == "telegram_premium"

    def test_telegram_stars(self, classify):
        assert classify({"purchase_type": "telegram_stars"}) == "telegram_stars"

    def test_traffic_pack(self, classify):
        assert classify({"purchase_type": "traffic_pack", "tariff": "bypass_15"}) == "traffic_pack"

    def test_gift(self, classify):
        assert classify({"purchase_type": "gift"}) == "gift"

    def test_proxy(self, classify):
        assert classify({"purchase_type": "proxy"}) == "proxy"

    def test_plain_subscription(self, classify):
        assert classify({"purchase_type": "subscription", "tariff": "plus"}) == "subscription"

    def test_empty_type_defaults_to_subscription(self, classify):
        assert classify({"purchase_type": "", "tariff": "basic"}) == "subscription"

    def test_none_defaults_to_subscription(self, classify):
        assert classify(None) == "subscription"

    def test_whitespace_type_is_trimmed(self, classify):
        assert classify({"purchase_type": "  spotify  "}) == "spotify"

    def test_vpn_tariff_never_misrouted(self, classify):
        """Тарифы VPN не должны случайно попасть в товарную ветку."""
        for tariff in ("basic", "plus", "biz_start", "bypass_only"):
            assert classify({"tariff": tariff}) == "subscription"
