from aiogram import Router

from .start import user_router as start_router
from .profile import user_router as profile_router
from .support import user_router as support_router
from .language_commands import user_router as language_router
from .referrals import user_router as referrals_router

router = Router()

router.include_router(start_router)
router.include_router(profile_router)
router.include_router(support_router)
router.include_router(language_router)
router.include_router(referrals_router)
