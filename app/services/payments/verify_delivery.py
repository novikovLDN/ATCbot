"""Верификация выдачи после платежа: реально ли применились GB в панели.

После combo/traffic-pack покупок делает GET user из Remnawave и сверяет
`trafficLimitBytes` c ожидаемым. При mismatch — DM админу с деталями,
чтобы можно было расследовать.

Fire-and-forget: verify запускается create_task после basic-flow, ни в
коем случае не блокирует основной путь платежа.

Legacy (flag-off) paths — balance, Telegram, auto-renewal, admin grant,
gift: schedule_legacy_check(tg, source=…) after their post-commit panel
work → check_legacy_delivery re-reads the COMMITTED subscription and the
panel after LEGACY_CHECK_DELAY_S: premium expireAt earlier than DB
expires_at by > 5 min, premium absent / not ACTIVE, or (expect_bypass)
bypass absent / not ACTIVE → forced DELIVERY_MISMATCH alert within the shared
per-window budget of the provisioning alert aggregation (kind
"legacy_delivery"); beyond it, without a bot or on a failed send the alert goes
into ONE digest flushed by the provisioning worker — never silently dropped
(P2-12). There is no hourly reconciler any more (removed in 98089fdc): every
delivery is checked here, per purchase. No retries on these paths — the alert
is the action item. Outbox jobs are
verified inside provisioning.apply (retry + alert), not here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import config

logger = logging.getLogger(__name__)


def _fmt_gb(byte_val: int) -> str:
    """Bytes → человекочитаемое GB (гибибайт)."""
    return f"{byte_val / (1024 ** 3):.2f} GB"


async def _send_admin_alert(title: str, body: str, tag: str = "delivery") -> None:
    """DM админу. Fail-safe, не бросает."""
    try:
        from aiogram import Bot
        from app.api import telegram_webhook
        bot: Optional[Bot] = getattr(telegram_webhook, "_bot", None)
        if bot is None or not getattr(config, "ADMIN_TELEGRAM_ID", 0):
            return
        text = f"⚠️ <b>{title}</b>\n{body}"
        await bot.send_message(
            chat_id=config.ADMIN_TELEGRAM_ID,
            text=text[:4000],
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("admin alert send failed: %s", e)


async def verify_bypass_delivery(
    *,
    telegram_id: int,
    provider: str,
    kind: str,               # "combo" | "traffic_pack"
    expected_added_bytes: int,
    baseline_bytes: Optional[int] = None,
    purchase_id: str = "",
    tariff: str = "",
    period_days: Optional[int] = None,
    delay_sec: float = 3.0,
) -> None:
    """Проверить что bypass entity в панели действительно получил GB.

    Args:
      expected_added_bytes: сколько ГБ должно было добавиться (в bytes).
      baseline_bytes: если известен current до операции — проверяем
        что реальный = baseline + expected. Иначе — что реальный
        >= expected (для новых entities).
    """
    import asyncio as _aio
    # Дадим панели время применить PATCH.
    await _aio.sleep(max(0.1, delay_sec))
    try:
        from app.services import remnawave_api
        # Читаем ТУ ЖЕ bypass-энтити, что патчит add_bypass_traffic —
        # get_bypass_entity_safe (username=str(tg), self-heal id/uuid).
        # Раньше verify резолвил через remnawave_uuid и при контаминации
        # колонок мерил ДРУГУЮ энтити → ложные mismatch-алерты.
        entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
        if not isinstance(entity, dict):
            await _send_admin_alert(
                "Bypass verify FAIL: entity not in panel",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"Kind: <b>{kind}</b> · Provider: <b>{provider}</b>\n"
                    f"Tariff: <b>{tariff}</b>{f' · {period_days}d' if period_days else ''}\n"
                    f"Purchase: <code>{purchase_id}</code>\n"
                    f"Expected +{_fmt_gb(expected_added_bytes)} bypass\n"
                    "get_bypass_entity_safe вернул пусто — bypass entity "
                    "(username=str(tg)) в панели нет. Проверить PURCHASE_FLOW."
                ),
            )
            return
        actual_bytes = int(entity.get("trafficLimitBytes") or 0)
        # Ok-условия — алертим ТОЛЬКО на недостачу (юзер заплатил, но GB не долетели).
        # Излишек (actual > expected) — не проблема: юзер получил больше, чем
        # ожидалось (обычно потому что baseline_bytes был снят до другого top-up'а,
        # или entity уже была наполнена сторонним источником). Спам админу не нужен.
        TOLERANCE = 100 * 1024 * 1024  # 100 MB допуск на округления/race
        if baseline_bytes is not None:
            expected_total = int(baseline_bytes) + int(expected_added_bytes)
        else:
            expected_total = int(expected_added_bytes)
        diff = actual_bytes - expected_total
        # ok = нет НЕДОСТАЧИ больше tolerance. Излишек любого размера — ок.
        ok = diff >= -TOLERANCE

        if ok:
            logger.info(
                "BYPASS_VERIFY_OK: tg=%s kind=%s actual=%s expected=%s diff=%s",
                telegram_id, kind, actual_bytes, expected_total, diff,
            )
            return

        # diff < -TOLERANCE → реальная недостача, юзер оплатил и не получил GB.
        await _send_admin_alert(
            "Bypass verify MISMATCH — недостача",
            (
                f"User: <code>tg:{telegram_id}</code>\n"
                f"Kind: <b>{kind}</b> · Provider: <b>{provider}</b>\n"
                f"Tariff: <b>{tariff}</b>{f' · {period_days}d' if period_days else ''}\n"
                f"Purchase: <code>{purchase_id}</code>\n"
                f"\n"
                f"Ожидалось: <b>{_fmt_gb(expected_total)}</b>"
                + (f" (был {_fmt_gb(baseline_bytes)} + {_fmt_gb(expected_added_bytes)})"
                   if baseline_bytes is not None else f" (пакет {_fmt_gb(expected_added_bytes)})") +
                f"\nВ панели: <b>{_fmt_gb(actual_bytes)}</b>\n"
                f"Недостача: <b>{_fmt_gb(abs(diff))}</b>\n"
                f"\n"
                f"Резолв через дашборд Юзеры → карточка."
            ),
        )
    except Exception as e:
        logger.exception("verify_bypass_delivery failed tg=%s: %s", telegram_id, e)


async def verify_premium_delivery(
    *,
    telegram_id: int,
    provider: str,
    expected_expire_at: datetime,
    purchase_id: str = "",
    tariff: str = "",
    period_days: Optional[int] = None,
    delay_sec: float = 3.0,
) -> None:
    """Проверить что premium entity в панели имеет корректный expireAt.

    Допуск: 5 минут (округления в панели / временной drift).
    """
    import asyncio as _aio
    await _aio.sleep(max(0.1, delay_sec))
    try:
        import database
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_premium_uuid(telegram_id)
        # Self-heal: если DB пусто — резолвим premium entity по username=
        # tg_{tg_id}_premium из панели, пишем uuid/id в БД, продолжаем.
        if not rmn_uuid:
            try:
                from app.services.remnawave_premium import build_premium_username
                pname = build_premium_username(telegram_id)
                by_name = await remnawave_api.find_user_by_username(pname)
                if by_name and isinstance(by_name, dict):
                    api_uuid = by_name.get("uuid") or by_name.get("vlessUuid")
                    api_id = by_name.get("id")
                    if api_uuid:
                        await database.set_remnawave_premium_uuid(
                            telegram_id, str(api_uuid), mark_migrated=False,
                        )
                        rmn_uuid = str(api_uuid)
                    if api_id is not None:
                        try:
                            await database.set_remnawave_premium_id(telegram_id, int(api_id))
                        except (TypeError, ValueError):
                            pass
                    logger.info(
                        "PREMIUM_VERIFY_SELFHEAL: tg=%s resolved by username=%s → uuid=%s id=%s",
                        telegram_id, pname, str(api_uuid or "")[:8], api_id,
                    )
            except Exception as e:
                logger.warning("premium verify self-heal failed tg=%s: %s", telegram_id, e)
        if not rmn_uuid:
            await _send_admin_alert(
                "Premium verify FAIL: no premium_uuid",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"Provider: <b>{provider}</b> · Tariff: <b>{tariff}</b>"
                    f"{f' · {period_days}d' if period_days else ''}\n"
                    f"Purchase: <code>{purchase_id}</code>\n"
                    "В subscriptions.remnawave_premium_uuid пусто И по username "
                    "в панели тоже entity нет — premium не создался. Проверить "
                    "логи PURCHASE_FLOW."
                ),
            )
            return
        entity = await remnawave_api.get_user(rmn_uuid)
        if not entity:
            await _send_admin_alert(
                "Premium verify FAIL: entity not in panel",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"UUID: <code>{str(rmn_uuid)[:16]}</code>\n"
                    "GET вернул пусто — id/uuid stale."
                ),
            )
            return
        panel_expire_raw = entity.get("expireAt")
        if not panel_expire_raw:
            await _send_admin_alert(
                "Premium verify: expireAt пусто в панели",
                f"tg={telegram_id}, ожидалось {expected_expire_at.isoformat()}",
            )
            return
        try:
            panel_expire = datetime.fromisoformat(str(panel_expire_raw).replace("Z", "+00:00"))
            if panel_expire.tzinfo is None:
                panel_expire = panel_expire.replace(tzinfo=timezone.utc)
        except Exception:
            panel_expire = None
        if panel_expire is None:
            await _send_admin_alert(
                "Premium verify: не удалось распарсить expireAt",
                f"tg={telegram_id}, raw={panel_expire_raw!r}",
            )
            return
        # Допуск 5 минут.
        diff_sec = abs((panel_expire - expected_expire_at).total_seconds())
        if diff_sec <= 300:
            logger.info(
                "PREMIUM_VERIFY_OK: tg=%s panel=%s expected=%s",
                telegram_id, panel_expire.isoformat(), expected_expire_at.isoformat(),
            )
            return
        await _send_admin_alert(
            "Premium verify MISMATCH: expireAt",
            (
                f"User: <code>tg:{telegram_id}</code>\n"
                f"Provider: <b>{provider}</b> · Tariff: <b>{tariff}</b>"
                f"{f' · {period_days}d' if period_days else ''}\n"
                f"Purchase: <code>{purchase_id}</code>\n"
                f"\n"
                f"Ожидалось: <code>{expected_expire_at.isoformat()}</code>\n"
                f"В панели:  <code>{panel_expire.isoformat()}</code>\n"
                f"Diff: <b>{int(diff_sec)}s</b>"
            ),
        )
    except Exception as e:
        logger.exception("verify_premium_delivery failed tg=%s: %s", telegram_id, e)


# ── legacy (flag-off) paths: delayed check against the committed DB ────

MISMATCH_TAG = "DELIVERY_MISMATCH"
LEGACY_CHECK_DELAY_S = 20.0          # after renew_*_bg / add_bypass_traffic of the same path
PREMIUM_TOLERANCE = timedelta(minutes=5)
_clock = time.monotonic              # tests patch it
_tasks: set = set()                  # strong refs to the running checks


# P2-25: one incident = one alert. A part ("premium" / "bypass") of a user that
# another legacy path has ALREADY alerted — renewal sync failure
# (purchase_flow._report_renewal_sync_failure, which also schedules the
# background re-sync) or a failed bypass top-up
# (remnawave_service._alert_bypass_not_delivered) — is dropped from the delayed
# legacy check for ALERTED_TTL_S (longer than the whole re-sync: 60+300+900 s;
# a re-sync that gives up sends its own final alert).
ALERTED_TTL_S = 1800.0
_alerted: dict = {}                  # (telegram_id, part) → monotonic time of the sent alert


def note_alerted(telegram_id: int, part: str) -> None:
    """Record that `part` of this user's delivery problem was alerted. Never raises."""
    try:
        now = _clock()
        if len(_alerted) > 1000:
            for k in [k for k, at in _alerted.items() if now - at >= ALERTED_TTL_S]:
                _alerted.pop(k, None)
        _alerted[(int(telegram_id), part)] = now
    except Exception as e:  # noqa: BLE001
        logger.warning("DELIVERY_NOTE_ALERTED_FAILED: tg=%s part=%s %s", telegram_id, part, e)


def recently_alerted(telegram_id: int, part: str) -> bool:
    at = _alerted.get((int(telegram_id), part))
    if at is None:
        return False
    if _clock() - at >= ALERTED_TTL_S:
        _alerted.pop((int(telegram_id), part), None)
        return False
    return True


def reset_legacy_alert_state() -> None:
    """Forget the already-alerted parts and the alert budget / digests (tests)."""
    _alerted.clear()
    from app.services import provisioning
    provisioning.reset_alert_state()


def premium_fix_sql(telegram_ids: Iterable[int], *, tag: str) -> str:
    """Outbox job per user with premium_until = DB expires_at: the provisioning
    worker PATCHes the panel up to max(premium_until, subscriptions.expires_at)
    (never shortens; creates a missing premium entity). Needs migration 082."""
    ids = ",".join(str(int(t)) for t in telegram_ids)
    return (
        "INSERT INTO provisioning_jobs (idempotency_key, telegram_id, source, tariff_key, premium_until) "
        f"SELECT 'fix:{tag}:' || telegram_id, telegram_id, 'manual_fix', 'manual', expires_at "
        f"FROM subscriptions WHERE telegram_id = ANY(ARRAY[{ids}]) "
        "AND expires_at > (NOW() AT TIME ZONE 'UTC') "
        "ON CONFLICT (idempotency_key) DO NOTHING;"
    )


def schedule_legacy_check(
    telegram_id: int,
    *,
    source: str,
    ref: str = "",
    expect_bypass: bool = False,
    delay_sec: Optional[float] = None,
) -> None:
    """Fire-and-forget check_legacy_delivery. Never raises, never blocks."""
    try:
        if not getattr(config, "REMNAWAVE_ENABLED", False):
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(check_legacy_delivery(
            int(telegram_id), source=source, ref=ref, expect_bypass=expect_bypass,
            delay_sec=LEGACY_CHECK_DELAY_S if delay_sec is None else delay_sec,
        ))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as e:
        logger.warning("DELIVERY_CHECK_SCHEDULE_FAILED: tg=%s source=%s %s", telegram_id, source, e)


def _utc(dt: Any) -> Optional[datetime]:
    try:
        if not isinstance(dt, datetime):
            dt = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_delta(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    return f"{hours // 24}d {hours % 24}h" if hours >= 24 else f"{int(delta.total_seconds() // 60)} min"


async def check_legacy_delivery(
    telegram_id: int,
    *,
    source: str,
    ref: str = "",
    expect_bypass: bool = False,
    delay_sec: float = LEGACY_CHECK_DELAY_S,
    bot=None,
) -> Optional[str]:
    """Returns the mismatch alert text (also sent), or None. Never raises
    (except cancellation)."""
    await asyncio.sleep(max(0.0, float(delay_sec)))
    try:
        return await _check_legacy(int(telegram_id), source=source, ref=ref,
                                   expect_bypass=expect_bypass, bot=bot)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("DELIVERY_CHECK_ERROR: tg=%s source=%s %s", telegram_id, source, e)
        return None


async def _check_legacy(tg: int, *, source: str, ref: str, expect_bypass: bool, bot) -> Optional[str]:
    import database
    from app.services import remnawave_api

    sub = await database.get_subscription_any(tg)
    if not sub or sub.get("status") != "active" or sub.get("activation_status") == "pending":
        return None
    db_exp = _utc(sub.get("expires_at")) if sub.get("expires_at") else None
    if db_exp is None or db_exp <= datetime.now(timezone.utc):
        return None

    problems = []
    premium_fixable = False
    if not sub.get("is_bypass_only"):
        state, ent = await remnawave_api.get_premium_state(tg)
        if state == "unavailable":
            logger.warning("DELIVERY_CHECK_SKIPPED: tg=%s source=%s panel unavailable", tg, source)
            return None
        if state != "present" or not isinstance(ent, dict):
            problems.append("premium: entity absent")
            premium_fixable = True
        else:
            panel_exp = _utc(ent.get("expireAt")) if ent.get("expireAt") else None
            status = str(ent.get("status") or "?").upper()
            if panel_exp is None:
                problems.append(f"premium: unreadable expireAt {ent.get('expireAt')!r}, status {status}")
                premium_fixable = True
            elif panel_exp < db_exp - PREMIUM_TOLERANCE:
                problems.append(
                    f"premium: DB expires_at {_fmt_dt(db_exp)}, panel expireAt {_fmt_dt(panel_exp)} "
                    f"(short by {_fmt_delta(db_exp - panel_exp)}), status {status}"
                )
                premium_fixable = True
            elif status != "ACTIVE":
                problems.append(f"premium: expireAt ok ({_fmt_dt(panel_exp)}), status {status}")
    bypass_problem = False
    if expect_bypass:
        state, ent = await remnawave_api.get_bypass_state(tg)
        if state == "unavailable":
            logger.warning("DELIVERY_CHECK_BYPASS_SKIPPED: tg=%s source=%s panel unavailable", tg, source)
        elif state != "present" or not isinstance(ent, dict):
            problems.append("bypass: entity absent")
            bypass_problem = True
        else:
            status = str(ent.get("status") or "?").upper()
            if status != "ACTIVE":
                problems.append(f"bypass: status {status}, trafficLimitBytes={ent.get('trafficLimitBytes')}")
                bypass_problem = True

    known = [p for p in problems if recently_alerted(tg, p.split(":", 1)[0])]
    if known:
        # P2-25: already alerted by the sync / top-up failure of the same incident.
        logger.warning(
            "DELIVERY_CHECK_ALREADY_ALERTED: tg=%s source=%s ref=%s %s",
            tg, source, ref, "; ".join(known),
        )
        problems = [p for p in problems if p not in known]
        premium_fixable = premium_fixable and any(p.startswith("premium") for p in problems)
        bypass_problem = bypass_problem and any(p.startswith("bypass") for p in problems)
    if not problems:
        if not known:
            logger.info("DELIVERY_CHECK_OK: tg=%s source=%s ref=%s", tg, source, ref)
        return None

    lines = [
        f"{MISMATCH_TAG} (legacy path): paid/granted, but the panel does not show it",
        f"source: {source}" + (f" · ref: {ref}" if ref else ""),
        f"user: tg:{tg}",
        *[f"- {p}" for p in problems],
        "",
        "This path has no automatic retry — this alert is the action item.",
    ]
    if premium_fixable:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        lines += ["Fix premium (provisioning worker; never shortens, creates if absent):",
                  premium_fix_sql([tg], tag=stamp)]
    elif any(p.startswith("premium") for p in problems):
        lines.append("Premium not ACTIVE: check in the panel whether it was disabled on purpose; enable if not.")
    if bypass_problem:
        lines.append("Fix bypass: dashboard → Юзеры → карточка → «Резолв bypass» / Traffic audit.")
    text = "\n".join(lines)
    logger.critical("DELIVERY_MISMATCH_LEGACY: tg=%s source=%s ref=%s %s", tg, source, ref, "; ".join(problems))
    await _send_legacy_alert(text, bot, telegram_id=tg, reason=f"{source}: {'; '.join(problems)}")
    return text


def _resolve_bot(bot):
    if bot is not None:
        return bot
    try:
        from app.api import payment_webhook, telegram_webhook
        return getattr(payment_webhook, "_bot", None) or getattr(telegram_webhook, "_bot", None)
    except Exception:
        return None


async def _send_legacy_alert(text: str, bot, *, telegram_id: Optional[int] = None,
                             reason: str = "") -> None:
    """P2-12: forced alert within the shared per-window budget (provisioning
    alert aggregation, kind "legacy_delivery"). Over the budget, without a bot
    or on a failed send it is buffered for ONE digest — before, it went out
    without force and the category cooldown could drop it silently."""
    target = _resolve_bot(bot)
    if target is None:
        logger.critical("DELIVERY_MISMATCH_NO_BOT: alert buffered for the digest (logged above)")
    try:
        from app.services import provisioning
        await provisioning.report_payment_alert(
            "legacy_delivery", text, reason=reason or text.split("\n", 1)[0],
            telegram_id=telegram_id, key=reason.split(":", 1)[0] if reason else None, bot=target,
        )
    except Exception as e:
        logger.warning("DELIVERY_MISMATCH_ALERT_FAILED: %s", e)
