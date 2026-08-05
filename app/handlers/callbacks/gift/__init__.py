"""Подарочные подписки: сборка роутера.

ЧТО ЗДЕСЬ
    Только подключение подроутеров. Содержимое разложено так:

        wizard.py      мастер покупки: тариф → период → способ оплаты
        payment.py     пять способов оплаты и выдача ссылки на подарок
        my_gifts.py    список купленных подарков и карточка одного
        offer_claim.py CTA «скидка 20%» под админским уведомлением
        formatting.py  подписи тарифа и периода — общие для всех экранов

    Разложено так, потому что в одном файле на 982 строки лежали покупка,
    деньги, просмотр купленного и посторонний CTA из другого раздела.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router. Ошибок нет — кнопка просто молчит.

    Реэкспорт `_send_gift_success`: её зовут снаружи по пути
    `app.handlers.callbacks.gift` — доставка товара после вебхука
    (app/handlers/payments/goods_delivery.py, app/services/payments/
    confirmation.py) и тесты, которые её подменяют. Уберёте имя отсюда —
    покупатель не получит ссылку на оплаченный подарок, и упадёт это не
    при импорте, а в момент оплаты.

    Порядок подключения на поведение не влияет: фильтры экранов не
    пересекаются, а шаги мастера дополнительно разделены состоянием FSM.
"""
from aiogram import Router

from app.handlers.callbacks.gift import (
    my_gifts,
    offer_claim,
    payment,
    wizard,
)

gift_router = Router()

gift_router.include_router(wizard.router)
gift_router.include_router(payment.router)
gift_router.include_router(my_gifts.router)
gift_router.include_router(offer_claim.router)

# Реэкспорт: раньше всё лежало одним модулем, и к этим именам обращаются
# снаружи (доставка подарка после вебхука, тесты, соседние экраны).
from app.handlers.callbacks.gift.formatting import (  # noqa: F401,E402
    _period_display,
    _tariff_display_name,
)
from app.handlers.callbacks.gift.wizard import (  # noqa: F401,E402
    callback_gift_period,
    callback_gift_start,
    callback_gift_tariff,
)
from app.handlers.callbacks.gift.payment import (  # noqa: F401,E402
    _schedule_invoice_deletion,
    _send_gift_success,
    callback_gift_pay_balance,
    callback_gift_pay_card,
    callback_gift_pay_crypto,
    callback_gift_pay_lava,
    callback_gift_pay_stars,
)
from app.handlers.callbacks.gift.my_gifts import (  # noqa: F401,E402
    GIFTS_PER_PAGE,
    callback_gift_detail,
    callback_my_gifts,
)
from app.handlers.callbacks.gift.offer_claim import (  # noqa: F401,E402
    callback_gift_offer_claim,
)

__all__ = [
    "gift_router",
    "_send_gift_success",
    "_schedule_invoice_deletion",
    "_tariff_display_name",
    "_period_display",
    "GIFTS_PER_PAGE",
    "callback_gift_start",
    "callback_gift_tariff",
    "callback_gift_period",
    "callback_gift_pay_balance",
    "callback_gift_pay_card",
    "callback_gift_pay_stars",
    "callback_gift_pay_crypto",
    "callback_gift_pay_lava",
    "callback_my_gifts",
    "callback_gift_detail",
    "callback_gift_offer_claim",
]
