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

CTA button under each broadcast message — "🎁 Забрать подарок" —
writes a 24-hour personal_discount=30% row via
database.create_user_discount, then opens the standard tariff screen.
The discount is automatically applied by calculate_final_price's
personal_discount branch, so it works for ANY tariff (basic / plus /
combo / biz) and ANY period (1/3/6/12 mo). No tariff-specific plumbing.

CREATIVE TEXT
-------------
The body of the broadcast lives in PROMO_TEXT_HTML at the top of this
file as a placeholder. The admin approves the exact wording, then we
replace it here.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

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
    BROADCAST_BATCH_SIZE,
    BROADCAST_BATCH_PAUSE,
    _safe_send_with_buttons,
)
from app.handlers.common.utils import safe_edit_text

admin_promo_trial_router = Router()
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
# PLACEHOLDER. Approve creative with the admin, then replace.
# ────────────────────────────────────────────────────────────────────
PROMO_TEXT_HTML = (
    "<tg-emoji emoji-id=\"5435957704932179430\">⭐️</tg-emoji> "
    "<b>Персональное предложение</b>\n\n"
    "Ты попал в небольшой список пользователей, которым мы открыли "
    "доступ к скидке <b>−30%</b> на всю линейку Atlas.\n\n"
    "<b>Условия простые:</b>\n"
    "— скидка действует <b>24 часа</b> после активации\n"
    "— применяется к <b>любому тарифу</b> (Basic, Plus, Combo)\n"
    "— на <b>любой срок</b> подписки\n\n"
    "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Жми кнопку "
    "ниже — забираем подарок и выбираем тариф. Цена со скидкой "
    "подставится автоматически."
)
PROMO_BUTTON_TEXT = "🎁 Забрать подарок"
PROMO_BUTTON_CALLBACK = "promo_trial_claim"
# How long the discount stays active once the user claims it.
PROMO_DISCOUNT_PERCENT = 30
PROMO_DISCOUNT_HOURS = 24

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
    """Background broadcast — same delivery model as the custom-broadcast
    pipeline in broadcast.py:
      - Semaphore(BROADCAST_CONCURRENCY=15) — Telegram-safe 30 msg/s
      - Batches of BROADCAST_BATCH_SIZE=200, BROADCAST_BATCH_PAUSE=2s
        between batches, so a hot loop never piles tasks into memory
      - return_exceptions=True per batch, so one user's failure can't
        cancel its siblings
      - Per-user retry (RetryAfter / generic) lives inside
        _safe_send_with_buttons — inherited automatically.
    """
    state = _runs[admin_id]
    state["total"] = len(user_ids)
    state["sent"] = 0
    state["failed"] = 0
    state["done"] = 0

    sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)
    keyboard = _make_promo_keyboard()

    async def _send_one(uid: int):
        msg_id = await _safe_send_with_buttons(
            bot, uid, PROMO_TEXT_HTML, sem,
            reply_markup=keyboard,
        )
        return uid, msg_id

    try:
        total = len(user_ids)
        for i in range(0, total, BROADCAST_BATCH_SIZE):
            batch = user_ids[i:i + BROADCAST_BATCH_SIZE]
            results = await asyncio.gather(
                *[_send_one(uid) for uid in batch],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    state["failed"] += 1
                    logger.warning("PROMO_TRIAL_TASK_ERROR admin=%s err=%s",
                                   admin_id, r)
                else:
                    _uid, msg_id = r
                    if msg_id is not None:
                        state["sent"] += 1
                    else:
                        state["failed"] += 1
                state["done"] += 1
            logger.info(
                "PROMO_TRIAL_PROGRESS admin=%s done=%s/%s sent=%s failed=%s",
                admin_id, state["done"], total, state["sent"], state["failed"],
            )
            if i + BROADCAST_BATCH_SIZE < total:
                await asyncio.sleep(BROADCAST_BATCH_PAUSE)

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
    logger.info("PROMO_TRIAL_OPEN admin=%s data=%s",
                callback.from_user.id, callback.data)
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
        logger.info("PROMO_TRIAL_AUDIENCE_OK admin=%s count=%s",
                    admin_id, len(user_ids))
    except Exception as e:
        logger.exception("PROMO_TRIAL_AUDIENCE_FAIL: %s", e)
        try:
            await callback.message.answer(
                f"❌ Не удалось собрать аудиторию: <code>{type(e).__name__}: {e}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        try:
            await safe_edit_text(
                callback.message,
                f"❌ Не удалось собрать аудиторию: <code>{type(e).__name__}: {e}</code>",
                reply_markup=get_admin_back_keyboard(),
                bot=callback.bot, parse_mode="HTML",
            )
        except Exception:
            pass
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
        f"<i>{PROMO_BUTTON_TEXT}</i> → активирует персональную скидку "
        f"<b>{PROMO_DISCOUNT_PERCENT}%</b> на <b>{PROMO_DISCOUNT_HOURS} часов</b> "
        "и открывает экран тарифов. Скидка применяется к любому тарифу "
        "(Basic / Plus / Combo) на любой срок.\n\n"
        "<i>Текст можно поменять перед отправкой — скажи финал, заменю в коде.</i>"
    )

    rows = []
    if user_ids:
        rows.append([InlineKeyboardButton(
            text=f"📤 Отправить ({len(user_ids)})",
            callback_data="admin:promo_trial_confirm",
        )])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    # Edit-then-fallback-send: if the dashboard message can't be edited
    # (photo source / inaccessible / parse error swallowed inside
    # safe_edit_text), the admin must STILL see the preview — send a
    # fresh message as a backup.
    edited_ok = False
    try:
        await safe_edit_text(
            callback.message, preview,
            reply_markup=keyboard,
            bot=callback.bot, parse_mode="HTML",
        )
        edited_ok = True
    except Exception as e:
        logger.exception("PROMO_TRIAL_EDIT_FAIL admin=%s: %s", admin_id, e)

    if not edited_ok:
        try:
            await callback.message.answer(
                preview, reply_markup=keyboard, parse_mode="HTML",
            )
        except Exception as e:
            logger.exception("PROMO_TRIAL_ANSWER_FAIL admin=%s: %s", admin_id, e)
            try:
                await callback.message.answer(
                    f"❌ Не удалось показать превью: <code>{type(e).__name__}: {e}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass


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


# ────────────────────────────────────────────────────────────────────
# USER-FACING: "🎁 Забрать подарок" callback under every broadcast
# message. Activates the 24h × 30% personal discount and routes the
# user into the standard tariff screen — the existing pricing flow
# applies the discount automatically.
# ────────────────────────────────────────────────────────────────────
@admin_promo_trial_router.callback_query(F.data == "promo_trial_claim")
async def callback_promo_trial_claim(callback: CallbackQuery, state):
    """Activate 30%/24h personal discount + open the standard tariff screen.

    Behaviour:
      - Always refreshes expires_at to NOW + PROMO_DISCOUNT_HOURS so the
        user gets a fresh 24h window on every click.
      - Never downgrades: discount_percent = max(existing_percent, 30).
        If the user already has a 40% deal, they keep 40%; otherwise
        they get 30%.
      - calculate_final_price's personal_discount branch applies it
        automatically across basic / plus / combo / biz at any period —
        no tariff-specific code path needed.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        existing = await database.get_user_discount(telegram_id)
    except Exception as e:
        logger.exception("PROMO_TRIAL_CLAIM_LOOKUP_FAIL user=%s %s", telegram_id, e)
        existing = None

    existing_pct = int((existing or {}).get("discount_percent") or 0)
    final_pct = max(existing_pct, PROMO_DISCOUNT_PERCENT)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=PROMO_DISCOUNT_HOURS)

    try:
        ok = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=final_pct,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )
    except Exception as e:
        logger.exception("PROMO_TRIAL_CLAIM_CREATE_FAIL user=%s %s", telegram_id, e)
        ok = False

    if not ok:
        await callback.answer(
            "Не удалось активировать скидку. Попробуйте позже или напишите в поддержку.",
            show_alert=True,
        )
        return

    logger.info(
        "PROMO_TRIAL_CLAIM_OK user=%s existing_pct=%s applied_pct=%s hours=%s expires=%s",
        telegram_id, existing_pct, final_pct, PROMO_DISCOUNT_HOURS,
        expires_at.isoformat(),
    )

    try:
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state)
    except Exception as e:
        logger.warning("PROMO_TRIAL_CLAIM_SHOW_TARIFFS_FAIL user=%s %s", telegram_id, e)

    try:
        await callback.message.answer(
            f"🎁 <b>Скидка {final_pct}% активирована!</b>\n\n"
            f"Действует {PROMO_DISCOUNT_HOURS} часа — выберите <b>любой тариф</b> "
            "(Basic, Plus, Combo) на любой срок, и скидка применится автоматически "
            "при оплате.",
            parse_mode="HTML",
        )
    except Exception:
        pass
