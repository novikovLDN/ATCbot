"""Экраны покупки подписки: сборка роутера.

ЧТО ЗДЕСЬ
    Только подключение подроутеров. Содержимое разложено так:

        subscription_menu.py  управление подпиской и смена тарифа
        purchase_flow.py      тариф → период → способ оплаты
        promo.py              вход в ввод промокода и выход из него
        business.py           бизнес-каталог и выбор страны
        tariff_meta.py        подписи тарифов, значки периодов, текущий тариф

    Разложено так, потому что в одном файле на 976 строк лежали четыре
    разные ветки: покупка с нуля, смена тарифа действующим подписчиком,
    промокод и бизнес-сценарий со страной.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router. Ошибок нет — кнопка молчит. Здесь это самая
    дорогая тишина в боте: молчит кнопка «Купить».

    `_period_badge` берут снаружи — подарки (gift/wizard.py) и навигация.
    Реэкспорт ниже держит прежний путь `app.handlers.payments.callbacks`.

    Порядок подключения на поведение не влияет: фильтры не пересекаются,
    а шаги покупки дополнительно разделены состояниями FSM.
"""
from aiogram import Router

from app.handlers.payments.callbacks import (
    business,
    promo,
    purchase_flow,
    subscription_menu,
)

payments_callbacks_router = Router()

payments_callbacks_router.include_router(subscription_menu.router)
payments_callbacks_router.include_router(purchase_flow.router)
payments_callbacks_router.include_router(promo.router)
payments_callbacks_router.include_router(business.router)

# Реэкспорт: раньше всё лежало одним модулем, и к этим именам обращаются
# соседние экраны и тесты.
from app.handlers.payments.callbacks.tariff_meta import (  # noqa: F401,E402
    _TARIFF_META,
    _current_tariff_key,
    _period_badge,
)
from app.handlers.payments.callbacks.subscription_menu import (  # noqa: F401,E402
    callback_buy_vpn,
    callback_switch_tariff,
    callback_switch_tariff_menu,
)
from app.handlers.payments.callbacks.purchase_flow import (  # noqa: F401,E402
    callback_downgrade_confirm_basic,
    callback_tariff_period,
    callback_tariff_type,
)
from app.handlers.payments.callbacks.promo import (  # noqa: F401,E402
    callback_enter_promo,
    callback_promo_back,
)
from app.handlers.payments.callbacks.business import (  # noqa: F401,E402
    callback_biz_country_selected,
    callback_corporate_access_request,
)

__all__ = [
    "payments_callbacks_router",
    "_TARIFF_META",
    "_period_badge",
    "_current_tariff_key",
    "callback_buy_vpn",
    "callback_switch_tariff_menu",
    "callback_switch_tariff",
    "callback_tariff_type",
    "callback_tariff_period",
    "callback_downgrade_confirm_basic",
    "callback_enter_promo",
    "callback_promo_back",
    "callback_corporate_access_request",
    "callback_biz_country_selected",
]
