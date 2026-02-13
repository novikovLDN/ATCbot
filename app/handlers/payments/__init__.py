from aiogram import Router

from .buy import payments_router as buy_router
from .topup_fsm import payments_router as topup_router
from .withdraw_fsm import payments_router as withdraw_router
from .promo_fsm import payments_router as promo_router
from .payments_messages import payments_router as payments_messages_router

router = Router()

router.include_router(buy_router)
router.include_router(topup_router)
router.include_router(withdraw_router)
router.include_router(promo_router)
router.include_router(payments_messages_router)
