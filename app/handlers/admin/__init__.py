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
from .notifications import admin_notifications_router
from .traffic_admin import admin_traffic_router
from .bypass_gift import admin_bypass_gift_router
from .migration import admin_migration_router
from .reconcile import admin_reconcile_router
from .recovery_premium import admin_premium_recovery_router
from .bonus import admin_bonus_router
from .stage_users import admin_stage_users_router
from .farm_storm import admin_farm_storm_router

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
router.include_router(admin_notifications_router)
router.include_router(admin_traffic_router)
router.include_router(admin_bypass_gift_router)
router.include_router(admin_migration_router)
router.include_router(admin_reconcile_router)
router.include_router(admin_premium_recovery_router)
router.include_router(admin_bonus_router)
router.include_router(admin_stage_users_router)
router.include_router(admin_farm_storm_router)
