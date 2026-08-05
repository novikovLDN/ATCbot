"""Оплата подписки через внешних провайдеров: сборка роутера.

ЧТО ЗДЕСЬ
    Только подключение подроутеров. Сами экраны разложены по провайдерам:

        telegram_invoice.py  карта в Telegram (ЮKassa) и Telegram Stars
        platega.py           Platega: карта РФ, международная карта, СБП
        cryptobot.py         CryptoBot (криптовалюта)
        lava.py              Lava (карта)

    Разложено так, потому что в одном файле на 1031 строку лежали пять
    провайдеров. Правка всегда касается ровно одного из них — сменился
    формат ответа, поменялась наценка, отвалился API, — а читать
    приходилось тысячу строк чужих потоков оплаты.

ЧТО ОБЩЕЕ У ВСЕХ ЭКРАНОВ
    Один порядок действий: лимит запросов → проверка состояния FSM →
    создание pending_purchase → счёт у провайдера → кнопка оплаты.
    Покупка обязана создаваться ДО счёта: вебхук провайдера приходит по
    purchase_id, и если записи ещё нет, оплата зависнет без товара.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router. Обработчик остаётся объявленным, ошибок в
    логах нет — кнопка оплаты просто перестаёт отвечать, и узнаете вы об
    этом от человека, который не смог заплатить. Список подроутеров ниже
    сторожит tests/services/test_pay_external_split.py.

    Фильтры у экранов не пересекаются (pay:card, pay:stars, pay:card_pl,
    pay:intl_pl, pay:sbp, pay:crypto, pay:lava и префикс pay_tariff_card:),
    поэтому порядок подключения на поведение не влияет.
"""
from aiogram import Router

from app.handlers.callbacks.pay_external import (
    cryptobot,
    lava,
    platega,
    telegram_invoice,
)

pay_external_router = Router()

pay_external_router.include_router(telegram_invoice.router)
pay_external_router.include_router(platega.router)
pay_external_router.include_router(cryptobot.router)
pay_external_router.include_router(lava.router)

# Реэкспорт обработчиков: к ним обращаются по имени тесты, а раньше всё
# лежало одним модулем. Список явный, чтобы пропажу было видно глазами.
from app.handlers.callbacks.pay_external.telegram_invoice import (  # noqa: F401,E402
    callback_pay_card,
    callback_pay_stars,
    callback_pay_tariff_card,
)
from app.handlers.callbacks.pay_external.platega import (  # noqa: F401,E402
    _start_platega_payment,
    callback_pay_card_pl,
    callback_pay_intl_pl,
    callback_pay_sbp,
)
from app.handlers.callbacks.pay_external.cryptobot import (  # noqa: F401,E402
    callback_pay_crypto,
)
from app.handlers.callbacks.pay_external.lava import (  # noqa: F401,E402
    callback_pay_lava,
)

__all__ = [
    "pay_external_router",
    "callback_pay_card",
    "callback_pay_stars",
    "callback_pay_tariff_card",
    "callback_pay_card_pl",
    "callback_pay_intl_pl",
    "callback_pay_sbp",
    "callback_pay_crypto",
    "callback_pay_lava",
    "_start_platega_payment",
]
