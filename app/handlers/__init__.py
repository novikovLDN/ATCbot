"""Корневая сборка роутеров бота.

ПОРЯДОК ПОДКЛЮЧЕНИЯ ВАЖЕН
    aiogram отдаёт апдейт первому подошедшему обработчику. Ловец неизвестных
    сообщений подключается последним — иначе он перехватит всё остальное.

СОСТАВ
    callbacks — нажатия кнопок: меню, инструкции, покупки, Apple ID
    user      — команды пользователя и старт
    payments  — платёжные сообщения и колбэки
    admin     — админские команды
    game      — игровое меню, боулинг, кубики, бомбер
    farm      — мини-игра «Ферма» (вынесена из game)
"""
from aiogram import Router

from .callbacks import router as callbacks_router
from .user import router as user_router
from .payments import router as payments_router
from .admin import router as admin_router
from .game import router as game_router
from .farm import router as farm_router
from app.core.unknown_message_filter import unknown_message_router

router = Router()

router.include_router(callbacks_router)
router.include_router(user_router)
router.include_router(payments_router)
router.include_router(admin_router)
router.include_router(game_router)
router.include_router(farm_router)
# ПОСЛЕДНИМ — catch-all для неизвестных сообщений (только default_state)
router.include_router(unknown_message_router)
