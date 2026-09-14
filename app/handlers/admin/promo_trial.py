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
  2. Optional: attach a photo — admin sends a picture in this chat, it
     gets stored and prepended to every broadcast message.
  3. Confirm button → background sender starts.
  4. Status button polls progress.

DELIVERY
--------
Reuses broadcast.py's _safe_send_with_buttons() — Telegram-safe rate
limiting (BROADCAST_CONCURRENCY=15, retry on RetryAfter / generic
errors), so we inherit the same proven behaviour as the existing
custom broadcasts. Photo path uses bot.send_photo with caption when
photo_file_id is set.

CTA button under each broadcast message — "🎁 Забрать подарок" —
writes a 24-hour personal_discount=30% row via
database.create_user_discount, then opens the standard tariff screen.
The discount is automatically applied by calculate_final_price's
personal_discount branch, so it works for ANY tariff (basic / plus /
combo / biz) and ANY period (1/3/6/12 mo). No tariff-specific plumbing.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
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


class PromoTrialFSM(StatesGroup):
    waiting_for_photo = State()


# Approved creative (variant 3 — clubby tone) with the tg-emoji IDs
# the admin verified from Telegram Ads. Plain emoji fallback inside
# each <tg-emoji> tag means non-premium clients still see a glyph.
PROMO_TEXT_HTML = (
    "<tg-emoji emoji-id=\"5438496463044752972\">⭐️</tg-emoji> "
    "<b>Персональное предложение</b>\n\n"
    "Ты попал в небольшой список пользователей, которым мы открыли "
    "доступ к скидке <b>−30%</b> на всю линейку Atlas.\n\n"
    "<blockquote><b>Условия простые:</b>\n"
    "— скидка действует <b>24 часа</b> после активации\n"
    "— применяется к <b>любому тарифу</b> (Basic, Plus, Combo)\n"
    "— на <b>любой срок</b> подписки</blockquote>\n\n"
    "<tg-emoji emoji-id=\"5359527944505041421\">🎁</tg-emoji> Жми кнопку "
    "ниже — забираем подарок и выбираем тариф. Цена со скидкой "
    "подставится автоматически."
)
PROMO_BUTTON_TEXT = "🎁 Забрать подарок"
PROMO_BUTTON_CALLBACK = "promo_trial_claim"
PROMO_DISCOUNT_PERCENT = 30
PROMO_DISCOUNT_HOURS = 24

# In-memory state per admin id. Fields:
#   status:        "ready" | "running" | "done" | "failed"
#   audience:      list[int]
#   photo_file_id: Optional[str]  — Telegram file_id of attached photo
#   total/sent/failed/done/error/started_at — set by sender_worker
_runs: dict[int, dict] = {}


def _make_promo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=PROMO_BUTTON_TEXT,
                              callback_data=PROMO_BUTTON_CALLBACK)],
    ])


async def _sender_worker(
    admin_id: int, bot, user_ids: list,
    photo_file_id: str | None = None,
):
    """Background broadcast — same delivery model as the custom-broadcast
    pipeline in broadcast.py:
      - Semaphore(BROADCAST_CONCURRENCY=15) — Telegram-safe 30 msg/s
      - Batches of BROADCAST_BATCH_SIZE=200, BROADCAST_BATCH_PAUSE=2s
        between batches, so a hot loop never piles tasks into memory
      - return_exceptions=True per batch, so one user's failure can't
        cancel its siblings
      - Per-user retry (RetryAfter / generic) lives inside
        _safe_send_with_buttons — inherited automatically.
      - When photo_file_id is set, each delivery is bot.send_photo with
        PROMO_TEXT_HTML as the caption.
    """
    state = _runs[admin_id]
    state["total"] = len(user_ids)
    state["sent"] = 0
    state["failed"] = 0
    state["done"] = 0

    sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)
    keyboard = _make_promo_keyboard()

    async def _send_one(uid: int):
        if photo_file_id:
            msg_id = await _safe_send_with_buttons(
                bot, uid, PROMO_TEXT_HTML, sem,
                reply_markup=keyboard,
                photo_file_id=photo_file_id,
                caption=PROMO_TEXT_HTML,
            )
        else:
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
            "PROMO_TRIAL_DONE admin=%s total=%s sent=%s failed=%s photo=%s",
            admin_id, state["total"], state["sent"], state["failed"],
            bool(photo_file_id),
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


def _preview_text(audience_count: int, photo_attached: bool) -> str:
    photo_line = (
        "📸 Фото: <b>прикреплено</b> — придёт у каждого как фото с подписью.\n"
        if photo_attached else
        "📸 Фото: <i>не прикреплено</i> — будет обычное текстовое сообщение.\n"
    )
    return (
        "🎁 <b>Спец-предложение для trial-юзеров</b>\n\n"
        f"Аудитория: <b>{audience_count}</b> пользователей с активным "
        "пробным периодом (без активной paid-подписки).\n"
        f"{photo_line}\n"
        "<b>Текст рассылки:</b>\n"
        "─────────────────\n"
        f"{PROMO_TEXT_HTML}\n"
        "─────────────────\n\n"
        "Кнопка под сообщением: "
        f"<i>{PROMO_BUTTON_TEXT}</i> → активирует персональную скидку "
        f"<b>{PROMO_DISCOUNT_PERCENT}%</b> на <b>{PROMO_DISCOUNT_HOURS} часов</b> "
        "и открывает экран тарифов. Скидка применяется к любому тарифу "
        "(Basic / Plus / Combo) на любой срок."
    )


def _preview_keyboard(
    audience_count: int, photo_attached: bool
) -> InlineKeyboardMarkup:
    rows = []
    if audience_count:
        rows.append([InlineKeyboardButton(
            text=f"📤 Отправить ({audience_count})",
            callback_data="admin:promo_trial_confirm",
        )])
    if photo_attached:
        rows.append([
            InlineKeyboardButton(
                text="🖼 Заменить фото",
                callback_data="admin:promo_trial_add_photo",
            ),
            InlineKeyboardButton(
                text="❌ Убрать фото",
                callback_data="admin:promo_trial_remove_photo",
            ),
        ])
    else:
        rows.append([InlineKeyboardButton(
            text="📸 Добавить фото",
            callback_data="admin:promo_trial_add_photo",
        )])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_preview(callback: CallbackQuery, admin_id: int):
    """Re-render the preview screen from the current _runs state, with
    edit-then-fallback-send so a parse error or photo-source dashboard
    can't swallow the response."""
    state = _runs.get(admin_id) or {}
    audience = state.get("audience") or []
    photo_file_id = state.get("photo_file_id")
    text = _preview_text(len(audience), bool(photo_file_id))
    keyboard = _preview_keyboard(len(audience), bool(photo_file_id))

    edited_ok = False
    try:
        await safe_edit_text(
            callback.message, text,
            reply_markup=keyboard,
            bot=callback.bot, parse_mode="HTML",
        )
        edited_ok = True
    except Exception as e:
        logger.exception("PROMO_TRIAL_EDIT_FAIL admin=%s: %s", admin_id, e)

    if not edited_ok:
        try:
            await callback.message.answer(
                text, reply_markup=keyboard, parse_mode="HTML",
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


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial")
async def callback_promo_trial(callback: CallbackQuery, state: FSMContext):
    """Preview: show audience size + the exact message we'll send,
    plus confirm / photo buttons."""
    logger.info("PROMO_TRIAL_OPEN admin=%s data=%s",
                callback.from_user.id, callback.data)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await state.clear()

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
        return

    # Preserve a previously-attached photo across re-openings within
    # this admin session.
    prev_photo = (_runs.get(admin_id) or {}).get("photo_file_id")
    _runs[admin_id] = {
        "status": "ready",
        "audience": user_ids,
        "photo_file_id": prev_photo,
    }

    await _render_preview(callback, admin_id)


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial_add_photo")
async def callback_promo_trial_add_photo(callback: CallbackQuery, state: FSMContext):
    """Switch FSM to waiting_for_photo and prompt the admin to send a
    picture. Photo arrives in the message handler below."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    admin_id = callback.from_user.id
    if admin_id not in _runs:
        await callback.message.answer(
            "🎁 Сначала откройте превью из админ-панели.",
            parse_mode="HTML",
        )
        return

    await state.set_state(PromoTrialFSM.waiting_for_photo)
    try:
        await callback.message.answer(
            "📸 <b>Пришли фото одним сообщением.</b>\n\n"
            "Картинка прикрепится к рассылке как фото с подписью "
            "(сам текст останется неизменным).\n\n"
            "Если передумал — нажми «◀ Назад» в превью.",
            parse_mode="HTML",
        )
    except Exception:
        pass


@admin_promo_trial_router.message(
    PromoTrialFSM.waiting_for_photo, F.photo,
)
async def message_promo_trial_photo(message: Message, state: FSMContext):
    """Admin sent a photo while we were waiting for one — store the
    largest size's file_id and re-render the preview."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return

    admin_id = message.from_user.id
    file_id = message.photo[-1].file_id
    run = _runs.get(admin_id)
    if not run:
        await state.clear()
        await message.answer(
            "🎁 Сессия превью утеряна — открой меню заново.",
            parse_mode="HTML",
        )
        return

    run["photo_file_id"] = file_id
    await state.clear()
    logger.info(
        "PROMO_TRIAL_PHOTO_ATTACHED admin=%s file_id=%s", admin_id, file_id,
    )

    audience = run.get("audience") or []
    text = _preview_text(len(audience), True)
    keyboard = _preview_keyboard(len(audience), True)
    try:
        await message.answer_photo(
            photo=file_id, caption=text,
            reply_markup=keyboard, parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("PROMO_TRIAL_PHOTO_PREVIEW_FAIL admin=%s: %s", admin_id, e)
        await message.answer(
            f"✅ Фото прикреплено, но превью не отрисовалось: "
            f"<code>{type(e).__name__}: {e}</code>\n\n"
            "Открой «🎁 Trial → промо −30%» из админ-меню — там покажется "
            "обновлённое превью.",
            parse_mode="HTML",
        )


@admin_promo_trial_router.message(
    PromoTrialFSM.waiting_for_photo,
)
async def message_promo_trial_photo_other(message: Message, state: FSMContext):
    """Anything other than a photo while waiting — gentle nudge, keep
    the state alive so the admin can retry."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    try:
        await message.answer(
            "📸 Жду <b>фотографию</b> — пришли картинку одним сообщением, "
            "или вернись в превью кнопкой «◀ Назад».",
            parse_mode="HTML",
        )
    except Exception:
        pass


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial_remove_photo")
async def callback_promo_trial_remove_photo(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer("Фото убрано")
    except Exception:
        pass
    await state.clear()

    admin_id = callback.from_user.id
    run = _runs.get(admin_id)
    if run:
        run["photo_file_id"] = None
        logger.info("PROMO_TRIAL_PHOTO_REMOVED admin=%s", admin_id)
    await _render_preview(callback, admin_id)


@admin_promo_trial_router.callback_query(F.data == "admin:promo_trial_confirm")
async def callback_promo_trial_confirm(callback: CallbackQuery, state: FSMContext):
    """Confirm + kick off the background broadcast."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await state.clear()

    admin_id = callback.from_user.id
    run = _runs.get(admin_id)
    if not run or run.get("status") not in ("ready",):
        await safe_edit_text(
            callback.message,
            "🎁 Сначала откройте превью предложения.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    user_ids = run.get("audience") or []
    if not user_ids:
        await safe_edit_text(
            callback.message,
            "🎁 Аудитория пуста — никому отправлять.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    photo_file_id = run.get("photo_file_id")
    run["status"] = "running"
    run["started_at"] = datetime.now(timezone.utc)
    asyncio.create_task(_sender_worker(
        admin_id, callback.bot, list(user_ids),
        photo_file_id=photo_file_id,
    ))

    photo_note = "с фото" if photo_file_id else "без фото"
    await safe_edit_text(
        callback.message,
        "🎁 <b>Trial-промо отправляется в фоне</b>\n\n"
        f"Аудитория: <b>{len(user_ids)}</b> ({photo_note}).\n"
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

    run = _runs.get(callback.from_user.id)
    if not run:
        await safe_edit_text(
            callback.message,
            "🎁 Нет активной рассылки.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    text = _format_progress(run)
    if run.get("status") == "running":
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
