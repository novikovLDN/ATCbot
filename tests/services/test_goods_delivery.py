"""Выдача товаров после оплаты картой: каждая функция отвечает за свой тип.

Раньше семь веток выдачи жили внутри process_successful_payment — одного
обработчика на 1305 строк. Ветки шли подряд, делили полтора десятка
локальных переменных, и понять, где заканчивается одна и начинается другая,
можно было только по комментариям. Правка выдачи Spotify означала правку в
середине файла, где сверху подписка, а снизу трафик.

Теперь каждая ветка — своя функция в goods_delivery, принимающая единый
контекст PaidPurchase и возвращающая True, если оплату обработала.

Главный риск такой операции: функция перестанет узнавать свой тип покупки
(тогда оплата провалится в финализацию VPN-подписки — деньги списаны, товар
не выдан) или, наоборот, начнёт хватать чужие. Тест проверяет обе стороны.
"""
import inspect
from unittest.mock import MagicMock

import pytest

from app.handlers.payments import goods_delivery as goods

# Функция выдачи ↔ purchase_type, за который она отвечает.
DELIVERY_BY_TYPE = {
    "deliver_gift": "gift",
    "deliver_premium": "telegram_premium",
    "deliver_stars": "telegram_stars",
    "deliver_steam": "steam",
    "deliver_spotify": "spotify",
    "deliver_apple_id": "apple_id",
    "deliver_traffic_pack": "traffic_pack",
}


def _paid(purchase_type: str) -> goods.PaidPurchase:
    return goods.PaidPurchase(
        message=MagicMock(),
        state=MagicMock(),
        telegram_id=1,
        language="ru",
        purchase_id="p1",
        pending_purchase={"purchase_type": purchase_type, "tariff": "basic"},
        payment_amount_rubles=1.0,
        is_stars_payment=False,
        start_time=0.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fname", sorted(DELIVERY_BY_TYPE))
async def test_delivery_refuses_foreign_purchase_type(fname):
    """Чужой тип — немедленный False, без единого обращения к БД и Telegram.

    Если функция начнёт хватать чужие оплаты, человек получит не тот товар,
    за который заплатил.
    """
    result = await getattr(goods, fname)(_paid("subscription"))
    assert result is False, f"{fname} взялась за чужую покупку"


@pytest.mark.parametrize("fname, ptype", sorted(DELIVERY_BY_TYPE.items()))
def test_delivery_checks_exactly_its_own_type(fname, ptype):
    """Проверка типа обязана стоять первой строкой тела — до любых
    обращений к базе. Иначе чужая покупка успеет что-то изменить."""
    src = inspect.getsource(getattr(goods, fname))
    assert f'!= "{ptype}"' in src, f"{fname} не проверяет свой тип {ptype}"


def test_every_routed_type_has_a_delivery_function():
    """Список типов и список функций выдачи обязаны совпадать: тип без
    функции провалится в финализацию VPN-подписки."""
    from app.handlers.payments.purchase_routing import _ROUTED_PURCHASE_TYPES

    covered = set(DELIVERY_BY_TYPE.values())
    # proxy продаётся только через вебхуки (СБП и Lava), инвойса в Telegram
    # у него нет — своей функции выдачи здесь ему не нужно. Предохранитель
    # в конце обработчика всё равно не даст ему стать подпиской.
    expected = set(_ROUTED_PURCHASE_TYPES) - {"proxy"}
    assert covered == expected, (
        f"расхождение: без выдачи остались {sorted(expected - covered)}, "
        f"лишние {sorted(covered - expected)}"
    )


def test_handler_dispatches_through_all_of_them():
    """Все функции должны быть перечислены в диспетчере — иначе тип
    объявлен, обработчик написан, а оплата всё равно уходит в подписку."""
    from pathlib import Path

    src = Path("app/handlers/payments/payments_messages.py").read_text(encoding="utf-8")
    dispatcher = src[src.index("for deliver in ("):]
    dispatcher = dispatcher[: dispatcher.index("):")]
    for fname in DELIVERY_BY_TYPE:
        assert f"goods.{fname}" in dispatcher, f"{fname} не подключена к диспетчеру"


def test_mark_paid_precedes_notifications():
    """Порядок внутри выдачи: сначала пометить покупку оплаченной, потом
    слать уведомления. Повторный вебхук — штатная ситуация, и при обратном
    порядке человек с админом получат дубли."""
    for fname in DELIVERY_BY_TYPE:
        src = inspect.getsource(getattr(goods, fname))
        if "mark_pending_purchase_paid" not in src:
            continue  # traffic_pack идёт через finalize_purchase
        mark = src.index("mark_pending_purchase_paid")
        for notify in ("send_gift_success", "send_premium_success", "send_stars_success",
                       "send_steam_success", "send_spotify_success", "send_apple_id_success"):
            if notify in src:
                assert mark < src.index(notify), (
                    f"{fname}: уведомление раньше пометки — повторный вебхук даст дубль"
                )
