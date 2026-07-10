"""
User lookup + admin actions.

Read endpoints just proxy database.* functions. Write endpoints route
through the SAME atomic helpers the in-bot admin handlers use
(`admin_grant_access_atomic`, `admin_revoke_access_atomic`, etc.) —
so audit logs, Remnawave sync, and side effects stay identical no
matter whether the action comes from a Telegram chat or the web UI.

Bot-only writes (approve_payment_atomic, grant_access, finalize_purchase,
mark_trial_used) are intentionally NOT exposed here.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

import config
import database
from app.api.dashboard.deps import require_admin
from app.events import bus

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize_match(row: dict) -> dict:
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.get("/search")
async def users_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(25, gt=0, le=100),
):
    """Substring search across the whole users table.

    Matches telegram_id (digits typed anywhere — as text, so prefixes
    like "123" find tg:1234567890) and username (case-insensitive
    substring, leading @ stripped). Returns up to `limit` ranked
    matches as `{matches: [...], total: N}`.

    Empty result returns 200 with `matches: []` rather than 404 so the
    UI can render "ничего не нашлось" without an exception path.
    """
    try:
        rows = await database.search_users_dashboard(q, limit=limit)
    except Exception as e:
        raise HTTPException(500, f"search_failed: {e}")
    return {
        "query": q.strip(),
        "matches": [_serialize_match(r) for r in rows],
        "total": len(rows),
    }


@router.get("/{telegram_id}")
async def user_detail(telegram_id: int = Path(..., gt=0)):
    """Full card — user, balance, subscription, discount, vip, trial."""
    try:
        user = await database.get_user(telegram_id)
        if not user:
            raise HTTPException(404, "User not found")
        balance = await database.get_user_balance(telegram_id)
        subscription = await database.get_subscription(telegram_id)
        trial = await database.get_trial_info(telegram_id)
        discount = await database.get_user_discount(telegram_id)
        traffic_discount = await database.get_user_traffic_discount(telegram_id)
        is_vip = await database.is_vip_user(telegram_id)
        cashback_fixed = await database.get_cashback_fixed_percent(telegram_id)
        cashback_effective = await database.get_effective_cashback_percent(telegram_id)
        return {
            "user": user,
            "balance_rubles": balance,
            "subscription": subscription,
            "trial": trial,
            "discount": discount,
            "traffic_discount": traffic_discount,
            "is_vip": is_vip,
            "cashback_fixed_percent": cashback_fixed,
            "cashback_effective_percent": cashback_effective,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"user_detail_failed: {e}")


@router.get("/{telegram_id}/history")
async def user_history(
    telegram_id: int = Path(..., gt=0),
    limit: int = Query(20, gt=0, le=200),
):
    try:
        return await database.get_subscription_history(telegram_id, limit)
    except Exception as e:
        raise HTTPException(500, f"history_failed: {e}")


@router.get("/{telegram_id}/extended-stats")
async def user_extended_stats(telegram_id: int = Path(..., gt=0)):
    try:
        return await database.get_user_extended_stats(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"extended_stats_failed: {e}")


@router.get("/{telegram_id}/payments")
async def user_payments(
    telegram_id: int = Path(..., gt=0),
    limit: int = Query(100, gt=0, le=500),
):
    """Все покупки пользователя — paid / pending / expired —
    из pending_purchases (там лежат подписки, traffic-паки, balance,
    telegram premium, steam, прокси, фарм-участки). Старая таблица
    `payments` тут не используется: она устарела и пропускает большую
    часть потоков."""
    try:
        rows = await database.get_user_purchases(telegram_id, limit=limit)
    except Exception as e:
        raise HTTPException(500, f"payments_failed: {e}")
    return [_serialize(r) for r in rows]


def _serialize(row: dict) -> dict:
    """Make datetimes / Decimals JSON-friendly without pulling in a
    custom encoder. Bytes values are skipped (none expected here)."""
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out


# ──────────────────────────────────────────────────────────────────────
# WRITE endpoints — all go through the same atomic helpers as the
# in-bot admin handlers. Side effects: DB updates, audit log,
# Remnawave sync (where the helper does it), event publication.
# ──────────────────────────────────────────────────────────────────────

class GrantRequest(BaseModel):
    days: int = Field(..., gt=0, le=3650)
    tariff: str = Field("basic")

    @field_validator("tariff")
    @classmethod
    def _valid_tariff(cls, v: str) -> str:
        if v not in config.VALID_SUBSCRIPTION_TYPES:
            raise ValueError(f"invalid tariff: {v}")
        return v


@router.post("/{telegram_id}/grant")
async def user_grant(
    telegram_id: int = Path(..., gt=0),
    body: GrantRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        expires_at, vpn_key = await database.admin_grant_access_atomic(
            telegram_id, body.days, int(admin["sub"]), tariff=body.tariff,
        )
    except Exception as e:
        raise HTTPException(500, f"grant_failed: {e}")
    bus.publish({
        "type": "admin:grant",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "days": body.days,
        "tariff": body.tariff,
    })
    return {
        "ok": True,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "vpn_key": vpn_key,
    }


class GrantMinutesRequest(BaseModel):
    minutes: int = Field(..., gt=0, le=525600)  # ≤ 1 year


@router.post("/{telegram_id}/grant-minutes")
async def user_grant_minutes(
    telegram_id: int = Path(..., gt=0),
    body: GrantMinutesRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        expires_at, vpn_key = await database.admin_grant_access_minutes_atomic(
            telegram_id, body.minutes, int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"grant_minutes_failed: {e}")
    bus.publish({
        "type": "admin:grant_minutes",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "minutes": body.minutes,
    })
    return {
        "ok": True,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "vpn_key": vpn_key,
    }


@router.post("/{telegram_id}/revoke")
async def user_revoke(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.admin_revoke_access_atomic(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"revoke_failed: {e}")
    bus.publish({
        "type": "admin:revoke",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


class SwitchTariffRequest(BaseModel):
    tariff: str = Field(...)

    @field_validator("tariff")
    @classmethod
    def _valid_tariff(cls, v: str) -> str:
        if v not in config.VALID_SUBSCRIPTION_TYPES:
            raise ValueError(f"invalid tariff: {v}")
        return v


@router.post("/{telegram_id}/switch-tariff")
async def user_switch_tariff(
    telegram_id: int = Path(..., gt=0),
    body: SwitchTariffRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        updated = await database.admin_switch_tariff(telegram_id, body.tariff)
    except Exception as e:
        raise HTTPException(500, f"switch_tariff_failed: {e}")
    if not updated:
        raise HTTPException(404, "no_active_subscription")
    bus.publish({
        "type": "admin:switch_tariff",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "tariff": body.tariff,
    })
    return {"ok": True, "subscription": updated}


class DiscountRequest(BaseModel):
    percent: int = Field(..., ge=1, le=100)
    expires_in_hours: Optional[int] = Field(None, gt=0, le=8760)  # ≤ 1 year


@router.post("/{telegram_id}/discount")
async def user_discount_create(
    telegram_id: int = Path(..., gt=0),
    body: DiscountRequest = ...,
    admin: dict = Depends(require_admin),
):
    expires_at = None
    if body.expires_in_hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    try:
        ok = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=body.percent,
            expires_at=expires_at,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"discount_create_failed: {e}")
    if not ok:
        raise HTTPException(500, "discount_create_failed")
    bus.publish({
        "type": "admin:discount_create",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "percent": body.percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.delete("/{telegram_id}/discount")
async def user_discount_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_user_discount(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"discount_delete_failed: {e}")
    bus.publish({
        "type": "admin:discount_delete",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


class TrafficDiscountRequest(BaseModel):
    """Скидка на пакеты GB для «Обхода» (bypass traffic packs).
    Хранится в отдельной таблице user_traffic_discounts (не путать с
    личной скидкой на подписку). Применяется в чекауте GB паков.
    """
    percent: int = Field(..., ge=1, le=100)
    expires_in_hours: Optional[int] = Field(None, gt=0, le=8760)  # ≤ 1 year


@router.post("/{telegram_id}/traffic-discount")
async def user_traffic_discount_create(
    telegram_id: int = Path(..., gt=0),
    body: TrafficDiscountRequest = ...,
    admin: dict = Depends(require_admin),
):
    expires_at = None
    if body.expires_in_hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    try:
        ok = await database.create_user_traffic_discount(
            telegram_id=telegram_id,
            discount_percent=body.percent,
            expires_at=expires_at,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"traffic_discount_create_failed: {e}")
    if not ok:
        raise HTTPException(500, "traffic_discount_create_failed")
    bus.publish({
        "type": "admin:traffic_discount_create",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "percent": body.percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.delete("/{telegram_id}/traffic-discount")
async def user_traffic_discount_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_user_traffic_discount(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"traffic_discount_delete_failed: {e}")
    bus.publish({
        "type": "admin:traffic_discount_delete",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


# ── Cashback fixed % (admin-managed override) ─────────────────────────
class CashbackFixRequest(BaseModel):
    """Фиксирует конкретный % кешбэка для пользователя. Жёстко перекрывает
    и тир, и floor. При выключении (DELETE) — юзер возвращается к
    обычной логике (тир + floor)."""
    percent: int = Field(..., ge=0, le=100)


@router.post("/{telegram_id}/cashback-fix")
async def user_cashback_fix_set(
    telegram_id: int = Path(..., gt=0),
    body: CashbackFixRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.set_cashback_fixed_percent(telegram_id, body.percent)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"cashback_fix_set_failed: {e}")
    if not ok:
        raise HTTPException(404, "user_not_found")
    bus.publish({
        "type": "admin:cashback_fix_set",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    # Уведомление партнёру. НЕ используем create_task без сохранения
    # ссылки — GC может убить task до запуска. Await'им inline: ~200мс
    # добавляется к ответу API, но admin action всё равно редкое и не
    # чувствительное к латентности. Флаг notify_sent показывает в ответе,
    # получилось ли отправить (для UI диагностики).
    # На отзыве фикса ничего не шлём (по требованию).
    notify_sent = await _send_partner_congrats(telegram_id, body.percent)
    effective = await database.get_effective_cashback_percent(telegram_id)
    return {
        "ok": True,
        "percent": body.percent,
        "effective_percent": effective,
        "notify_sent": notify_sent,
    }


async def _send_partner_congrats(telegram_id: int, percent: int) -> bool:
    """Поздравительное уведомление партнёру при активации fix-статуса.

    Содержит: приветствие, назначенный %, реферальную ссылку в цитате
    (тап → copy в буфер Telegram) и inline-кнопку «Поделиться», которая
    открывает штатный t.me/share/url? UI Telegram и подставляет ссылку.

    Все ошибки логируем с полным трейсом (logger.exception), возвращаем
    False. Сама fix-настройка уже в БД — если Telegram отказал (403 =
    юзер заблокировал бота / никогда не писал; 400 = невалидный HTML),
    admin видит это в ответе endpoint'а по флагу notify_sent.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        from urllib.parse import quote
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from app.api import telegram_webhook
        bot = getattr(telegram_webhook, "_bot", None)
        if bot is None:
            logger.warning(
                "CASHBACK_FIX_CONGRATS_NO_BOT tg=%s (bot not initialized yet)",
                telegram_id,
            )
            return False
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        from app.utils.referral_link import build_referral_link
        referral_link = await build_referral_link(telegram_id, bot_username)
        text = (
            "🎉 <b>Поздравляем — ты теперь партнёр!</b>\n\n"
            "Группа компаний <b>Atlas Secure &amp; QoDev</b> подтверждает "
            f"твой статус партнёра с фиксированной ставкой "
            f"<b>{percent}%</b> с каждой продажи по твоей рекомендации.\n\n"
            "<blockquote expandable>"
            f"💰 За каждую покупку по твоей ссылке — <b>{percent}% на баланс</b>.\n"
            "📈 Процент зафиксирован и не зависит от количества приглашённых — "
            "это отдельный VIP-статус."
            "</blockquote>\n\n"
            "<b>🔗 Твоя партнёрская ссылка</b>\n"
            f"<blockquote expandable><code>{referral_link}</code></blockquote>\n"
            "<i>Тапни на ссылку — она скопируется в буфер обмена. "
            "Или воспользуйся кнопкой «Поделиться» ниже.</i>"
        )
        share_url = f"https://t.me/share/url?url={quote(referral_link, safe='')}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Поделиться ссылкой", url=share_url)],
        ])
        await bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info(
            "CASHBACK_FIX_CONGRATS_SENT user=%s percent=%s link=%s",
            telegram_id, percent, referral_link,
        )
        return True
    except Exception as e:
        # exception() пишет полный traceback — иначе диагностировать
        # невозможно (раньше только .warning без trace, ловили пустоту).
        logger.exception(
            "CASHBACK_FIX_CONGRATS_FAIL user=%s err=%s", telegram_id, e,
        )
        return False


@router.delete("/{telegram_id}/cashback-fix")
async def user_cashback_fix_clear(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.clear_cashback_fixed_percent(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"cashback_fix_clear_failed: {e}")
    if not ok:
        raise HTTPException(404, "user_not_found")
    bus.publish({
        "type": "admin:cashback_fix_clear",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    effective = await database.get_effective_cashback_percent(telegram_id)
    return {"ok": True, "effective_percent": effective}


@router.post("/{telegram_id}/vip")
async def user_vip_grant(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.grant_vip_status(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"vip_grant_failed: {e}")
    bus.publish({
        "type": "admin:vip_grant",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


@router.delete("/{telegram_id}/vip")
async def user_vip_revoke(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.revoke_vip_status(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"vip_revoke_failed: {e}")
    bus.publish({
        "type": "admin:vip_revoke",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


class BalanceRequest(BaseModel):
    delta_rubles: float = Field(..., description="Positive credits, negative debits")
    reason: Optional[str] = Field(None, max_length=200)

    @field_validator("delta_rubles")
    @classmethod
    def _nonzero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("delta cannot be zero")
        if abs(v) > 1_000_000:
            raise ValueError("absolute value too large")
        return v


@router.delete("/{telegram_id}")
async def user_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Cascade-delete a user across all related tables. Irreversible.
    Routes through admin_delete_user_complete which also cleans up
    Remnawave entities + writes the audit log."""
    try:
        ok = await database.admin_delete_user_complete(
            telegram_id, int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"delete_failed: {e}")
    if not ok:
        raise HTTPException(404, "User not found or delete blocked")
    bus.publish({
        "type": "admin:user_deleted",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}


@router.post("/{telegram_id}/balance")
async def user_balance_change(
    telegram_id: int = Path(..., gt=0),
    body: BalanceRequest = ...,
    admin: dict = Depends(require_admin),
):
    """Credit (positive) or debit (negative) the user's balance.
    Routes through increase_balance / decrease_balance with source='admin'
    so the change appears in balance_transactions with proper attribution.
    """
    reason = body.reason or f"Web dashboard adjustment by admin {admin.get('sub')}"
    try:
        if body.delta_rubles > 0:
            ok = await database.increase_balance(
                telegram_id, body.delta_rubles,
                source="admin", description=reason,
            )
        else:
            ok = await database.decrease_balance(
                telegram_id, abs(body.delta_rubles),
                source="admin", description=reason,
            )
    except Exception as e:
        raise HTTPException(500, f"balance_change_failed: {e}")
    if not ok:
        raise HTTPException(400, "balance_change_rejected")
    bus.publish({
        "type": "admin:balance_change",
        "telegram_id": telegram_id,
        "delta": body.delta_rubles,
        "by": admin.get("sub"),
    })
    new_balance = 0.0
    try:
        new_balance = await database.get_user_balance(telegram_id)
    except Exception:
        pass
    return {"ok": True, "new_balance_rubles": new_balance}
