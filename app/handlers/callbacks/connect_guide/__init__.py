"""Инструкция подключения: как поставить приложение и добавить ключ.

ЧТО ЗДЕСЬ

    Пошаговый путь одного человека:

        выбор устройства  →  установка приложения  →  ключи  →  готово
        (device_select)      (install_app)            (keys)

    Плюс `qr` — ветка с QR-кодами, доступная с экрана ключей, и
    `catalog` — общие данные: идентификаторы картинок и ссылки на
    приложения по платформам.

ПОЧЕМУ ПАКЕТ, А НЕ ОДИН ФАЙЛ

    Был файл на 1071 строку. Инструкции правят при смене клиентского
    приложения — то есть трогают ссылки и картинки, — и делать это
    приходилось посреди генерации QR и работы с ключами.

ЧТО ЛЕГКО СЛОМАТЬ

    Забыть подключить роутер ниже. Это не даёт ошибки: aiogram просто не
    найдёт обработчика, кнопка молча перестанет отвечать, и в логах не
    будет ни строки. Добавили модуль — добавьте include_router.

    `_open_connect_screen` из device_select зовёт команда /instruction
    (app/handlers/user/support.py). Она должна оставаться импортируемой
    отсюда: переименуете или спрячете — команда умрёт тихо.

ПОРЯДОК ПОДКЛЮЧЕНИЯ

    Фильтры здесь не пересекаются, поэтому порядок не влияет на
    поведение. Он выбран по ходу пути пользователя — так проще читать.
"""
from aiogram import Router

from .catalog import _get_photo_id, _DEVICE_SELECT_PHOTO, _DOWNLOAD_LINKS, _SETUP_PHOTOS
from .device_select import device_select_router, _open_connect_screen
from .install_app import install_app_router
from .keys import keys_router
from .qr import qr_router

router = Router()

router.include_router(device_select_router)
router.include_router(install_app_router)
router.include_router(keys_router)
router.include_router(qr_router)

__all__ = [
    "router",
    # Нужна снаружи: команда /instruction открывает тот же экран, что и
    # кнопка «Подключиться».
    "_open_connect_screen",
    # Каталог держим доступным на прежних путях: на него ссылались из
    # соседних модулей, когда всё лежало одним файлом.
    "_get_photo_id",
    "_DEVICE_SELECT_PHOTO",
    "_DOWNLOAD_LINKS",
    # Картинки шагов берут экран подарочного обхода и экран подключения.
    "_SETUP_PHOTOS",
]
