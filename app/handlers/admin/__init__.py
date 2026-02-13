from aiogram import Router

from .base import admin_base_router
from .activations import admin_activations_router
from .audit import admin_audit_router
from .reissue import admin_reissue_router
# TODO: Add remaining routers as they are populated:
# from .stats import admin_stats_router
# from .broadcast import admin_broadcast_router
# from .export import admin_export_router

router = Router()

router.include_router(admin_base_router)
router.include_router(admin_activations_router)
router.include_router(admin_audit_router)
router.include_router(admin_reissue_router)
# TODO: Include remaining routers:
# router.include_router(admin_stats_router)
# router.include_router(admin_broadcast_router)
# router.include_router(admin_export_router)
