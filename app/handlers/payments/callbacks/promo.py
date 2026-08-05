"""Промокод: вход в ввод и выход из него.

ЧТО ЗДЕСЬ
    Две кнопки: «Ввести промокод» (ставит состояние ожидания) и «Назад» с
    экрана ошибки промокода.

ПОЧЕМУ ВЫДЕЛЕНО
    Крошечная, но самостоятельная ветка со своим состоянием FSM
    (PromoCodeInput). Сам разбор введённого кода живёт в обработчике
    сообщений, а не здесь.

ЧТО ЛЕГКО СЛОМАТЬ
    Перед установкой состояния ожидания оно сбрасывается в None. Это не
    лишний шаг: без сброса человек, уже стоявший в другом состоянии,
    оставался в «зависшем» экране.

    Если промо-сессия уже активна, второй ввод не предлагается — иначе
    человек перезабьёт действующую скидку менее выгодной.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.screens import show_tariffs_main_screen
from app.handlers.common.utils import get_promo_session
from app.handlers.common.states import PromoCodeInput

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "enter_promo")
async def callback_enter_promo(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки ввода промокода"""
    try:
        await callback.answer()
    except Exception:
        pass

    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем активную промо-сессию
    promo_session = await get_promo_session(state)
    if promo_session:
        # Промокод уже применён - показываем сообщение
        text = i18n_get_text(language, "buy.promo_applied")
        await callback.message.answer(text, parse_mode="HTML")
        return

    # CRITICAL FIX: Очищаем предыдущие FSM состояния перед установкой нового
    # Это гарантирует, что пользователь не останется в "зависшем" состоянии
    await state.set_state(None)
    
    # Устанавливаем состояние ожидания промокода
    await state.set_state(PromoCodeInput.waiting_for_promo)

    text = i18n_get_text(language, "buy.enter_promo_text")
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "promo_back")
async def callback_promo_back(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад' при ошибке промокода - возвращает на экран выбора тарифа"""
    # CRITICAL FIX: Очищаем FSM state при выходе с экрана ввода промокода
    await state.clear()
    
    # CRITICAL FIX: Используем каноничный экран тарифов вместо локального render
    await show_tariffs_main_screen(callback, state)
