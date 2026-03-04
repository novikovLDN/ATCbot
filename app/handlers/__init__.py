"""
Handlers module - modularized handlers for Telegram bot.

Root aggregation: callbacks, user, payments, admin, game.
Catch-all for unknown messages — last.
"""
from aiogram import Router

from .callbacks import router as callbacks_router
from .user import router as user_router
from .payments import router as payments_router
from .admin import router as admin_router
from .game import router as game_router
from app.core.unknown_message_filter import unknown_message_router

router = Router()

router.include_router(callbacks_router)
router.include_router(user_router)
router.include_router(payments_router)
router.include_router(admin_router)
router.include_router(game_router)
# ПОСЛЕДНИМ — catch-all для неизвестных сообщений (только default_state)
router.include_router(unknown_message_router)
