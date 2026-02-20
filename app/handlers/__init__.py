"""
Handlers module - modularized handlers for Telegram bot.

Root aggregation: callbacks, user, payments, admin, game.
"""
from aiogram import Router

from .callbacks import router as callbacks_router
from .user import router as user_router
from .payments import router as payments_router
from .admin import router as admin_router
from .game import router as game_router

router = Router()

# Include game_router FIRST to ensure farm callbacks are handled before catch-all handlers
router.include_router(game_router)
router.include_router(callbacks_router)
router.include_router(user_router)
router.include_router(payments_router)
router.include_router(admin_router)
