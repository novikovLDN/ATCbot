"""
Pricing management — управление ценами тарифов и global-discount.

GET  /pricing/tariffs                            — список всех цен
PATCH /pricing/tariffs/{tariff}/{period_days}    — override для пары
DELETE /pricing/tariffs/{tariff}/{period_days}   — снять override

GET  /pricing/global-discount                    — текущая скидка
PUT  /pricing/global-discount                    — установить/обновить
DELETE /pricing/global-discount                  — отключить (=percent 0)

Все endpoint'ы требуют admin-JWT.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.api.dashboard.deps import require_admin
from app.services import pricing

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/tariffs")
async def list_tariffs():
    """Все тарифы × периоды с эффективными ценами.

    Response format:
      [
        {
          tariff: "basic",
          period_days: 30,
          base_price: 199,           # config или override
          config_price: 199,         # из config.TARIFFS
          effective_price: 149,      # после global-discount
          discount_percent: 25,
          is_overridden: false,
          has_discount: true,
        }, ...
      ]
    """
    try:
        return await pricing.list_all_prices()
    except Exception as e:
        raise HTTPException(500, f"list_tariffs_failed: {e}")


class OverrideBody(BaseModel):
    price_rub: int = Field(..., gt=0, le=10_000_000)


@router.patch("/tariffs/{tariff}/{period_days}")
async def set_tariff_override(
    body: OverrideBody,
    tariff: str = Path(..., min_length=1, max_length=40),
    period_days: int = Path(..., gt=0, le=3650),
    admin: dict = Depends(require_admin),
):
    """Установить override цены. price_rub > 0. Кэш инвалидируется."""
    try:
        await pricing.set_override(
            tariff, period_days, body.price_rub, int(admin["sub"]),
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"set_override_failed: {e}")
    return {"ok": True, "tariff": tariff, "period_days": period_days,
            "price_rub": body.price_rub}


@router.delete("/tariffs/{tariff}/{period_days}")
async def clear_tariff_override(
    tariff: str = Path(..., min_length=1, max_length=40),
    period_days: int = Path(..., gt=0, le=3650),
):
    """Убрать override — вернёмся к цене из config.TARIFFS."""
    try:
        removed = await pricing.clear_override(tariff, period_days)
    except Exception as e:
        raise HTTPException(500, f"clear_override_failed: {e}")
    if not removed:
        raise HTTPException(404, "no override for that tariff/period")
    return {"ok": True, "tariff": tariff, "period_days": period_days,
            "cleared": True}


@router.get("/global-discount")
async def get_global_discount():
    """Текущие настройки глобальной скидки."""
    try:
        return await pricing.get_global_discount()
    except Exception as e:
        raise HTTPException(500, f"get_discount_failed: {e}")


class DiscountBody(BaseModel):
    """Payload для PUT /global-discount.

    percent: 0..99. 0 = отключено (то же самое что DELETE).
    reason: короткая подпись отображаемая юзеру («Летняя акция»).
    until_at_iso: ISO 8601 с TZ (UTC). None = бессрочная скидка.
    """
    percent: int = Field(..., ge=0, le=99)
    reason: Optional[str] = Field(None, max_length=200)
    until_at_iso: Optional[str] = Field(None, max_length=40)


@router.put("/global-discount")
async def put_global_discount(
    body: DiscountBody,
    admin: dict = Depends(require_admin),
):
    """Установить/обновить глобальную скидку."""
    until_at = None
    if body.until_at_iso:
        try:
            until_at = datetime.fromisoformat(body.until_at_iso.replace("Z", "+00:00"))
            if until_at.tzinfo is None:
                until_at = until_at.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "until_at_iso must be ISO 8601 datetime")
        # В прошлом смысла нет.
        if until_at <= datetime.now(timezone.utc):
            raise HTTPException(400, "until_at must be in the future")
    try:
        await pricing.set_global_discount(
            body.percent, body.reason, until_at, int(admin["sub"]),
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"set_discount_failed: {e}")
    return {"ok": True, "percent": body.percent}


@router.delete("/global-discount")
async def delete_global_discount(admin: dict = Depends(require_admin)):
    """Быстрое выключение — эквивалентно PUT percent=0, reason=None."""
    try:
        await pricing.set_global_discount(0, None, None, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"clear_discount_failed: {e}")
    return {"ok": True, "cleared": True}
