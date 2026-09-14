"""Runtime toggle: SBP платежи → Platega ИЛИ Wata ИЛИ 50/50 split.

Админ переключает через dashboard, конфиг живёт в Redis, локальный
in-memory кэш на 30 сек чтобы не дёргать Redis на каждый callback.

Три режима:
  - "platega" — все SBP-кнопки уходят в Platega
  - "wata"    — все SBP-кнопки уходят в Wata
  - "split"   — детерминированное разделение по telegram_id:
                (telegram_id % 100) < wata_percent → Wata, иначе → Platega
                (тот же user всегда попадает к тому же провайдеру,
                пока wata_percent не меняется — стабильные метрики).

Кнопка в UI бота всегда одна («📱 СБП», callback_data="pay:sbp"), решение
о провайдере принимается ВНУТРИ хендлера через resolve_provider().
"""
from __future__ import annotations

import json
import logging
import time
from typing import Literal

logger = logging.getLogger(__name__)

_REDIS_KEY = "dashboard:sbp_router_config"

MODE_PLATEGA = "platega"
MODE_WATA = "wata"
MODE_SPLIT = "split"
_VALID_MODES = {MODE_PLATEGA, MODE_WATA, MODE_SPLIT}

Provider = Literal["platega", "wata"]
Mode = Literal["platega", "wata", "split"]

_DEFAULTS: dict = {
    "mode": MODE_PLATEGA,
    "wata_percent": 50,
}

_CACHE_TTL_SEC = 30.0
_cache: dict = dict(_DEFAULTS)
_cache_expires_at: float = 0.0


async def _redis():
    try:
        from app.utils.redis_client import get_client, is_configured
        if not is_configured():
            return None
        return await get_client()
    except Exception:
        return None


def _normalize(raw: dict) -> dict:
    mode = str(raw.get("mode") or MODE_PLATEGA).strip().lower()
    if mode not in _VALID_MODES:
        mode = MODE_PLATEGA
    try:
        pct = int(raw.get("wata_percent", 50))
    except (TypeError, ValueError):
        pct = 50
    pct = max(0, min(100, pct))
    return {"mode": mode, "wata_percent": pct}


async def get_config() -> dict:
    """Возвращает текущий конфиг {mode, wata_percent}. Кэш 30 сек."""
    global _cache, _cache_expires_at
    now = time.monotonic()
    if now < _cache_expires_at:
        return dict(_cache)

    r = await _redis()
    if r is not None:
        try:
            raw = await r.get(_REDIS_KEY)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                loaded = _normalize(json.loads(raw))
                _cache = loaded
                _cache_expires_at = now + _CACHE_TTL_SEC
                return dict(_cache)
        except Exception as e:
            logger.warning("sbp_router redis read failed: %s", e)

    _cache = dict(_DEFAULTS)
    _cache_expires_at = now + _CACHE_TTL_SEC
    return dict(_cache)


async def set_config(*, mode: str, wata_percent: int) -> dict:
    """Обновить конфиг. Redis + локальный кэш инвалидируется мгновенно.

    Другие процессы бота подхватят через свой 30-сек TTL.
    """
    global _cache, _cache_expires_at
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown sbp router mode: {mode!r}")
    pct = max(0, min(100, int(wata_percent)))
    payload = {"mode": mode, "wata_percent": pct}

    r = await _redis()
    if r is not None:
        try:
            await r.set(_REDIS_KEY, json.dumps(payload))
        except Exception as e:
            logger.warning("sbp_router redis write failed: %s", e)

    _cache = dict(payload)
    _cache_expires_at = time.monotonic() + _CACHE_TTL_SEC
    logger.info("sbp_router config updated: %s", payload)
    return dict(payload)


def _resolve_provider_sync(telegram_id: int, cfg: dict) -> Provider:
    mode = cfg.get("mode", MODE_PLATEGA)
    if mode == MODE_WATA:
        return "wata"
    if mode == MODE_SPLIT:
        pct = int(cfg.get("wata_percent", 50))
        bucket = int(telegram_id) % 100
        return "wata" if bucket < pct else "platega"
    return "platega"


async def resolve_provider(telegram_id: int) -> Provider:
    """Возвращает провайдера для конкретного юзера ('platega' | 'wata')."""
    cfg = await get_config()
    provider = _resolve_provider_sync(int(telegram_id), cfg)

    if provider == "wata":
        try:
            import wata_service
            if not wata_service.is_enabled():
                logger.warning(
                    "sbp_router: user %s selected 'wata' but wata_service "
                    "not configured — falling back to platega",
                    telegram_id,
                )
                return "platega"
        except Exception:
            return "platega"
    return provider
