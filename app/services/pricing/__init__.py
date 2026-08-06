"""
Pricing helper — единая точка расчёта эффективной цены тарифа.

Использование в bot-коде:
    from app.services.pricing import get_effective_price
    p = await get_effective_price("basic", 30)
    if p.has_discount:
        text = f"~~{p.base}₽~~ {p.effective}₽  · {p.discount_reason}"
    else:
        text = f"{p.effective}₽"

Логика:
  1) Читаем override из tariff_price_overrides (если есть) — иначе
     базовая цена из config.TARIFFS.
  2) Читаем pricing_settings — если global_discount_percent > 0 и
     (discount_until_at IS NULL OR NOW() < discount_until_at),
     применяем скидку.
  3) Округляем до рубля.

Кэш: 30с TTL, invalidate через touch_cache() при PATCH.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import config
from database.core import get_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EffectivePrice:
    tariff: str
    period_days: int
    # Базовая цена (override или config.TARIFFS).
    base: int
    # Итоговая цена после global-discount.
    effective: int
    # 0..99 — процент, applied. 0 = скидки не было.
    discount_percent: int
    # Текст причины для отображения юзеру («Летняя акция»).
    discount_reason: Optional[str]
    # Когда скидка истекает (или None если бессрочная).
    discount_until_at: Optional[datetime]
    # True если базовая цена была изменена через override (не global-скидка).
    is_overridden: bool

    @property
    def has_discount(self) -> bool:
        return self.discount_percent > 0 and self.effective < self.base


# ── In-memory cache ─────────────────────────────────────────────────

_OVERRIDES_CACHE: Dict[tuple, int] = {}
_SETTINGS_CACHE: Optional[Dict[str, Any]] = None
_CACHE_TS: float = 0.0
_CACHE_TTL_SEC = 30.0


def touch_cache() -> None:
    """Сбросить кэш — вызывается из API endpoint после PATCH/PUT/DELETE."""
    global _CACHE_TS, _OVERRIDES_CACHE, _SETTINGS_CACHE
    _CACHE_TS = 0.0
    _OVERRIDES_CACHE = {}
    _SETTINGS_CACHE = None


async def _refresh_cache() -> None:
    global _OVERRIDES_CACHE, _SETTINGS_CACHE, _CACHE_TS
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                "SELECT tariff, period_days, price_rub FROM tariff_price_overrides"
            )
            _OVERRIDES_CACHE = {
                (r["tariff"], int(r["period_days"])): int(r["price_rub"])
                for r in rows
            }
        except Exception as e:
            logger.warning("tariff_price_overrides load failed: %s", e)
            _OVERRIDES_CACHE = {}
        try:
            r = await conn.fetchrow(
                """SELECT global_discount_percent, discount_reason,
                          discount_until_at, updated_at, updated_by
                   FROM pricing_settings WHERE id = 1"""
            )
            _SETTINGS_CACHE = dict(r) if r else {"global_discount_percent": 0}
        except Exception as e:
            logger.warning("pricing_settings load failed: %s", e)
            _SETTINGS_CACHE = {"global_discount_percent": 0}
    _CACHE_TS = time.monotonic()


async def _ensure_cache() -> None:
    if time.monotonic() - _CACHE_TS > _CACHE_TTL_SEC:
        try:
            await _refresh_cache()
        except Exception as e:
            logger.warning("pricing cache refresh failed: %s", e)


def _base_price(tariff: str, period_days: int) -> Optional[int]:
    """Базовая цена из config.TARIFFS (без override и discount)."""
    tariff_map = config.TARIFFS.get(tariff)
    if not tariff_map:
        return None
    period = tariff_map.get(period_days)
    if not period:
        return None
    return int(period.get("price") or 0) or None


def _apply_discount(base: int, percent: int) -> int:
    """base × (1 - percent/100), округление до рубля."""
    if percent <= 0 or percent >= 100:
        return base
    return int(round(base * (100 - percent) / 100))


async def get_effective_price(
    tariff: str, period_days: int,
) -> Optional[EffectivePrice]:
    """Вернуть эффективную цену (с учётом override и global-discount).
    None если тариф/период неизвестны."""
    await _ensure_cache()
    override = _OVERRIDES_CACHE.get((tariff, int(period_days)))
    base_from_config = _base_price(tariff, period_days)
    if override is None and base_from_config is None:
        return None
    base = int(override) if override is not None else int(base_from_config)
    is_overridden = override is not None

    # Global discount
    settings = _SETTINGS_CACHE or {}
    pct = int(settings.get("global_discount_percent") or 0)
    reason = settings.get("discount_reason")
    until = settings.get("discount_until_at")
    if until is not None:
        _now = datetime.now(timezone.utc)
        _until = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
        if _now >= _until:
            pct = 0  # Скидка истекла — сбрасываем.
            reason = None
    effective = _apply_discount(base, pct)
    return EffectivePrice(
        tariff=tariff,
        period_days=int(period_days),
        base=base,
        effective=effective,
        discount_percent=pct if effective < base else 0,
        discount_reason=reason if effective < base else None,
        discount_until_at=until if effective < base else None,
        is_overridden=is_overridden,
    )


async def list_all_prices() -> list[Dict[str, Any]]:
    """Все тарифы × периоды с их эффективными ценами. Для админ-UI.

    ДВА РОДА СТРОК, И РАЗНИЦА МЕЖДУ НИМИ — НЕ КОСМЕТИКА.

        editable=True   тарифы из config.TARIFFS. Их цену можно
                        переопределить (tariff_price_overrides) и на них
                        действует глобальная скидка — обе правки доходят
                        до покупателя через get_effective_price.

        editable=False  комбо-тарифы из config.COMBO_TARIFFS. Их цена
                        приходит в расчёт отдельным путём
                        (base_price_override_rubles в
                        database.subscription_pricing), мимо этого
                        модуля. Ни override, ни глобальная скидка на них
                        НЕ действуют.

    ЗАЧЕМ ТОГДА ВООБЩЕ ОТДАВАТЬ КОМБО. Затем, что комбо — отдельный
    продаваемый продукт со своей ценой, а не разновидность «Плюс».
    Экран цен, на котором его нет, врёт молчанием: человек видит четыре
    цены Плюса, меняет их и думает, что закрыл прайс целиком. Строка с
    editable=False говорит правду: цена такая, а меняется она в конфиге.

    Сделаете комбо editable, не научив расчёт цены читать override, —
    получите поле ввода, которое сохраняется и ни на что не влияет. Это
    хуже, чем его отсутствие.
    """
    await _ensure_cache()
    out = []
    for tariff, periods in config.TARIFFS.items():
        for period_days, meta in periods.items():
            ep = await get_effective_price(tariff, period_days)
            if ep is None:
                continue
            out.append({
                "tariff": tariff,
                "period_days": period_days,
                "base_price": ep.base,
                "config_price": int(meta.get("price") or 0),
                "effective_price": ep.effective,
                "discount_percent": ep.discount_percent,
                "is_overridden": ep.is_overridden,
                "has_discount": ep.has_discount,
                "editable": True,
            })
    for tariff, periods in (getattr(config, "COMBO_TARIFFS", {}) or {}).items():
        for period_days, meta in periods.items():
            price = int((meta or {}).get("price") or 0)
            if price <= 0:
                continue
            out.append({
                "tariff": tariff,
                "period_days": int(period_days),
                "base_price": price,
                "config_price": price,
                "effective_price": price,
                "discount_percent": 0,
                "is_overridden": False,
                "has_discount": False,
                "editable": False,
                # Сколько ГБ обхода входит в пакет — единственное, чем
                # комбо отличается от одноимённой подписки на экране.
                "bypass_gb": int((meta or {}).get("gb") or 0),
            })
    return out


async def set_override(
    tariff: str, period_days: int, price_rub: int, admin_id: int,
) -> None:
    """Upsert override для (tariff, period)."""
    if price_rub <= 0:
        raise ValueError("price_rub must be > 0")
    if tariff not in config.TARIFFS or period_days not in config.TARIFFS[tariff]:
        raise ValueError(f"unknown tariff/period: {tariff}/{period_days}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tariff_price_overrides
                    (tariff, period_days, price_rub, updated_by)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (tariff, period_days) DO UPDATE
                 SET price_rub = EXCLUDED.price_rub,
                     updated_at = NOW(),
                     updated_by = EXCLUDED.updated_by""",
            tariff, int(period_days), int(price_rub), int(admin_id),
        )
    touch_cache()


async def clear_override(tariff: str, period_days: int) -> bool:
    """Убрать override — вернёмся к base price из config."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM tariff_price_overrides WHERE tariff = $1 AND period_days = $2",
            tariff, int(period_days),
        )
    touch_cache()
    return res != "DELETE 0"


async def get_global_discount() -> Dict[str, Any]:
    """Прочитать текущие настройки глобальной скидки (для UI)."""
    await _ensure_cache()
    s = _SETTINGS_CACHE or {}
    return {
        "global_discount_percent": int(s.get("global_discount_percent") or 0),
        "discount_reason": s.get("discount_reason"),
        "discount_until_at": (
            s["discount_until_at"].isoformat()
            if s.get("discount_until_at") else None
        ),
        "updated_at": (
            s["updated_at"].isoformat() if s.get("updated_at") else None
        ),
        "updated_by": s.get("updated_by"),
    }


async def set_global_discount(
    percent: int, reason: Optional[str],
    until_at: Optional[datetime], admin_id: int,
) -> None:
    """Обновить глобальную скидку. percent=0 → отключить."""
    if percent < 0 or percent >= 100:
        raise ValueError("percent must be in [0, 99]")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE pricing_settings
               SET global_discount_percent = $1,
                   discount_reason = $2,
                   discount_until_at = $3,
                   updated_at = NOW(),
                   updated_by = $4
               WHERE id = 1""",
            int(percent), (reason or None), until_at, int(admin_id),
        )
    touch_cache()


__all__ = [
    "EffectivePrice",
    "get_effective_price",
    "list_all_prices",
    "set_override",
    "clear_override",
    "get_global_discount",
    "set_global_discount",
    "touch_cache",
]
