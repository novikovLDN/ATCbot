"""
Bot-side admin: /admin (web dashboard link, «Написать пользователю», dashboard
password reset), the admin → user chat, /platega_sub_status, and the 🔒 shop
delivery handlers for Apple ID / Spotify orders. The old in-bot admin panel
was removed (owner decision 2026-09-14); everything else is the web dashboard.
"""
from aiogram import Router

from .base import admin_base_router
from .apple_id_delivery import apple_id_delivery_router
from .spotify_delivery import spotify_delivery_router

router = Router()

router.include_router(admin_base_router)
router.include_router(apple_id_delivery_router)
router.include_router(spotify_delivery_router)
