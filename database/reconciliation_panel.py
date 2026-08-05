"""Сверка: всё общение с панелью Remnawave.

ЧТО ЗДЕСЬ
    Единственное место, где сверка ходит в панель:
      • разбор дат из ответов панели (`_parse_remnawave_dt`);
      • чтение expireAt конкретного премиум-юзера (`_fetch_panel_expires_at`);
      • кэшированный полный скан панели для поиска кандидатов
        (`_scan_panel_for_over_issuance` + сброс кэша).

ПОЧЕМУ ВЫДЕЛЕНО
    Скан панели, детальный экран и «Исправить» правят по разным поводам,
    но все трое опираются на эти функции. Держать их рядом с расчётами и
    SQL значило править кэш и лимиты, глядя на 1100 строк чужого кода.

ЧТО ЛЕГКО СЛОМАТЬ
    Кэш скана — глобал ЭТОГО модуля. Тесты и «Исправить» сбрасывают его
    через `invalidate_panel_scan_cache()`; присвоение
    `database.reconciliation._panel_scan_cache` из фасада ничего бы не
    изменило, поэтому наружу глобал намеренно не реэкспортируется.

    Неудачный скан не кэшируется, и это важнее, чем кажется: закэшированное
    «панель недоступна» на 10 минут спрятало бы реальные данные, а
    закэшированный пустой список читался бы как «всё чисто».
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Matches the default premium-entity username pattern `tg_{telegram_id}_premium`.
# See app/services/remnawave_premium.py:build_premium_username. If deployment
# uses a custom REMNAWAVE_PREMIUM_USERNAME_PATTERN, the tail/head is customised
# but the telegram_id digits are always present as the numeric group.
_PREMIUM_USERNAME_RE = re.compile(r"^tg_(\d+)_premium$")


def _parse_remnawave_dt(raw) -> Optional[datetime]:
    """Parse Remnawave-returned expireAt into a UTC-aware datetime."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Кэш полного скана панели ──────────────────────────────────────────
# get_all_users листает Remnawave страницами по 1000; на проде это ~358k
# сущностей, то есть сотни HTTP-запросов и десятки секунд. Экран «Сверка»
# дёргает скан на КАЖДОЕ открытие, поэтому пара обновлений страницы
# подряд превращалась в шторм запросов к панели (вплоть до rate-limit) и
# залипание воркера FastAPI.
#
# Кэшируем не сырой список пользователей (он огромный), а уже отфильтрованных
# кандидатов — их единицы. Порог «8 лет» за время жизни кэша сдвигается на
# минуты, поэтому пересчитывать cutoff чаще бессмысленно.
#
# В памяти процесса, без таблицы: фоновой задачи с записью в БД тут быть не
# должно — это изменение схемы.
_PANEL_SCAN_TTL_SECONDS = 600  # 10 минут
_panel_scan_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
# Лок, чтобы N одновременных запросов дашборда не запустили N полных сканов:
# первый идёт в панель, остальные ждут и разбирают готовый результат.
_panel_scan_lock = asyncio.Lock()


def invalidate_panel_scan_cache() -> None:
    """Сбросить кэш скана панели.

    Вызывается после успешного патча expireAt: без сброса админ жмёт
    «Исправить», обновляет экран и видит того же человека в кандидатах —
    выглядит как «кнопка не сработала».
    """
    global _panel_scan_cache
    _panel_scan_cache = None


async def _scan_panel_for_over_issuance(
    cutoff: datetime,
    force_refresh: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """Список премиум-сущностей панели с expireAt > cutoff, с кэшем на TTL.

    Возвращает None, если панель недоступна (вызывающий обязан отличать это
    от пустого списка — иначе «панель легла» покажется как «всё чисто»).
    Неудачный скан не кэшируется.
    """
    global _panel_scan_cache

    async with _panel_scan_lock:
        cached = _panel_scan_cache
        if not force_refresh and cached is not None:
            stored_at, rows = cached
            if (time.monotonic() - stored_at) < _PANEL_SCAN_TTL_SECONDS:
                logger.debug(
                    "find_over_issuance_candidates: panel scan cache hit (%d rows)",
                    len(rows),
                )
                return rows

        from app.services import remnawave_api
        all_users = await remnawave_api.get_all_users()
        if all_users is None:
            return None

        over_from_panel: List[Dict[str, Any]] = []
        for u in all_users:
            username = (u.get("username") or "").strip()
            m = _PREMIUM_USERNAME_RE.match(username)
            if not m:
                continue
            try:
                tg_id = int(m.group(1))
            except (ValueError, TypeError):
                continue
            panel_expires_at = _parse_remnawave_dt(u.get("expireAt"))
            if not panel_expires_at or panel_expires_at <= cutoff:
                continue
            over_from_panel.append({
                "telegram_id": tg_id,
                "panel_username": username,
                "panel_expires_at": panel_expires_at,
                "panel_uuid": u.get("uuid"),
                "panel_status": u.get("status"),
            })

        over_from_panel.sort(key=lambda x: x["panel_expires_at"], reverse=True)
        _panel_scan_cache = (time.monotonic(), over_from_panel)
        logger.info(
            "find_over_issuance_candidates: panel scanned, %d entities, "
            "%d over cutoff (cached for %ds)",
            len(all_users), len(over_from_panel), _PANEL_SCAN_TTL_SECONDS,
        )
        return over_from_panel


# ──────────────────────────────────────────────────────────────────────
#  Remnawave premium entity — source of truth for actual expireAt
# ──────────────────────────────────────────────────────────────────────

async def _fetch_panel_expires_at(
    telegram_id: int,
    remnawave_premium_uuid: Optional[str],
) -> Optional[datetime]:
    """Fetch the Remnawave premium entity's `expireAt` — this is the
    authoritative expiration for VPN access. The bot's `subscriptions.expires_at`
    can go stale (leftover from bypass-only transitions, migration back-fills,
    admin scripts, …); the panel value is what actually controls the user.

    Lookup order:
      1. by cached `remnawave_premium_uuid` (fast — direct GET /api/users/{uuid})
      2. by username `tg_{telegram_id}_premium` (fallback for rows where the
         uuid was never cached).

    Returns None on any failure — the caller then falls back to the DB value
    (i.e. keeps the row as a candidate so it is not silently dropped)."""
    try:
        from app.services import remnawave_api
        from app.services.remnawave_premium import build_premium_username
    except Exception as e:
        logger.warning("reconciliation: remnawave_api import failed: %s", e)
        return None

    payload = None
    if remnawave_premium_uuid:
        try:
            payload = await remnawave_api.get_user(remnawave_premium_uuid)
        except Exception as e:
            logger.debug(
                "reconciliation: get_user(uuid=%s) failed for tg=%s: %s",
                remnawave_premium_uuid[:8], telegram_id, e,
            )

    if not payload:
        try:
            payload = await remnawave_api.find_user_by_username(
                build_premium_username(telegram_id)
            )
        except Exception as e:
            logger.debug(
                "reconciliation: find_user_by_username failed for tg=%s: %s",
                telegram_id, e,
            )
            return None

    if not payload:
        return None

    raw = payload.get("expireAt") or payload.get("expire_at")
    if not raw:
        return None
    try:
        # Remnawave returns ISO-8601 (usually with trailing 'Z').
        if isinstance(raw, str) and raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# Здесь была _bulk_fetch_panel_expires_at — пакетный опрос панели по списку
# кандидатов с семафором на 8 параллельных запросов. Удалена: ни одного
# вызывающего по всему дереву не было ни разу. Рядом живёт _fetch_panel_expires_at
# (её зовут get_reconciliation_detail и apply_reconciliation_fix), и пакетная
# обёртка читалась как рабочая часть API модуля — кто-нибудь построил бы на ней
# новый экран сверки, ни разу не проверенный на живой панели.
# Если пакетный опрос понадобится — писать заново под конкретный вызов,
# с явным лимитом и обработкой ошибок панели.

