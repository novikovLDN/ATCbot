from aiogram import Router

from .base import admin_base_router
from .promo_fsm import admin_promo_fsm_router
from .activations import admin_activations_router
from .audit import admin_audit_router
from .export import admin_export_router
from .stats import admin_stats_router
from .access import admin_access_router
from .finance import admin_finance_router
from .reissue import admin_reissue_router
from .broadcast import admin_broadcast_router

router = Router()

router.include_router(admin_base_router)
router.include_router(admin_promo_fsm_router)
router.include_router(admin_activations_router)
router.include_router(admin_audit_router)
router.include_router(admin_export_router)
router.include_router(admin_stats_router)
router.include_router(admin_access_router)
router.include_router(admin_finance_router)
router.include_router(admin_reissue_router)
router.include_router(admin_broadcast_router)
