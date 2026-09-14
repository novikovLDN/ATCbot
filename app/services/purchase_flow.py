"""
Purchase-time provisioning of Remnawave entities.

Replaces the legacy `vpn_utils.add_vless_user` call at purchase /
trial / renewal time when `config.PURCHASE_FLOW_REMNAWAVE` is on.
Creates / adopts BOTH entities the customer wants in the new world:

  premium  — squad MainServer, expireAt = subscription_end, unlimited bytes
  bypass   — squad Clients,    far-future expireAt,         byte-limited

Returns a dict shaped EXACTLY like the legacy `add_vless_user` so the
existing grant_access / finalize_purchase code consumes it unchanged:

    {
        "uuid":              <samopis-style UUID, also embedded in VLESS link>,
        "vless_url":         <premium subscription URL>,
        "vless_url_plus":    <bypass subscription URL or None>,
        "subscription_type": <tariff string, e.g. "basic"/"plus"/"trial">,
    }

`vpn_key` column gets the premium URL, `vpn_key_plus` column gets the
bypass URL — so the rest of the bot continues to ship two links to
Plus / Basic / Trial buyers without code changes elsewhere.

This module never calls vpnapi master.  When `PURCHASE_FLOW_REMNAWAVE`
is OFF the legacy `vpn_utils.add_vless_user` is used instead by the
caller (see database/subscriptions.py:grant_access).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuid_lib
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import config
from app.services import remnawave_bypass, remnawave_premium

logger = logging.getLogger(__name__)

# Free-tier traffic allowance for the bypass entity on a Trial run.
# Sourced from config.TRIAL_BYPASS_MB (default 500 MB).
def _trial_bypass_bytes() -> int:
    return int(getattr(config, "TRIAL_BYPASS_MB", 500)) * (1024 ** 2)


def _bypass_bytes_for(
    tariff: str,
    period_days: int,
    is_trial: bool,
    is_combo: bool = False,
) -> int:
    """Return the bypass entity's trafficLimitBytes for the given tariff.

    - Trial → config.TRIAL_BYPASS_MB MB
    - Combo → COMBO_TARIFFS[combo_{tariff}][period_days]["gb"] GB
      (base tariff basic/plus + combo_gb пакет — combo_gb это лимит bypass)
    - Basic / Plus → TRAFFIC_LIMITS[tariff][period_days] (already in bytes)
    """
    if is_trial:
        return _trial_bypass_bytes()
    combo_table = getattr(config, "COMBO_TARIFFS", {}) or {}
    # is_combo flag → tariff="basic"/"plus" мапится в combo_basic/combo_plus.
    # Fallback: tariff уже может быть с префиксом (напр. broadcast gift-combo).
    if is_combo:
        combo_key = tariff if tariff.startswith("combo_") else f"combo_{tariff}"
        per_period = (combo_table.get(combo_key) or {}).get(period_days) or {}
        gb = per_period.get("gb")
        if isinstance(gb, int) and gb > 0:
            return gb * (1024 ** 3)
    if tariff in combo_table:
        per_period = combo_table[tariff].get(period_days) or {}
        gb = per_period.get("gb")
        if isinstance(gb, int) and gb > 0:
            return gb * (1024 ** 3)
    # Standard tariff
    traffic_table = getattr(config, "TRAFFIC_LIMITS", {}) or {}
    table = traffic_table.get(tariff)
    if isinstance(table, dict):
        if period_days in table:
            return int(table[period_days])
        available = sorted(table.keys())
        for p in available:
            if p >= period_days:
                return int(table[p])
        if available:
            return int(table[available[-1]])
    if isinstance(table, int):
        return int(table)
    # Last resort: 10 GB.
    return 10 * (1024 ** 3)


def _looks_like_uuid(s: Optional[str]) -> bool:
    if not s or not isinstance(s, str):
        return False
    if len(s) != 36 or s.count("-") != 4:
        return False
    try:
        uuid_lib.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


async def _premium_url_for_existing(telegram_id: int) -> Optional[str]:
    """Return the cached premium subscriptionUrl for a user whose premium
    entity already exists (Task-1-migrated user or earlier purchase)."""
    import database
    pool = await database.get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        cached = await conn.fetchval(
            "SELECT remnawave_premium_sub_url FROM subscriptions "
            "WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )
    return cached or None


async def provision_subscription(
    telegram_id: int,
    *,
    tariff: str,
    subscription_end: datetime,
    period_days: int,
    is_trial: bool = False,
    is_combo: bool = False,
) -> dict:
    """Provision premium + bypass entities for a purchase / trial / renewal.

    Returns a dict shaped like the legacy `vpn_utils.add_vless_user`:
    keys `uuid`, `vless_url`, `vless_url_plus`, `subscription_type`.

    On any non-recoverable error a RuntimeError is raised so the caller's
    existing retry logic (`MAX_VPN_RETRIES` loop in grant_access) kicks in.
    """
    if not config.REMNAWAVE_ENABLED:
        raise RuntimeError("PURCHASE_FLOW_REMNAWAVE is on but REMNAWAVE_API_URL/TOKEN are not set")

    import database  # lazy import — keeps unit tests asyncpg-free

    # ── Determine the connection UUID we want the premium entity to use ──
    # If the user has an old samopis uuid (un-migrated legacy purchase, or a
    # previous bot purchase before cut-over), reuse it so legacy VLESS
    # clients keep working.  Otherwise generate a fresh one.
    existing_subscription = await database.get_subscription_any(telegram_id)
    legacy_uuid = (existing_subscription or {}).get("uuid") if existing_subscription else None
    if not _looks_like_uuid(legacy_uuid):
        legacy_uuid = None

    requested_uuid = legacy_uuid or str(uuid_lib.uuid4())

    # ── Premium entity ───────────────────────────────────────────────
    existing_premium_uuid = await database.get_remnawave_premium_uuid(telegram_id)
    premium_sub_url: Optional[str] = None
    premium_panel_uuid: Optional[str] = existing_premium_uuid

    if existing_premium_uuid:
        # Renewal: PATCH expireAt.  Bypass entity is handled below independently.
        renewed = await remnawave_premium.renew_premium_user(telegram_id, subscription_end, tier=tariff)
        if not renewed:
            logger.warning(
                "PURCHASE_FLOW: premium renew returned False — falling back to create-flow tg=%s",
                telegram_id,
            )
            existing_premium_uuid = None
        else:
            premium_sub_url = await _premium_url_for_existing(telegram_id)

    if not existing_premium_uuid:
        result = await remnawave_premium.create_premium_user_entity(
            telegram_id,
            requested_uuid=requested_uuid,
            expire_at=subscription_end,
            description=f"Premium via bot ({tariff})",
            tier=tariff,   # devices by tariff (owner 2026-09-14)
        )
        if not result.ok:
            raise RuntimeError(f"premium provision failed: status={result.status} error={result.error}")
        premium_panel_uuid = result.panel_uuid
        premium_sub_url = result.subscription_url
        try:
            await database.set_remnawave_premium_uuid_and_url(
                telegram_id,
                result.panel_uuid or "",
                result.subscription_url,
                short_uuid=result.short_uuid,
            )
            # 3.x: numeric id обязателен для последующих actions/PATCH.
            if result.panel_id is not None:
                await database.set_remnawave_premium_id(telegram_id, result.panel_id)
        except Exception as e:
            logger.error(
                "PURCHASE_FLOW: failed to persist premium mapping tg=%s err=%s",
                telegram_id, e,
            )
            raise

    if not premium_sub_url:
        # Cache miss after a renewal — back-fill from panel one time.
        try:
            from app.services import remnawave_api
            entity = await remnawave_api.get_user(premium_panel_uuid or "")
            premium_sub_url = (entity or {}).get("subscriptionUrl") or ""
            if premium_sub_url:
                await database.set_remnawave_premium_sub_url(telegram_id, premium_sub_url)
        except Exception as e:
            logger.warning("PURCHASE_FLOW: premium url back-fill failed tg=%s %s", telegram_id, e)

    # ── Bypass entity ────────────────────────────────────────────────
    # ⚠️ ПРАВИЛА:
    #   • TRIAL             → создаём bypass с TRIAL_BYPASS_MB (единственный
    #                         путь для trial, webhook confirmation НЕ идёт).
    #   • Fresh paid (нет   → создаём bypass С ФИНАЛЬНЫМ лимитом (combo → 75 ГБ,
    #     entity)             обычный basic 30d → 10 ГБ). confirmation.py
    #                         увидит bypass_created_fresh=True в return и
    #                         SKIP свой top-up → никакого double-add.
    #   • Renewal (есть     → SKIP полностью. confirmation.py сам всё сделает:
    #     entity)             top-up на нужную сумму по tariff / combo.
    #                         Раньше double-add: +tariff здесь + +combo там.
    bypass_bytes = _bypass_bytes_for(tariff, period_days, is_trial, is_combo=is_combo)
    bypass_sub_url: Optional[str] = None
    bypass_created_fresh = False

    existing_bypass_uuid = await database.get_remnawave_uuid(telegram_id)
    if existing_bypass_uuid:
        # Renewal — bypass entity уже есть. Топ-ап делает confirmation.py.
        cache = await database.get_remnawave_bypass_cache(telegram_id)
        bypass_sub_url = (cache or {}).get("remnawave_bypass_sub_url") or None

    # Fresh create — только если нет entity вообще И это trial ИЛИ paid.
    if not existing_bypass_uuid:
        bresult = await remnawave_bypass.create_bypass_user_entity(
            telegram_id,
            traffic_limit_bytes=bypass_bytes,
            description=f"Bypass via bot ({tariff})",
        )
        if not bresult.ok:
            # Bypass fail НЕ блокирует premium (юзер получит ключ),
            # но админ должен узнать — иначе тихая потеря bypass tier.
            # Reconciliation-flow добэкфилит через resolve_bypass /
            # admin dashboard.
            logger.warning(
                "PURCHASE_FLOW_BYPASS_FAILED_NON_FATAL: tg=%s status=%s error=%s",
                telegram_id, bresult.status, bresult.error,
            )
            bypass_sub_url = None
            # Fire-and-forget DM админу (не блокирует flow).
            try:
                import asyncio as _aio
                _aio.create_task(_notify_admin_bypass_failed(
                    telegram_id, tariff, bresult.status, bresult.error,
                ))
            except Exception:
                pass
        else:
            bypass_sub_url = bresult.subscription_url
            # Only a real POST-create carries the final limit. An ADOPTED
            # entity (recovered: the DB cache was empty, the panel already had
            # it) keeps its old trafficLimitBytes — confirmation must top it up
            # or the paid GB are lost (docs/audit/07_e2e.md, E2E-BYPASS-ADOPT).
            bypass_created_fresh = not bresult.recovered
        if bresult.ok:
            try:
                await database.set_remnawave_bypass_cache(
                    telegram_id,
                    bresult.panel_uuid,
                    bresult.subscription_url,
                    bresult.short_uuid,
                )
                # 3.x: numeric id для быстрого пути update/actions без
                # UUID→id auto-resolve overhead.
                if bresult.panel_id is not None:
                    await database.set_remnawave_id(telegram_id, bresult.panel_id)
            except Exception as e:
                logger.warning(
                    "PURCHASE_FLOW: failed to persist bypass cache tg=%s %s",
                    telegram_id, e,
                )

    # Backfill bypass_sub_url через панель, если POST не отдал subscriptionUrl
    # (защита: без URL bypass-кнопка "Подключиться" не появится).
    if not bypass_sub_url:
        try:
            from app.services import remnawave_api
            # ⚠️ Резолвим ГАРАНТИРОВАННО bypass-сущность (username == str(tg)),
            # а НЕ первую попавшуюся по telegram_id: панельный stream часто
            # отдаёт premium первым, и его subscriptionUrl утекал в
            # remnawave_bypass_sub_url → на экране «Обход» показывался ключ
            # основных серверов (один ключ на оба). get_bypass_entity_safe
            # проверяет username и по пути чинит id/uuid в БД.
            entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
            fetched_url = ((entity or {}).get("subscriptionUrl") or "").strip() or None
            if fetched_url:
                bypass_sub_url = fetched_url
                try:
                    # Сохраняем URL в кеш — не перезаписывает uuid/short_uuid.
                    await database.set_remnawave_bypass_cache(
                        telegram_id, None, fetched_url, None,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("PURCHASE_FLOW: bypass url back-fill failed tg=%s %s", telegram_id, e)

    logger.info(
        "PURCHASE_FLOW_DONE: tg=%s tariff=%s is_combo=%s premium_uuid=%s bypass_uuid=%s "
        "premium_url=%s bypass_url=%s bypass_fresh=%s bypass_bytes=%s",
        telegram_id, tariff, is_combo,
        (premium_panel_uuid or "")[:8],
        ((await database.get_remnawave_uuid(telegram_id)) or "")[:8],
        bool(premium_sub_url),
        bool(bypass_sub_url),
        bypass_created_fresh,
        bypass_bytes if bypass_created_fresh else "(untouched)",
    )

    # Sub-aggregator hook: entities только что созданы/продлены → нужно
    # выкинуть кеш и перечитать. Fire-and-forget, no-op если агрегатор
    # отключён (SUB_AGGREGATOR_ENABLED=false) или юзер не в бета-скоупе
    # (SUB_AGGREGATOR_ADMIN_ONLY=true и юзер не админ).
    try:
        from app.services import sub_aggregator
        sub_aggregator.invalidate_bg(telegram_id)
    except Exception as _agg_err:
        logger.warning("sub_aggregator hook failed tg=%s: %s", telegram_id, _agg_err)

    return {
        # legacy uuid lives in subscriptions.uuid; the connection uuid that
        # ended up in the panel may differ if forced-uuid was rejected.
        "uuid": requested_uuid,
        "vless_url": premium_sub_url or "",
        "vless_url_plus": bypass_sub_url,
        "subscription_type": tariff or "basic",
        # NEW: True если мы только что создали bypass entity с ФИНАЛЬНЫМ лимитом.
        # confirmation.py: если True → НЕ добавлять combo/tariff GB (иначе double).
        "bypass_created_fresh": bypass_created_fresh,
    }


async def sync_renewal_to_remnawave(sync_info: dict) -> None:
    """Post-commit renewal sync — продлить ТОЛЬКО premium expireAt.

    Bypass GB добавляется отдельно в confirmation.py (там знают is_combo
    и сколько GB именно этой покупки). Раньше здесь звался
    provision_subscription, который делал double-add: +tariff_gb здесь
    и потом +combo_gb в confirmation → юзер получал сумму (85 вместо 75
    для combo, 20 вместо 10 для обычного renewal + случайного combo-фикса).

    Простая логика: renewal = продлить срок на premium. Всё.
    Bypass GB — отдельная зона ответственности confirmation.py.

    Сбой (docs/audit/03_payment_matrix.md, баг M-RENEW-SYNC): БД уже продлена,
    а панель — нет («подписка до 20 окт., купил месяц, в панели 20 окт.»).
    Раньше это оставалось только в логе, а ретрай вебхука после commit упирается
    в already_processed и панель не трогает. Теперь: строка payment_errors +
    алерт админу (force, с бюджетом) + ОДНА фоновая пересинхронизация к
    ТЕКУЩЕЙ дате БД (никогда не ниже и не выше БД). Исключение пробрасывается —
    семантика вызывающих не меняется.
    """
    try:
        await _sync_renewal_once(sync_info)
    except Exception as e:
        await _report_renewal_sync_failure(sync_info, e, final=False)
        _schedule_renewal_resync(sync_info)
        raise


async def _sync_renewal_once(sync_info: dict) -> None:
    from app.services import remnawave_premium
    tg = int(sync_info["telegram_id"])
    new_expire = sync_info["subscription_end"]
    ok = await remnawave_premium.renew_premium_user(tg, new_expire, tier=sync_info.get("tariff"))
    if not ok:
        # Premium entity не найден — вызовем полный provision, который
        # создаст premium (и bypass если нужно) через preflight+adopt.
        # Это redundancy для legacy юзеров без premium entity в панели.
        logger.warning(
            "sync_renewal: renew_premium_user returned False tg=%s — "
            "falling back to full provision_subscription (creates missing entities)",
            tg,
        )
        await provision_subscription(
            tg,
            tariff=sync_info.get("tariff") or "basic",
            subscription_end=new_expire,
            period_days=int(sync_info.get("period_days") or 30),
            is_trial=False,
            is_combo=bool(sync_info.get("is_combo", False)),
        )


# ── renewal sync failure: alert + one background re-sync (legacy path) ──────
#
# Only the legacy (flag-off) renewal path comes here; the provisioning outbox
# has its own retry + alerts. Delays are short on purpose: a panel hiccup is the
# usual cause. The re-sync target is re-read from the DB each time, so a later
# renewal that already moved the panel forward is never undone.

RESYNC_DELAYS_S = (60, 300, 900)
ALERT_WINDOW_S = 300.0
ALERT_FORCED_PER_WINDOW = 5

_forced_alert_times: "deque[float]" = deque()
_resync_tasks: dict = {}          # telegram_id → pending re-sync task (one per user)


async def _resync_sleep(seconds: float) -> None:
    """Separate hook so tests can control the re-sync timing."""
    await asyncio.sleep(seconds)


def _take_forced_alert_slot() -> bool:
    """At most ALERT_FORCED_PER_WINDOW forced alerts per window; the rest go on
    the category cooldown (every failure is still in payment_errors)."""
    now = time.monotonic()
    while _forced_alert_times and now - _forced_alert_times[0] >= ALERT_WINDOW_S:
        _forced_alert_times.popleft()
    if len(_forced_alert_times) >= ALERT_FORCED_PER_WINDOW:
        return False
    _forced_alert_times.append(now)
    return True


def _alert_bot():
    for module_name in ("app.api.payment_webhook", "app.api.telegram_webhook"):
        try:
            import importlib
            bot = getattr(importlib.import_module(module_name), "_bot", None)
        except Exception:
            bot = None
        if bot is not None:
            return bot
    return None


def _fmt_dt(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)


async def _report_renewal_sync_failure(sync_info: dict, err: Optional[BaseException], *, final: bool) -> None:
    """payment_errors row + admin alert. Never raises. No secrets in the text."""
    tg = sync_info.get("telegram_id")
    end = sync_info.get("subscription_end")
    err_text = f"{type(err).__name__}: {err}" if err is not None else "unknown"
    logger.critical(
        "RENEWAL_SYNC_%s: tg=%s db_expires=%s tariff=%s period_days=%s err=%s",
        "GAVE_UP" if final else "FAILED", tg, _fmt_dt(end), sync_info.get("tariff"),
        sync_info.get("period_days"), err_text,
    )
    try:
        import database
        await database.log_payment_error(
            stage="renewal_sync",
            telegram_id=int(tg) if tg is not None else None,
            error_code="renewal_sync_gave_up" if final else "renewal_sync_failed",
            error_message=err_text[:500],
            raw_payload={
                "subscription_end": _fmt_dt(end),
                "tariff": sync_info.get("tariff"),
                "period_days": sync_info.get("period_days"),
            },
        )
    except Exception as e:
        logger.warning("RENEWAL_SYNC_PAYMENT_ERROR_LOG_FAILED: tg=%s %s", tg, e)
    bot = _alert_bot()
    if bot is None:
        logger.error("RENEWAL_SYNC_ALERT_NO_BOT: tg=%s", tg)
        return
    if final:
        head = "Premium STILL NOT extended in the panel (automatic re-sync gave up)"
        action = "Action: set the premium expireAt in the panel to the DB date above."
    else:
        head = "Premium NOT extended in the panel after a renewal (DB is extended)"
        action = "Automatic re-sync to the DB date is scheduled; you get another alert if it fails."
    text = "\n".join([
        head,
        f"user: tg:{tg}",
        f"DB expires_at: {_fmt_dt(end)}",
        f"tariff: {sync_info.get('tariff')}, +{sync_info.get('period_days')}d",
        f"error: {err_text[:300]}",
        action,
    ])
    try:
        from app.services import admin_alerts
        sent = await admin_alerts.send_alert(bot, "payment", text, force=final or _take_forced_alert_slot())
        if sent and tg is not None:
            # P2-25: the delayed legacy check must not alert this premium problem again.
            from app.services.payments import verify_delivery
            verify_delivery.note_alerted(int(tg), "premium")
    except Exception as e:
        logger.warning("RENEWAL_SYNC_ALERT_FAILED: tg=%s %s", tg, e)


async def _renewal_resync_target(telegram_id: int) -> Optional[datetime]:
    """The DB expires_at of a still-active subscription (panel must equal it), else None."""
    import database
    sub = await database.get_subscription_any(telegram_id)
    if not sub or sub.get("status") != "active" or sub.get("is_bypass_only"):
        return None
    exp = sub.get("expires_at")
    if not isinstance(exp, datetime):
        return None
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp if exp > datetime.now(timezone.utc) else None


async def _renewal_resync(sync_info: dict) -> bool:
    """Background re-sync after a failed renewal sync. True = panel updated."""
    tg = int(sync_info["telegram_id"])
    last_err: Optional[BaseException] = None
    for delay in RESYNC_DELAYS_S:
        await _resync_sleep(delay)
        try:
            target = await _renewal_resync_target(tg)
            if target is None:
                logger.warning("RENEWAL_RESYNC_SKIPPED: tg=%s — subscription no longer active", tg)
                return False
            await _sync_renewal_once({**sync_info, "subscription_end": target})
        except Exception as e:  # noqa: BLE001 — retried, then alerted
            last_err = e
            logger.warning("RENEWAL_RESYNC_ATTEMPT_FAILED: tg=%s %s: %s", tg, type(e).__name__, e)
            continue
        logger.warning("RENEWAL_RESYNC_RECOVERED: tg=%s expireAt=%s", tg, _fmt_dt(target))
        bot = _alert_bot()
        if bot is not None:
            try:
                from app.services import admin_alerts
                await admin_alerts.send_alert(
                    bot, "payment",
                    f"Premium re-sync recovered\nuser: tg:{tg}\nexpireAt: {_fmt_dt(target)}",
                    force=False,
                )
            except Exception:
                pass
        return True
    await _report_renewal_sync_failure(sync_info, last_err, final=True)
    return False


def _schedule_renewal_resync(sync_info: dict) -> None:
    """One pending background re-sync per user (a later failure reuses it: the
    target is re-read from the DB anyway)."""
    try:
        tg = int(sync_info["telegram_id"])
        if tg in _resync_tasks and not _resync_tasks[tg].done():
            return
        task = asyncio.get_running_loop().create_task(_renewal_resync(dict(sync_info)))
    except Exception as e:  # no running loop / bad payload — the alert is already out
        logger.warning("RENEWAL_RESYNC_NOT_SCHEDULED: %s", e)
        return
    _resync_tasks[tg] = task

    def _forget(done_task, _tg=tg):
        if _resync_tasks.get(_tg) is done_task:
            _resync_tasks.pop(_tg, None)
    task.add_done_callback(_forget)


async def _notify_admin_bypass_failed(
    telegram_id: int,
    tariff: str,
    status: int,
    error: Optional[str],
) -> None:
    """DM админу что bypass не создался — premium у юзера работает,
    но bypass tier требует ручной добэкфилл (кнопка в dashboard
    users → tools или через reconciliation flow).

    08 #25: plain text through admin_alerts (budget + digest) — the panel's
    answer used to go into HTML unescaped, and a «<» in it (an nginx error
    page) made Telegram reject the alert; plus a payment_errors row."""
    try:
        import database
        await database.log_payment_error(
            stage="bypass_create_failed",
            telegram_id=telegram_id,
            error_code=str(status)[:120],
            error_message=(error or "unknown")[:500],
            raw_payload={"tariff": tariff},
        )
    except Exception as e:
        logger.warning("bypass-fail payment_errors row failed: %s", type(e).__name__)
    try:
        from app.services import admin_alerts
        bot = _alert_bot()
        if bot is None:
            logger.error("BYPASS_CREATE_FAILED_ALERT_NO_BOT: tg=%s", telegram_id)
            return
        await admin_alerts.send_alert(
            bot, "vpn_api",
            "\n".join([
                "Bypass NOT created (the premium key works)",
                f"user: tg:{telegram_id}",
                f"tariff: {tariff}",
                f"status: {status}",
                f"error: {(error or 'unknown')[:300]}",
                "Action: dashboard → Юзеры → карточка → «Резолв bypass».",
            ]),
            force=_take_forced_alert_slot(),
        )
    except Exception as e:
        logger.warning("bypass-fail admin-notify failed: %s", e)


__all__ = ["provision_subscription", "sync_renewal_to_remnawave"]
