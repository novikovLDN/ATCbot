"""Половина награды считается одним способом и показывается с копейками.

ЧТО БЫЛО

    Витрина раннего сбора и фактическое начисление считали половину награды
    по-разному. Кнопка — `reward // 200`, то есть сразу в рублях, с
    отбрасыванием копеек. Начисление — `reward // 2`, в копейках. Для наград
    с нечётным числом сотен копеек это расходилось:

        вишня 2700 → кнопка «+13 ₽», на баланс 13,50 ₽
        дуб    5300 → кнопка «+26 ₽», на баланс 26,50 ₽
        лимон  3600 → кнопка «+18 ₽», на баланс 18,00 ₽ (совпало случайно)

    Сейчас расхождение в пользу пользователя, поэтому жалоб нет. Но витрина
    и касса расходятся, а при обратном знаке округления это уже обращения в
    поддержку.

ЧТО ЗАКРЕПЛЯЕТ ТЕСТ

    Единственный способ посчитать половину — farm_half_reward_kopecks, и
    показывать её надо через format_kopecks_rub (два знака, как во всей
    остальной денежной части бота: «{amount:.2f} ₽» в словарях i18n).
    Тест ловит возврат старой формулы `// 200` для любой культуры.
"""
import pytest

from app.handlers.game import (
    PLANT_TYPES,
    farm_half_reward_kopecks,
    format_kopecks_rub,
)


@pytest.mark.parametrize("plant_key", sorted(PLANT_TYPES))
def test_showcase_matches_credited_amount(plant_key):
    """Показанное на кнопке = зачисленное на баланс, для каждой культуры."""
    reward = PLANT_TYPES[plant_key]["reward"]

    credited_kopecks = farm_half_reward_kopecks(reward)
    shown_rub = float(format_kopecks_rub(credited_kopecks))

    assert round(shown_rub * 100) == credited_kopecks, (
        f"{plant_key}: показываем {shown_rub} ₽, начисляем {credited_kopecks} коп."
    )


@pytest.mark.parametrize("plant_key", sorted(PLANT_TYPES))
def test_old_rub_rounding_is_not_used(plant_key):
    """Старая формула `reward // 200` не должна вернуться под видом округления."""
    reward = PLANT_TYPES[plant_key]["reward"]
    old_rub = reward // 200
    new_rub = farm_half_reward_kopecks(reward) / 100

    assert new_rub >= old_rub
    if reward % 200:
        assert new_rub > old_rub, (
            f"{plant_key}: половина награды снова теряет 50 копеек"
        )


def test_kopecks_with_tail_are_visible():
    assert format_kopecks_rub(2650) == "26.50"
    assert format_kopecks_rub(1800) == "18.00"
    assert format_kopecks_rub(0) == "0.00"


def test_half_of_odd_hundred_keeps_fifty_kopecks():
    assert farm_half_reward_kopecks(5300) == 2650
    assert farm_half_reward_kopecks(2700) == 1350
    assert farm_half_reward_kopecks(3600) == 1800
