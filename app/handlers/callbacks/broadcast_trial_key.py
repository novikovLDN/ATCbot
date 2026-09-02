"""
Broadcast-кнопка «🎁 Получить пробный ключ».

Callback data: `broadcast_trial_key:{broadcast_id}` (broadcast_id опционален —
если отсутствует/не число, используется 0 как bucket).

Логика клика (по ТЗ владельца):
  • Подарок = +1 день подписки и +1 ГБ трафика обхода белых списков.
  • Ограничение: ОДИН раз на рассылку (broadcast_id, telegram_id) — атомарно
    через database.claim_broadcast_trial_key (PK + ON CONFLICT). Повторный клик
    по той же рассылке → toast «Подарок уже получен».
  • Если у юзера УЖЕ есть профиль в панели → grant_access(source='trial')
    продлевает подписку на +1 день (стабильный UUID), +1 ГБ обхода сверху.
  • Если профиля не было → grant_access создаёт нового юзера в панели с
    1 днём доступа, обход выставляется РОВНО в 1 ГБ.
  • После выдачи: сообщение «Подарок активирован» + кнопка подключения.
  • Через ~1.5с автоматически шлём экран выбора устройства (setup.select_device),
    чтобы юзер сразу подключил устройство.

Идемпотентность выдачи ГБ достигается delta-подходом: считаем, сколько байт
обхода было ДО выдачи (baseline), сколько стало ПОСЛЕ grant_access, и добавляем
ровно столько, чтобы итог = baseline + 1 ГБ. Так новичок (grant создал bypass
на 500 МБ trial-дефолт) получает ровно 1 ГБ, а существующий — ровно +1 ГБ.
"""
import asyncio
import logging
from datetime import timedelta

import config
import database
from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.emoji import CE

broadcast_trial_key_router = Router()
logger = logging.getLogger(__name__)

ONE_GB = 1024 ** 3
DEVICE_SCREEN_DELAY_SECONDS = 1.5


async def _current_bypass_bytes(telegram_id: int) -> int | None:
    """trafficLimitBytes bypass-энтити (username=str(tg)) или None, если нет."""
    try:
        from app.services import remnawave_api
        entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
        if not isinstance(entity, dict):
            return None
        return int(entity.get("trafficLimitBytes") or 0)
    except Exception:
        return None


async def _deliver_bypass_gb(telegram_id: int, extra_bytes: int) -> bool:
    """Начислить extra_bytes обхода, СОЗДАВ entity при отсутствии.

    Тонкая обёртка над remnawave_service.add_bypass_traffic (create-if-missing):
    top-up существующей энтити ИЛИ create новой с extra_bytes как лимитом.
    """
    if extra_bytes <= 0:
        return False
    try:
        from app.services import remnawave_service
        return bool(await remnawave_service.add_bypass_traffic(
            telegram_id,
            extra_bytes=int(extra_bytes),
            subscription_type="basic",
            period_days=1,
        ))
    except Exception as e:
        logger.error("TRIAL_KEY_BYPASS_DELIVER_FAIL tg=%s: %s", telegram_id, e)
        return False


def _device_select_keyboard(language: str) -> InlineKeyboardMarkup:
    """Та же клавиатура выбора устройства, что и в /connect (cmd_connect)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iPhone / iPad", callback_data="setup_step1:ios", style="primary"),
            InlineKeyboardButton(text="🤖 Android", callback_data="setup_step1:android", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🍎 Mac", callback_data="setup_step1:macos", style="primary"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="setup_step1:windows", style="primary"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])


async def _send_device_screen_delayed(bot, chat_id: int, telegram_id: int, language: str) -> None:
    """Через ~1.5с после подарка — экран выбора устройства (fire-and-forget)."""
    try:
        await asyncio.sleep(DEVICE_SCREEN_DELAY_SECONDS)
        text = i18n_get_text(language, "setup.select_device")
        keyboard = _device_select_keyboard(language)
        from app.utils.telegram_safe import safe_send_message
        try:
            from app.handlers.callbacks.navigation import _DEVICE_SELECT_PHOTO
            _ds_photo = _DEVICE_SELECT_PHOTO.get("prod" if config.IS_PROD else "stage", "")
        except Exception:
            _ds_photo = ""
        if _ds_photo:
            try:
                await bot.send_photo(
                    chat_id, photo=_ds_photo, caption=text,
                    reply_markup=keyboard, parse_mode="HTML",
                )
                return
            except Exception:
                pass  # fallback на текст ниже
        await safe_send_message(bot, chat_id, text, reply_markup=keyboard)
    except Exception as e:
        logger.warning(
            "TRIAL_KEY_DEVICE_SCREEN_FAIL tg=%s: %s", telegram_id, e,
        )


@broadcast_trial_key_router.callback_query(F.data.startswith("broadcast_trial_key:"))
async def callback_broadcast_trial_key(callback: CallbackQuery) -> None:
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Burst-guard (реальная идемпотентность — в БД ниже).
    is_allowed, rl_msg = check_rate_limit(telegram_id, "trial_key_gift")
    if not is_allowed:
        try:
            await callback.answer(
                rl_msg or i18n_get_text(language, "common.rate_limit_message"),
                show_alert=True,
            )
        except Exception:
            pass
        return

    # Парсим broadcast_id (0 если отсутствует / не число).
    parts = (callback.data or "").split(":")
    broadcast_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0

    # Идемпотентность: один подарок на рассылку.
    newly_claimed = await database.claim_broadcast_trial_key(broadcast_id, telegram_id)
    if not newly_claimed:
        try:
            await callback.answer(
                i18n_get_text(language, "broadcast.trial_key_already"),
                show_alert=False,
            )
        except Exception:
            pass
        return

    try:
        await callback.answer()
    except Exception:
        pass

    try:
        # Снимок обхода ДО выдачи.
        baseline = await _current_bypass_bytes(telegram_id)

        # +1 день подписки. grant_access аддитивен: renewal (стабильный UUID)
        # для активной подписки, либо new_issuance (создаёт юзера в панели).
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=timedelta(days=1),
            source="trial",
            admin_telegram_id=None,
        )
        if not result or not result.get("subscription_end"):
            raise RuntimeError("grant_access returned no subscription_end")

        # Доводим обход до baseline + 1 ГБ (у новичка = ровно 1 ГБ).
        after = await _current_bypass_bytes(telegram_id)
        base = baseline or 0
        cur = after or 0
        delta = (base + ONE_GB) - cur
        if delta > 0:
            ok = await _deliver_bypass_gb(telegram_id, delta)
            if not ok:
                logger.error(
                    "TRIAL_KEY_BYPASS_NOT_DELIVERED tg=%s delta=%s baseline=%s after=%s",
                    telegram_id, delta, baseline, after,
                )

        logger.info(
            "BROADCAST_TRIAL_KEY_GRANTED tg=%s broadcast_id=%s action=%s "
            "sub_end=%s bypass_baseline=%s bypass_after=%s delta=%s",
            telegram_id, broadcast_id, result.get("action"),
            result.get("subscription_end"), baseline, after, max(0, delta),
        )
    except Exception as e:
        # Выдача упала ПОСЛЕ claim — освобождаем claim, чтобы юзер мог повторить,
        # и показываем ошибку. Подписка/обход финансово не критичны (подарок),
        # но не сжигаем единственную попытку из-за инфры.
        logger.exception(
            "BROADCAST_TRIAL_KEY_GRANT_FAILED tg=%s broadcast_id=%s: %s",
            telegram_id, broadcast_id, e,
        )
        await database.release_broadcast_trial_key(broadcast_id, telegram_id)
        try:
            await callback.message.answer(
                i18n_get_text(language, "broadcast.trial_key_error"),
            )
        except Exception:
            pass
        return

    # Сообщение «Подарок активирован» + кнопка подключения.
    activated_text = i18n_get_text(language, "broadcast.trial_key_activated")
    connect_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "broadcast.trial_key_connect_btn"),
            callback_data="connect_instruction",
            style="primary",
        )],
    ])
    try:
        await callback.message.answer(
            activated_text, parse_mode="HTML", reply_markup=connect_kb,
        )
    except Exception as e:
        logger.warning("TRIAL_KEY_ACTIVATED_MSG_FAIL tg=%s: %s", telegram_id, e)

    # Через ~1.5с — экран выбора устройства (fire-and-forget).
    try:
        asyncio.create_task(_send_device_screen_delayed(
            callback.bot, callback.message.chat.id, telegram_id, language,
        ))
    except Exception:
        pass


__all__ = ["broadcast_trial_key_router"]
