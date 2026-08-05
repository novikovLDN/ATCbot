"""Спецпредложения из рассылок: сборка роутера.

ЧТО ЗДЕСЬ
    Только подключение подроутеров. Сами экраны разложены по
    предложениям:

        promo_discounts.py  «Купить со скидкой» и «Купить ГБ со скидкой» —
                            единственные, кто пишет скидку в базу
        gift_1m.py          подарок −30% на 1 месяц
        gift_3m.py          подарок −30% на 3 месяца
        gift_1y40.py        1 год со скидкой 40% (двухшаговый выбор)
        gift_reveal.py      «Посмотреть подарок» + возврат к тарифам

    Разложено так, потому что в одном файле на 1025 строк лежали пять
    независимых акций. Правка всегда касается одной из них — сменили
    процент, переписали текст, добавили тариф, — а рядом лежали чужие
    формулы цен, которые легко задеть.

ЭТО ПОЛЬЗОВАТЕЛЬСКИЕ ЭКРАНЫ, А НЕ АДМИНСКИЕ
    Кнопки приходят обычному человеку в рассылке. Проверок на админа
    здесь быть не должно — на этом уже обжигались: экраны лежали в
    админском разделе, попали под middleware «пускать только админа» и
    молча перестали работать у всех. Ни ошибки, ни ответа: middleware
    возвращает None, а None для aiogram — «обработано».

    Сторожит это tests/services/test_broadcast_buttons_reachable.py.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router. Обработчик остаётся объявленным, ошибок нет —
    кнопка в уже разосланном сообщении просто молчит. А сообщение это
    живёт в чате навсегда.

    Фильтры предложений не пересекаются (broadcast_*, bcg1m:*, bcg3m:*,
    bcg1y40:*), поэтому порядок подключения на поведение не влияет.
"""
from aiogram import Router

from app.handlers.callbacks.broadcast_offers import (
    gift_1m,
    gift_1y40,
    gift_3m,
    gift_reveal,
    promo_discounts,
)

broadcast_offers_router = Router()

broadcast_offers_router.include_router(promo_discounts.router)
broadcast_offers_router.include_router(gift_1m.router)
broadcast_offers_router.include_router(gift_3m.router)
broadcast_offers_router.include_router(gift_1y40.router)
broadcast_offers_router.include_router(gift_reveal.router)

# Реэкспорт: раньше всё лежало одним модулем, и к экранам обращались по
# имени. Список явный, чтобы пропажу было видно глазами при ревью.
from app.handlers.callbacks.broadcast_offers.promo_discounts import (  # noqa: F401,E402
    callback_broadcast_promo_buy,
    callback_broadcast_promo_traffic,
)
from app.handlers.callbacks.broadcast_offers.gift_1m import (  # noqa: F401,E402
    callback_broadcast_gift_1m,
    callback_broadcast_gift_1m_buy,
)
from app.handlers.callbacks.broadcast_offers.gift_3m import (  # noqa: F401,E402
    callback_broadcast_gift_3m,
    callback_broadcast_gift_3m_buy,
    callback_broadcast_gift_3m_info,
    callback_broadcast_gift_3m_menu,
)
from app.handlers.callbacks.broadcast_offers.gift_1y40 import (  # noqa: F401,E402
    callback_broadcast_gift_1y_40,
    callback_broadcast_gift_1y_40_buy,
    callback_broadcast_gift_1y_40_info,
    callback_broadcast_gift_1y_40_menu,
    callback_broadcast_gift_1y_40_tariff,
)
from app.handlers.callbacks.broadcast_offers.gift_reveal import (  # noqa: F401,E402
    callback_broadcast_back_to_tariffs,
    callback_broadcast_gift_reveal,
)

__all__ = [
    "broadcast_offers_router",
    "callback_broadcast_promo_buy",
    "callback_broadcast_promo_traffic",
    "callback_broadcast_gift_1m",
    "callback_broadcast_gift_1m_buy",
    "callback_broadcast_gift_3m",
    "callback_broadcast_gift_3m_menu",
    "callback_broadcast_gift_3m_info",
    "callback_broadcast_gift_3m_buy",
    "callback_broadcast_gift_1y_40",
    "callback_broadcast_gift_1y_40_menu",
    "callback_broadcast_gift_1y_40_info",
    "callback_broadcast_gift_1y_40_tariff",
    "callback_broadcast_gift_1y_40_buy",
    "callback_broadcast_gift_reveal",
    "callback_broadcast_back_to_tariffs",
]
