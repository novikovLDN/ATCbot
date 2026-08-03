import logging

from aiogram import Router

from .base import admin_base_router
from .promo_fsm import admin_promo_fsm_router
from .activations import admin_activations_router
from .audit import admin_audit_router
from .export import admin_export_router
from .stats import admin_stats_router
from .broadcast_gifts import admin_broadcast_gifts_router
from .referral_screens import admin_referral_router
from .access import admin_access_router
from .finance import admin_finance_router
from .reissue import admin_reissue_router
from .broadcast import admin_broadcast_router
from .notifications import admin_notifications_router
from .traffic_admin import admin_traffic_router
from .bypass_gift import admin_bypass_gift_router
from .migration import admin_migration_router
from .recovery_premium import admin_premium_recovery_router
from .audit_subs import admin_audit_subs_router
from .audit_db_dates import admin_audit_db_dates_router
from .promo_trial import admin_promo_trial_router
from .bonus import admin_bonus_router
from .stage_users import admin_stage_users_router
from .farm_storm import admin_farm_storm_router
from .apple_id_delivery import apple_id_delivery_router
from .spotify_delivery import spotify_delivery_router

_admin_logger = logging.getLogger(__name__)

router = Router()

router.include_router(admin_base_router)
router.include_router(admin_promo_fsm_router)
router.include_router(admin_activations_router)
router.include_router(admin_audit_router)
router.include_router(admin_export_router)
router.include_router(admin_stats_router)
router.include_router(admin_broadcast_gifts_router)
router.include_router(admin_referral_router)
router.include_router(admin_access_router)
router.include_router(admin_finance_router)
router.include_router(admin_reissue_router)
router.include_router(admin_broadcast_router)
router.include_router(admin_notifications_router)
router.include_router(admin_traffic_router)
router.include_router(admin_bypass_gift_router)
router.include_router(admin_migration_router)
router.include_router(admin_premium_recovery_router)
router.include_router(admin_audit_subs_router)
router.include_router(admin_audit_db_dates_router)
router.include_router(admin_promo_trial_router)
router.include_router(admin_bonus_router)
router.include_router(admin_stage_users_router)
router.include_router(admin_farm_storm_router)
router.include_router(apple_id_delivery_router)
router.include_router(spotify_delivery_router)


# ──────────────────────────────────────────────────────────────────────
#  Единая проверка «я админ» на входе в весь админский раздел
# ──────────────────────────────────────────────────────────────────────
#
# Зачем middleware, когда проверка и так стоит в каждом обработчике.
# Она стоит не в каждом: проверка написана руками 193 раза в виде
# `if callback.from_user.id != config.ADMIN_TELEGRAM_ID: return`, и это
# ровно тот случай, когда достаточно один раз забыть строчку в новом
# обработчике, чтобы админская операция стала доступна кому угодно.
# Найти такую дыру глазами в двадцати пяти модулях нельзя.
#
# Middleware на родительском роутере закрывает раздел целиком, включая
# обработчики, которые напишут завтра. Существующие ручные проверки не
# трогаем: они безвредны и работают как второй рубеж, а массовая замена
# 193 мест — источник регрессий (у каждой свой хвост: где-то return,
# где-то answer с текстом, где-то очистка FSM).
#
# Источник истины по «кто админ» — app.services.admin_auth.is_admin.
# Из четырёх реализаций проверки эта остаётся единственной живой.
async def _require_admin(handler, event, data: dict):
    """Пропустить дальше только администратора.

    Молчим в ответ чужому: админский раздел не должен подтверждать своё
    существование посторонним. Telegram сам погасит «часики» на кнопке.
    """
    from app.services.admin_auth import is_admin

    user = getattr(event, "from_user", None)
    if user is not None and is_admin(user.id):
        return await handler(event, data)

    _admin_logger.warning(
        "ADMIN_ACCESS_DENIED user=%s event=%s",
        getattr(user, "id", None), type(event).__name__,
    )
    return None


router.message.middleware(_require_admin)
router.callback_query.middleware(_require_admin)
