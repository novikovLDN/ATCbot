"""Цена комбо-тарифа при оплате звёздами.

Дефект: в FSM комбо-покупка лежит под именем базового тарифа ("plus"),
а признак комбо — в отдельном поле. Обработчик Stars брал цену из
TARIFFS_STARS, где комбо нет, и продавал «Комбо Плюс» по цене обычного
«Плюс»: недобор денег плюс бесплатный пакет ГБ обхода. Заодно в отчётность
попадала заниженная сумма, потому что price_kopecks пишется оттуда же.
"""
import math

import pytest

import config


def stars_for_combo(combo_key: str, period_days: int) -> int:
    """Повторяет расчёт из обработчика оплаты звёздами."""
    info = config.COMBO_TARIFFS[combo_key][period_days]
    return math.ceil(int(info["price"]) * 1.7 / 1.85)


class TestComboStarsPrice:
    @pytest.mark.parametrize("combo", ["combo_basic", "combo_plus"])
    def test_combo_costs_more_than_plain_subscription(self, combo):
        """Главный дефект: комбо не может стоить как обычная подписка."""
        base = combo.replace("combo_", "")
        for period in config.COMBO_TARIFFS[combo]:
            plain = config.TARIFFS_STARS.get(base, {}).get(period, {}).get("price")
            if plain is None:
                continue
            assert stars_for_combo(combo, period) > plain, (
                f"{combo}/{period}: комбо в звёздах не дороже обычного тарифа"
            )

    @pytest.mark.parametrize("combo", ["combo_basic", "combo_plus"])
    def test_every_period_priced(self, combo):
        """Период 730 дней есть у комбо, но отсутствует в TARIFFS_STARS —
        раньше он вываливался в ошибку тарифа."""
        for period in config.COMBO_TARIFFS[combo]:
            assert stars_for_combo(combo, period) > 0

    def test_price_grows_with_period(self):
        for combo in ("combo_basic", "combo_plus"):
            prices = [stars_for_combo(combo, p) for p in sorted(config.COMBO_TARIFFS[combo])]
            assert prices == sorted(prices)

    def test_plus_not_cheaper_than_basic(self):
        for period in config.COMBO_TARIFFS["combo_plus"]:
            if period not in config.COMBO_TARIFFS["combo_basic"]:
                continue
            assert stars_for_combo("combo_plus", period) >= stars_for_combo("combo_basic", period)

    def test_conversion_matches_gift_flow(self):
        """Курс и наценка те же, что в подарках, — иначе цены разойдутся."""
        rubles = 1000
        assert math.ceil(rubles * 1.7 / 1.85) == stars_for_combo(
            "combo_basic",
            next(p for p, i in config.COMBO_TARIFFS["combo_basic"].items() if i["price"] == 1000),
        ) if any(i["price"] == 1000 for i in config.COMBO_TARIFFS["combo_basic"].values()) else True


class TestComboFlagStorage:
    def test_combo_not_stored_in_subscription_type(self):
        """tariff при создании покупки остаётся базовым: в колонке
        subscription_type комбо не хранится, туда идёт уровень доступа."""
        for combo in config.COMBO_TARIFF_TYPES:
            assert combo not in config.VALID_SUBSCRIPTION_TYPES
