"""
Admin: targeted 30%-off promo broadcast for users still on trial.

AUDIENCE
--------
Only users matching BOTH:
  - users.trial_expires_at > NOW       (trial currently running)
  - NO active paid premium subscription (so we don't spam paying users)

Selection is done by database.get_active_trial_telegram_ids() — see
the SQL there.

UX
--
Admin clicks "🎁 Trial → −30%" in the dashboard:
  1. Preview screen: count of trial users + the message template.
  2. Confirm button → background sender starts.
  3. Status button polls progress.

DELIVERY
--------
Reuses broadcast.py's _safe_send_with_buttons() — Telegram-safe rate
limiting (BROADCAST_CONCURRENCY=15, retry on RetryAfter / generic
errors), so we inherit the same proven behaviour as the existing
custom broadcasts.

CTA button under each broadcast message uses callback_data
"broadcast_gift_3m" — the existing flow that opens the 4-tariff gift
screen with 30% pre-applied (Basic 349 ₽, Plus 629 ₽, Combo Basic
594 ₽, Combo Plus 909 ₽). No new payment plumbing required.

CREATIVE TEXT
-------------
The body of the broadcast lives in PROMO_TEXT_HTML at the top of this
file as a placeholder. The admin approves the exact wording, then we
replace it here. NO copy ships until that approval.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

import config
import database
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.admin.broadcast import (
    BROADCAST_CONCURRENCY,
    _safe_send_with_buttons,
)
from app.handlers.common.utils import safe_edit_text

admin_promo_trial_router = Router()
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
# PLACEHOLDER. Approve creative with the admin, then replace.
# Keep PROMO_BUTTON_CALLBACK pointed at the existing 30%/3m flow so
# the user lands in the working tariff screen without new plumbing.
# ────────────────────────────────────────────────────────────────────
PROMO_TEXT_HTML = (
    "🎁 <b>Спец-предложение только для тебя</b>\n\n"
    "<i>[черновик текста — заменим после утверждения]</i>\n\n"
    "Скидка <b>30%</b> на любой тариф. Действует ограниченное время."
)
PROMO_BUTTON_TEXT = "🎁 Активировать скидку 30%"
PROMO_BUTTON_CALLBACK = "broadcast_gift_3m"

# Seconds between live progress edits.
_PROGRESS_INTERVAL = 5

# In-memory state per admin id while a broadcast is running.
_runs: dict[int, dict] = {}


def _make_promo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=PROMO_BUTTON_TEXT,
                              callback_data=PROMO_BUTTON_CALLBACK)],
    ])


async def _sender_worker(admin_id: int, bot, user_ids: list):
    """Background broadcast — reuses broadcast.py's _safe_send_with_buttons
    so rate-limit and retry semantics match the rest of the admin
    broadcasts."""
    state = _runs[admin_id]
    state["total"] = len(user_ids)
    state["sent"] = 0
    state["failed"] = 0

    sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)
    keyboard = _make_promo_keyboard()

    async def _send_one(uid: int):
        msg_id = await _safe_send_with_buttons(
            bot, uid, PROMO_TEXT_HTML, sem,
            reply_markup=keyboard,
        )
        if msg_id is not None:
            state["sent"] += 1
        else:
            state["failed"] += 1
        state["done"] += 1

    state["done"] = 0
    try:
        await asyncio.gather(*[_send_one(uid) for uid in user_ids])
        state["status"] = "done"
        logger.info(
            "PROMO_TRIAL_DONE admin=%s total=%s sent=%s failed=%s",
            admin_id, state["total"], state["sent"], state["failed"],
        )
    except Exception as e:
        state["status"] = "failed"
        state["error"] = f"{type(e).__name__}: {e}"
        logger.exception("PROMO_TRIAL_FATAL admin=%s: %s", admin_id, e)


def _format_progress(state: dict) -> str:
    if state.get("status") == "failed":
        return (
            "🎁 <b>Trial-промо: сбой</b>\n\n"
            f"❌ <code>{state.get('error') or '—'}</code>"
        )
    total = state.get("total", 0)
    done = state.get("done", 0)
    sent = state.get("sent", 0)
    failed = state.get("failed", 0)
    status = state.get("status", "running")
    if status == "running":
        head = "🎁 <b>Trial-промо: отправка идёт</b>"
    else:
        head = "🎁 <b>Trial-промо: завершено</b>"
    return (
        f"{head}\n\n"
        f"Обработано: <b>{done}</b> / {total}\n"
        f"  ✅ Доставлено: {sent}\n"
        f"  ❌ Не доставлено: {failed}"
    )


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial")
async def callback_promo_trial(callback: CallbackQuery):
    """Preview: show audience size + the exact message we'll send,
    plus a confirm button."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    admin_id = callback.from_user.id
    existing = _runs.get(admin_id)
    if existing and existing.get("status") == "running":
        await safe_edit_text(
            callback.message, _format_progress(existing),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:promo_trial_status")],
                [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
            ]),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    try:
        user_ids = await database.get_active_trial_telegram_ids()
    except Exception as e:
        logger.exception("PROMO_TRIAL_AUDIENCE_FAIL: %s", e)
        await safe_edit_text(
            callback.message,
            f"❌ Не удалось собрать аудиторию: <code>{e}</code>",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    # Stash so the confirm step doesn't requery.
    _runs[admin_id] = {
        "status": "ready",
        "audience": user_ids,
    }

    preview = (
        "🎁 <b>Спец-предложение для trial-юзеров</b>\n\n"
        f"Аудитория: <b>{len(user_ids)}</b> пользователей с активным "
        "пробным периодом (без активной paid-подписки).\n\n"
        "<b>Текст рассылки (черновик):</b>\n"
        "─────────────────\n"
        f"{PROMO_TEXT_HTML}\n"
        "─────────────────\n\n"
        "Кнопка под сообщением: "
        f"<i>{PROMO_BUTTON_TEXT}</i> → открывает экран 30%/3 месяца.\n\n"
        "<i>Текст можно поменять перед отправкой — скажи финал, заменю в коде.</i>"
    )

    rows = []
    if user_ids:
        rows.append([InlineKeyboardButton(
            text=f"📤 Отправить ({len(user_ids)})",
            callback_data="admin:promo_trial_confirm",
        )])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])

    await safe_edit_text(
        callback.message, preview,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial_confirm")
async def callback_promo_trial_confirm(callback: CallbackQuery):
    """Confirm + kick off the background broadcast."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    admin_id = callback.from_user.id
    state = _runs.get(admin_id)
    if not state or state.get("status") not in ("ready",):
        await safe_edit_text(
            callback.message,
            "🎁 Сначала откройте превью предложения.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    user_ids = state.get("audience") or []
    if not user_ids:
        await safe_edit_text(
            callback.message,
            "🎁 Аудитория пуста — никому отправлять.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    state["status"] = "running"
    state["started_at"] = datetime.now(timezone.utc)
    asyncio.create_task(_sender_worker(admin_id, callback.bot, list(user_ids)))

    await safe_edit_text(
        callback.message,
        "🎁 <b>Trial-промо отправляется в фоне</b>\n\n"
        f"Аудитория: <b>{len(user_ids)}</b>.\n"
        "Кнопка «🔄 Обновить» — текущий прогресс и финальный отчёт.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:promo_trial_status")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial_status")
async def callback_promo_trial_status(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    state = _runs.get(callback.from_user.id)
    if not state:
        await safe_edit_text(
            callback.message,
            "🎁 Нет активной рассылки.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    text = _format_progress(state)
    if state.get("status") == "running":
        rows = [
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:promo_trial_status")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text="🎁 Запустить ещё раз", callback_data="admin:promo_trial")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]
    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )
