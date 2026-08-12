"""
Admin bulk bonus distribution.

Two bonus types — bypass-traffic GB and subscription days — applied to a
chosen user segment (active subscriptions / no subscription / all users).

Implementation reuses existing primitives:
  - remnawave_service.add_bypass_traffic — handles both existing and
    missing Remnawave users for the traffic bonus.
  - database.grant_access — single source of truth for granting or
    extending a subscription for the days bonus.

Distribution runs as a background asyncio.Task so the admin's callback
returns immediately and Telegram never times out. A module-level flag
prevents two concurrent runs from interleaving (would otherwise double-
notify and race on Remnawave updates).
"""
import asyncio
import logging
from datetime import timedelta

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

import config
import database
from app.handlers.common.utils import safe_edit_text
from app.utils.telegram_safe import safe_send_message
from app.services import remnawave_service

admin_bonus_router = Router()
logger = logging.getLogger(__name__)


class AdminBonusDistribute(StatesGroup):
    waiting_for_custom_amount = State()


_TRAFFIC_PRESETS_GB = [1, 3, 5, 10, 20, 50]
_DAYS_PRESETS = [1, 3, 7, 14, 30]
_MAX_TRAFFIC_GB = 1000
_MAX_DAYS = 365

# Batch tuning for bulk distribution.  Smaller batches + a between-batch
# pause keep Remnawave and Telegram from refusing or 429-ing us on big
# segments (1000+ users).  Tuned conservatively — admins are not in a
# hurry, infrastructure is.
_BONUS_CONCURRENCY = 5      # parallel Remnawave + Telegram calls per batch
_BONUS_BATCH_SIZE = 40      # users per batch
_BONUS_BATCH_PAUSE_SEC = 3  # cooldown between batches

# Single-flight guard so two admin clicks can't interleave.
_bonus_active = False


def _type_label(t: str) -> str:
    return "ГБ обхода" if t == "t" else "дней подписки"


def _seg_label(s: str) -> str:
    return {
        "act": "Активные подписки",
        "nos": "Без подписки",
        "all": "Все пользователи",
    }.get(s, s)


def _seg_to_full(s: str) -> str:
    return {
        "act": "active_subscriptions",
        "nos": "no_subscription",
        "all": "all_users",
    }.get(s, s)


def _days_word(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if 11 <= n100 <= 14:
        return "дней"
    if n10 == 1:
        return "день"
    if 2 <= n10 <= 4:
        return "дня"
    return "дней"


def _gift_text(t: str, amount: int) -> str:
    if t == "t":
        gift = f"<b>+{amount} ГБ обхода блокировок</b>"
    else:
        gift = f"<b>подписка на {amount} {_days_word(amount)}</b>"
    return (
        "А мы с подарком! 😍\n\n"
        f"Для тебя — {gift}.\n\n"
        "Спасибо, что остаёшься с Atlas Secure 🤍"
    )


def _gift_keyboard() -> InlineKeyboardMarkup:
    """Buy-with-20%-discount CTA attached to every gift notification."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Купить со скидкой 20%",
            callback_data="gift_offer:claim",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )],
    ])


# ── Screens ──────────────────────────────────────────────────────────────

@admin_bonus_router.callback_query(F.data == "admin:bonus")
async def callback_admin_bonus_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    text = "🎁 <b>Выдача бонуса</b>\n\nВыберите тип:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 ГБ обхода", callback_data="admin:bonus:type:t")],
        [InlineKeyboardButton(text="📅 Дни подписки", callback_data="admin:bonus:type:d")],
        [InlineKeyboardButton(text="← Назад", callback_data="admin:main")],
    ])
    await safe_edit_text(
        callback.message, text, reply_markup=kb,
        bot=callback.bot, parse_mode="HTML",
    )


@admin_bonus_router.callback_query(F.data.startswith("admin:bonus:type:"))
async def callback_admin_bonus_type(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    t = callback.data.rsplit(":", 1)[-1]
    if t not in ("t", "d"):
        return
    presets = _TRAFFIC_PRESETS_GB if t == "t" else _DAYS_PRESETS
    unit = "ГБ" if t == "t" else "д."
    rows, row = [], []
    for p in presets:
        row.append(InlineKeyboardButton(
            text=f"{p} {unit}",
            callback_data=f"admin:bonus:amt:{t}:{p}",
        ))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="✏️ Указать вручную",
        callback_data=f"admin:bonus:amt:{t}:c",
    )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="admin:bonus")])
    text = f"🎁 <b>Бонус: {_type_label(t)}</b>\n\nСколько выдать каждому?"
    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_bonus_router.callback_query(F.data.startswith("admin:bonus:amt:"))
async def callback_admin_bonus_amount(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) < 5:
        return
    t = parts[3]
    raw = parts[4]
    if t not in ("t", "d"):
        return
    if raw == "c":
        await state.set_state(AdminBonusDistribute.waiting_for_custom_amount)
        await state.update_data(bonus_type=t)
        await callback.answer()
        await callback.message.answer(
            f"Введите число — сколько {_type_label(t)} выдать каждому "
            f"(1..{_MAX_TRAFFIC_GB if t == 't' else _MAX_DAYS})."
        )
        return
    try:
        amount = int(raw)
    except ValueError:
        await callback.answer("Неверное число", show_alert=True)
        return
    cap = _MAX_TRAFFIC_GB if t == "t" else _MAX_DAYS
    if amount <= 0 or amount > cap:
        await callback.answer(f"Допустимо: 1..{cap}", show_alert=True)
        return
    await callback.answer()
    await _show_segment_screen_edit(callback.message, callback.bot, t, amount)


@admin_bonus_router.message(StateFilter(AdminBonusDistribute.waiting_for_custom_amount))
async def message_admin_bonus_custom_amount(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    data = await state.get_data()
    t = data.get("bonus_type")
    if t not in ("t", "d"):
        await state.clear()
        return
    try:
        amount = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    cap = _MAX_TRAFFIC_GB if t == "t" else _MAX_DAYS
    if amount <= 0 or amount > cap:
        await message.answer(f"Допустимо: 1..{cap}.")
        return
    await state.clear()
    await _show_segment_screen_new(message, message.bot, t, amount)


def _segment_keyboard(t: str, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Активные подписки",
                              callback_data=f"admin:bonus:seg:{t}:{amount}:act")],
        [InlineKeyboardButton(text="🚫 Без подписки",
                              callback_data=f"admin:bonus:seg:{t}:{amount}:nos")],
        [InlineKeyboardButton(text="👥 Все пользователи",
                              callback_data=f"admin:bonus:seg:{t}:{amount}:all")],
        [InlineKeyboardButton(text="← Назад",
                              callback_data=f"admin:bonus:type:{t}")],
    ])


async def _show_segment_screen_edit(msg, bot, t: str, amount: int):
    text = f"🎁 <b>Бонус: +{amount} {_type_label(t)}</b>\n\nКому выдать?"
    await safe_edit_text(
        msg, text, reply_markup=_segment_keyboard(t, amount),
        bot=bot, parse_mode="HTML",
    )


async def _show_segment_screen_new(msg, bot, t: str, amount: int):
    text = f"🎁 <b>Бонус: +{amount} {_type_label(t)}</b>\n\nКому выдать?"
    await msg.answer(text, reply_markup=_segment_keyboard(t, amount), parse_mode="HTML")


@admin_bonus_router.callback_query(F.data.startswith("admin:bonus:seg:"))
async def callback_admin_bonus_seg(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) != 6:
        return
    _, _, _, t, amt, s = parts
    if t not in ("t", "d") or s not in ("act", "nos", "all"):
        return
    try:
        amount = int(amt)
    except ValueError:
        return
    try:
        user_ids = await database.get_users_by_segment(_seg_to_full(s))
    except Exception as e:
        logger.exception(f"BONUS_SEG_FETCH_FAIL: {e}")
        await callback.message.answer("Не удалось получить список пользователей.")
        return
    total = len(user_ids)
    text = (
        "🎁 <b>Подтверждение</b>\n\n"
        f"Бонус: <b>+{amount} {_type_label(t)}</b>\n"
        f"Сегмент: <b>{_seg_label(s)}</b>\n"
        f"Затронет: <b>{total}</b> пользователей\n\n"
        "<i>Раздача идёт в фоне; каждому получателю придёт уведомление о подарке.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выдать",
                              callback_data=f"admin:bonus:go:{t}:{amount}:{s}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bonus")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb,
                         bot=callback.bot, parse_mode="HTML")


@admin_bonus_router.callback_query(F.data.startswith("admin:bonus:go:"))
async def callback_admin_bonus_go(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    global _bonus_active
    if _bonus_active:
        await callback.answer("Уже идёт раздача — дождитесь окончания.", show_alert=True)
        return
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) != 6:
        return
    _, _, _, t, amt, s = parts
    if t not in ("t", "d") or s not in ("act", "nos", "all"):
        return
    try:
        amount = int(amt)
    except ValueError:
        return
    try:
        user_ids = await database.get_users_by_segment(_seg_to_full(s))
    except Exception as e:
        logger.exception(f"BONUS_FETCH_FAIL: {e}")
        await callback.message.answer("Не удалось получить список пользователей.")
        return
    total = len(user_ids)
    if total == 0:
        await safe_edit_text(
            callback.message, "Сегмент пуст — раздавать некому.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Назад", callback_data="admin:bonus")],
            ]),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    _bonus_active = True
    try:
        await safe_edit_text(
            callback.message,
            (f"🎁 <b>Раздача запущена</b>\n\n"
             f"Бонус: +{amount} {_type_label(t)}\n"
             f"Сегмент: {_seg_label(s)}\n"
             f"Всего: {total}\n\n"
             f"<i>Идёт в фоне, можно закрыть этот экран.</i>"),
            reply_markup=None, bot=callback.bot, parse_mode="HTML",
        )
    except Exception:
        pass

    asyncio.create_task(_run_bonus_distribution(
        bot=callback.bot,
        admin_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        msg_id=callback.message.message_id,
        bonus_type=t, amount=amount, segment=s,
        user_ids=user_ids,
    ))


# ── Background distribution ───────────────────────────────────────────────

async def _run_bonus_distribution(*, bot, admin_id, chat_id, msg_id,
                                   bonus_type, amount, segment, user_ids):
    global _bonus_active
    try:
        total = len(user_ids)
        sem = asyncio.Semaphore(_BONUS_CONCURRENCY)
        stats = {"ok": 0, "fail": 0, "notified": 0, "done": 0}

        async def _apply_one(uid: int):
            async with sem:
                try:
                    ok = await _apply_bonus(uid, bonus_type, amount)
                except Exception as e:
                    logger.error(f"BONUS_APPLY_FAIL user={uid}: {e}")
                    ok = False
                if ok:
                    stats["ok"] += 1
                    try:
                        sent = await safe_send_message(
                            bot, uid, _gift_text(bonus_type, amount),
                            reply_markup=_gift_keyboard(), parse_mode="HTML",
                        )
                        if sent is not None:
                            stats["notified"] += 1
                    except Exception:
                        pass
                else:
                    stats["fail"] += 1
                stats["done"] += 1

        batch_count = (total + _BONUS_BATCH_SIZE - 1) // _BONUS_BATCH_SIZE
        for batch_idx, i in enumerate(range(0, total, _BONUS_BATCH_SIZE)):
            batch = user_ids[i:i + _BONUS_BATCH_SIZE]
            await asyncio.gather(*(_apply_one(uid) for uid in batch), return_exceptions=True)
            is_last_batch = (batch_idx + 1) >= batch_count
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=(
                        f"🎁 <b>Раздача в процессе…</b>\n\n"
                        f"Бонус: +{amount} {_type_label(bonus_type)}\n"
                        f"Прогресс: {stats['done']}/{total}\n"
                        f"✅ Выдано: {stats['ok']}\n"
                        f"❌ Не удалось: {stats['fail']}\n"
                        f"📨 Уведомлено: {stats['notified']}\n"
                        f"⏳ Батч {batch_idx + 1}/{batch_count}"
                        + ("" if is_last_batch else f" · пауза {_BONUS_BATCH_PAUSE_SEC} с")
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            # Cool-down between batches so Remnawave / Telegram don't time us out.
            if not is_last_batch:
                await asyncio.sleep(_BONUS_BATCH_PAUSE_SEC)

        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=(
                    f"✅ <b>Раздача завершена</b>\n\n"
                    f"Бонус: +{amount} {_type_label(bonus_type)}\n"
                    f"Сегмент: {_seg_label(segment)}\n"
                    f"Обработано: {stats['done']}\n"
                    f"✅ Выдано: {stats['ok']}\n"
                    f"❌ Не удалось: {stats['fail']}\n"
                    f"📨 Уведомлено: {stats['notified']}"
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="← В админку", callback_data="admin:main")],
                ]),
                parse_mode="HTML",
            )
        except Exception:
            pass

        try:
            await database._log_audit_event_atomic_standalone(
                "admin_bonus_distribute", admin_id, None,
                f"type={bonus_type} amount={amount} segment={segment} "
                f"ok={stats['ok']} fail={stats['fail']} notified={stats['notified']} total={total}",
            )
        except Exception as e:
            logger.warning(f"BONUS_AUDIT_FAIL: {e}")

        logger.info(
            f"BONUS_DISTRIBUTED type={bonus_type} amount={amount} segment={segment} "
            f"ok={stats['ok']} fail={stats['fail']} notified={stats['notified']} total={total}"
        )
    finally:
        _bonus_active = False


async def _apply_bonus(telegram_id: int, bonus_type: str, amount: int) -> bool:
    if bonus_type == "t":
        return await _apply_traffic_bonus(telegram_id, amount)
    if bonus_type == "d":
        return await _apply_days_bonus(telegram_id, amount)
    return False


async def _apply_traffic_bonus(telegram_id: int, gb: int) -> bool:
    """Add bypass-traffic GB. Provisions a bypass-only subscription row +
    fresh Remnawave user if the recipient has neither."""
    sub = await database.get_subscription(telegram_id)
    if sub is None:
        try:
            await database.ensure_bypass_only_subscription(telegram_id)
        except Exception as e:
            logger.error(f"BONUS_ENSURE_BYPASS_FAIL user={telegram_id}: {e}")
            return False
    bytes_ = gb * 1024 ** 3
    return bool(await remnawave_service.add_bypass_traffic(
        telegram_id, bytes_, "basic", period_days=30,
    ))


async def _apply_days_bonus(telegram_id: int, days: int) -> bool:
    """Grant or extend a basic subscription by N days. grant_access is the
    single source of truth — extends for active subs, creates new + VPN key
    for users without one."""
    from database.subscriptions import grant_access
    try:
        await grant_access(
            telegram_id=telegram_id,
            duration=timedelta(days=days),
            source="admin_bonus",
            admin_telegram_id=config.ADMIN_TELEGRAM_ID,
            admin_grant_days=days,
        )
        return True
    except Exception as e:
        logger.error(f"BONUS_GRANT_ACCESS_FAIL user={telegram_id}: {e}")
        return False
