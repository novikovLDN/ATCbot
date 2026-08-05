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


class TestUnhandledRouteGuard:
    """Предохранитель против «забыли обработчик».

    Список _ROUTED_PURCHASE_TYPES и ветки обработки — две разные вещи, и они
    разъезжаются: тип добавляют в список, а ветку написать забывают. Тогда
    оплата товара уходила бы в финализацию VPN-подписки. Guard в конце
    process_successful_payment обязан перехватить такой случай ДО вызова
    finalize_subscription_payment.
    """

    def _source(self):
        from pathlib import Path
        return Path(
            "app/handlers/payments/payments_messages.py"
        ).read_text(encoding="utf-8")

    # Сам вызов сервиса переехал в subscription_finalize.py, а обработчик
    # зовёт его через finalize_subscription(...). Ищем именно этот вызов:
    # искать «payment_service.finalize_subscription_payment» в обработчике
    # больше нельзя — строка осталась бы только в комментарии, и тест
    # проходил бы, даже если предохранитель убрать.
    _FINALIZE_CALL = "await finalize_subscription("

    def test_guard_runs_before_subscription_finalization(self):
        src = self._source()
        guard = src.index('_route != "subscription"')
        finalize = src.index(self._FINALIZE_CALL)
        assert guard < finalize, (
            "предохранитель должен стоять ДО финализации подписки, "
            "иначе неопознанный товар всё равно станет VPN-подпиской"
        )

    def test_guard_logs_critical_and_marks_purchase_paid(self):
        src = self._source()
        guard_block = src[src.index('_route != "subscription"'):]
        guard_block = guard_block[:guard_block.index(self._FINALIZE_CALL)]
        assert "PURCHASE_ROUTE_UNHANDLED" in guard_block, "нужен искомый маркер в логах"
        assert "mark_pending_purchase_paid" in guard_block, "факт оплаты нельзя терять"
        assert "alert_payment_failure" in guard_block, "админ обязан узнать о зависшем заказе"

    def test_every_routed_type_is_either_handled_or_guarded(self, classify):
        """Каждый тип из списка либо имеет свою ветку, либо ловится guard'ом —
        третьего (тихая выдача VPN вместо товара) быть не должно."""
        from app.handlers.payments.payments_messages import _ROUTED_PURCHASE_TYPES
        for ptype in _ROUTED_PURCHASE_TYPES:
            assert classify({"purchase_type": ptype}) == ptype != "subscription"


@pytest.fixture(scope="module")
def resolve_amount():
    from app.handlers.payments.payments_messages import resolve_payment_amount_rubles
    return resolve_payment_amount_rubles


class TestResolvePaymentAmountRubles:
    """Для Stars total_amount — количество звёзд, а не рубли.

    Раньше это число записывалось как рублёвая сумма: выручка и реферальный
    кешбэк считались от числа звёзд, то есть занижались в разы.
    """

    def test_stars_uses_purchase_price_not_star_count(self, resolve_amount):
        pending = {"price_kopecks": 49900}
        assert resolve_amount(150, True, pending) == 499.0

    def test_stars_without_price_falls_back_and_does_not_crash(self, resolve_amount):
        assert resolve_amount(150, True, {}) == 150.0

    def test_stars_with_none_purchase_falls_back(self, resolve_amount):
        assert resolve_amount(150, True, None) == 150.0

    def test_card_converts_kopecks_to_rubles(self, resolve_amount):
        pending = {"price_kopecks": 49900}
        assert resolve_amount(49900, False, pending) == 499.0

    def test_card_ignores_purchase_price(self, resolve_amount):
        """Для карты источник истины — фактически списанная сумма."""
        pending = {"price_kopecks": 49900}
        assert resolve_amount(19900, False, pending) == 199.0

    def test_zero_price_kopecks_falls_back_to_star_count(self, resolve_amount):
        assert resolve_amount(75, True, {"price_kopecks": 0}) == 75.0
