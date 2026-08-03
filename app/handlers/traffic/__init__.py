"""Раздел трафика: расход, витрины пакетов и оплата двух линеек.

ПОЧЕМУ ПАКЕТ, А НЕ ФАЙЛ
    app/handlers/traffic.py был на 1268 строк и держал четыре разные вещи:
    чтение расхода из Remnawave, витрины выбора пакета, checkout трафика и
    checkout обхода. Правка любой из них шла посреди трёх остальных.

СОСТАВ
    usage       — экран «сколько осталось», единственный поход в Remnawave
    packs       — витрины выбора пакета, только цены и клавиатуры
    pay_traffic — счета за пакет трафика (tariff='traffic_{N}gb')
    pay_bypass  — счета за пакет обхода (tariff='bypass_{N}gb')
    _shared     — форматирование объёма, полоса прогресса, автоудаление Lava

ЧТО ЛЕГКО СЛОМАТЬ
    Наружу раздел отдаёт РОВНО два имени: traffic_router (его подключает
    app/handlers/callbacks/__init__.py) и show_traffic_info_message (её
    зовёт /white из app/handlers/user/connect.py). Забытый include_router
    ниже не даёт никакой ошибки — кнопки просто перестают отвечать.

    Фильтры всех пяти роутеров не пересекаются: точные строки против
    префиксов с двоеточием. Если добавится префикс, поглощающий чужой
    (например 'traffic_pay' целиком), порядок подключения начнёт решать,
    кто ответит, — и раздел сломается молча.
"""
from aiogram import Router

from .usage import usage_router, show_traffic_info_message  # noqa: F401
from .packs import packs_router
from .pay_traffic import pay_traffic_router
from .pay_bypass import pay_bypass_router

traffic_router = Router()

traffic_router.include_router(usage_router)
traffic_router.include_router(packs_router)
traffic_router.include_router(pay_traffic_router)
traffic_router.include_router(pay_bypass_router)

__all__ = ["traffic_router", "show_traffic_info_message"]
