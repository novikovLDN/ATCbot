from aiogram import Router

from .navigation import router as navigation_router
from .language import language_router
from .subscription import subscription_router
from .payments_callbacks import payments_router

router = Router()
router.include_router(navigation_router)
router.include_router(language_router)
router.include_router(subscription_router)
router.include_router(payments_router)
