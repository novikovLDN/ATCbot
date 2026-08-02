"""Сборка колбэк-роутеров бота.

ПОРЯДОК ВАЖЕН
    aiogram отдаёт апдейт первому подошедшему обработчику. Роутеры с
    точными фильтрами должны идти раньше тех, что ловят по префиксу,
    иначе более общий обработчик перехватит чужой колбэк.

ДОБАВЛЯЯ РОУТЕР
    Импортируйте его здесь и подключите ниже — забытый include_router
    означает, что кнопки просто не работают, без единой ошибки в логах.
"""
from aiogram import Router

from .navigation import router as navigation_router
from .apple_id import router as apple_id_router
from .connect_guide import router as connect_guide_router
from .language import language_router
from .subscription import subscription_router
from .payments_callbacks import payments_router
from .gift import gift_router
from .bypass_setup import bypass_setup_router
from app.handlers.traffic import traffic_router
from app.handlers.proxy import proxy_router

router = Router()
router.include_router(navigation_router)
router.include_router(apple_id_router)
router.include_router(connect_guide_router)
router.include_router(language_router)
router.include_router(subscription_router)
router.include_router(payments_router)
router.include_router(gift_router)
router.include_router(bypass_setup_router)
router.include_router(traffic_router)
router.include_router(proxy_router)
