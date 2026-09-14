"""Модуль для автопродления подписок с баланса"""
import asyncio
import contextlib
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app import i18n
from app.services.notifications import service as notification_service
from app.services.language_service import resolve_user_language
from app.services import provisioning_flags, tariffs
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection

logger = logging.getLogger(__name__)

# Event loop protection: max iteration time (prevents 300s blocking)
MAX_ITERATION_SECONDS = int(os.getenv("AUTO_RENEWAL_MAX_ITERATION_SECONDS", "15"))
# Hard timeout for entire iteration (prevents hung worker holding DB, avoids liveness watchdog)
ITERATION_HARD_TIMEOUT_SECONDS = 120.0
BATCH_SIZE = 100
_worker_lock = asyncio.Lock()

# Конфигурация интервала проверки автопродления (5-15 минут, по умолчанию 10 минут)
AUTO_RENEWAL_INTERVAL_SECONDS = int(os.getenv("AUTO_RENEWAL_INTERVAL_SECONDS", "600"))  # 10 минут
if AUTO_RENEWAL_INTERVAL_SECONDS < 300:  # Минимум 5 минут
    AUTO_RENEWAL_INTERVAL_SECONDS = 300
if AUTO_RENEWAL_INTERVAL_SECONDS > 900:  # Максимум 15 минут
    AUTO_RENEWAL_INTERVAL_SECONDS = 900

# Окно для автопродления: проверяем подписки, истекающие в течение этого времени (по умолчанию 6 часов)
RENEWAL_WINDOW_HOURS = int(os.getenv("RENEWAL_WINDOW_HOURS", "6"))
if RENEWAL_WINDOW_HOURS < 1:
    RENEWAL_WINDOW_HOURS = 1
RENEWAL_WINDOW = timedelta(hours=RENEWAL_WINDOW_HOURS)

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 300  # seconds (half of AUTO_RENEWAL_INTERVAL_SECONDS minimum)


# ── T12: auto-renewal through the provisioning outbox (flag "autorenew") ──
# docs/audit/02_payment_core_plan.md §A flow 4, §G0. Used only when
# provisioning_flags.is_on("autorenew"); flag off → the legacy path below, unchanged.


class _OutboxRenewalAborted(Exception):
    """Rolls back one user's savepoint without an alert (log + skip). Both paths:
    decrease_balance(conn) swallows SQL errors (deadlock …) as False."""


async def _tx_usable(conn) -> bool:
    """False when the batch transaction is aborted (an SQL error outside a savepoint)."""
    try:
        await conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@contextlib.asynccontextmanager
async def _batch_transaction(conn, bot, renewed_items: list, deferred_alerts: list):
    """The batch transaction of process_auto_renewals (N1, docs/audit/06_bug_hunt.md).

    Phase B (messages, panel sync) runs only after this block exits normally, i.e.
    after a REAL commit:
      * the last statement before COMMIT is `SELECT 1`: Postgres turns COMMIT of an
        aborted transaction into a silent ROLLBACK, so it would otherwise "succeed";
      * any batch-level failure rolls back every claim / debit / renewal of the
        batch, sends ONE forced alert and re-raises — the next run retries them.
    Per-user alerts (deferred_alerts) are sent after the transaction, not inside it.
    """
    try:
        try:
            async with conn.transaction():
                yield
                await conn.execute("SELECT 1")
        except Exception as e:
            logger.error("AUTO_RENEWAL_BATCH_ROLLED_BACK %s: %s", type(e).__name__, e)
            try:
                from app.services.admin_alerts import send_alert
                users = ", ".join(str(i["telegram_id"]) for i in renewed_items) or "—"
                await send_alert(
                    bot, "payment",
                    "Auto-renewal batch rolled back — nothing committed, no user was "
                    "notified, the panel was not touched.\n"
                    f"Renewals undone in this batch: {users}\n"
                    f"Error: {type(e).__name__}: {str(e)[:200]}\n"
                    "The batch is retried on the next run.",
                    force=True,
                )
            except Exception:
                pass
            raise
    finally:
        for text in deferred_alerts:
            try:
                from app.services.admin_alerts import send_alert
                await send_alert(bot, "payment", text, force=True)
            except Exception:
                pass


def _outbox_renewal_plan(subscription, tariff_type: str, period_days: int) -> dict:
    """What to bill and provision for this subscription (flag ON).

    Tariff key = the subscription's REAL tariff: subscription_type + is_combo via
    tariffs.tariff_key (legacy biz_* → "plus"). Period = the one the legacy
    code derives from the last payment (tariff_type / period_days after its fallback).

    Base price — the caller applies the SAME personal discount to it as the legacy path:
      basic / plus (legacy biz = plus) → config.TARIFFS[tariff_type][period_days], exactly
          today's number (non-combo prices do not change);
      combo_basic / combo_plus → tariffs.renewal_price_rub(key, period) — the COMBO
          price (owner decision §G0: combo users were billed the basic price).
    There is no admin price override for combo (pricing.get_effective_price knows
    only basic/plus, and today's auto-renewal does not use it for basic/plus either).

    Raises TariffConfigError (unknown subscription_type / period) → the caller's
    per-user error path: alert, nothing billed.
    """
    is_combo = bool(subscription.get("is_combo"))
    sub_type = (subscription.get("subscription_type") or tariff_type).strip().lower()
    key = tariffs.tariff_key(sub_type, is_combo)
    ent = tariffs.for_purchase(key, period_days)
    if key in tariffs.COMBO_KEYS:
        tier_label = "Basic" if ent.premium_tier == "basic" else "Plus"
        return {
            "key": key, "ent": ent, "period_days": period_days, "is_combo": True,
            "base_price": tariffs.renewal_price_rub(key, period_days),
            # combo purchases record payments.tariff as "<base tier>_<days>"
            # (finalize_purchase / finalize_balance_purchase) — the next renewal
            # parses the period from it the same way.
            "payment_tariff": f"{ent.premium_tier}_{period_days}",
            "label": f"Комбо {tier_label}",
        }
    # The price / label of the tariff actually provisioned (the subscription's own
    # tier), not of the tier parsed from the payment history (P1, 2026-09-14).
    return {
        "key": key, "ent": ent, "period_days": period_days, "is_combo": False,
        "base_price": config.TARIFFS[key][period_days]["price"],
        "payment_tariff": f"{key}_{period_days}",
        "label": "Basic" if key == "basic" else "Plus",
    }


async def _autorenew_via_outbox(conn, *, telegram_id: int, language, plan: dict,
                                amount_rubles: float) -> dict | None:
    """One user's renewal inside the batch transaction (flag ON).

    SAVEPOINT (nested conn.transaction()): debit → grant_access(defer_panel=True,
    DB only) → INSERT payments → provisioning.enqueue("autorenew:{payment_id}").
    Any failure rolls back all four for this user only; the rest of the batch is
    unaffected. Zero HTTP here — the panel is updated post-commit by
    provisioning.run_now / the provisioning worker.
    Returns the Phase-B item, or None when nothing was billed. Errors other than a
    refused debit propagate to the caller's per-user handler (log + admin alert).
    """
    from app.services import provisioning

    ent = plan["ent"]
    period_days = plan["period_days"]
    duration = timedelta(days=period_days)
    months = period_days // 30
    try:
        async with conn.transaction():
            debited = await database.decrease_balance(
                telegram_id=telegram_id,
                amount=amount_rubles,
                source="auto_renew",
                description=f"Автопродление подписки {plan['label']} на {months} месяц(ев)",
                conn=conn,
            )
            if not debited:
                # decrease_balance(conn) swallows SQL errors as False → roll the savepoint back
                raise _OutboxRenewalAborted("decrease_balance returned False")
            result = await database.grant_access(
                telegram_id=telegram_id,
                duration=duration,
                source="auto_renew",
                admin_telegram_id=None,
                admin_grant_days=None,
                conn=conn,
                _caller_holds_transaction=True,
                tariff=ent.premium_tier,
                defer_panel=True,
                tariff_period_days=period_days,  # calendar months
            )
            expires_at = result.get("subscription_end")
            if result.get("action") != "renewal" or result.get("vless_url") is not None or expires_at is None:
                raise RuntimeError(
                    f"auto-renewal grant_access returned action={result.get('action')!r}, "
                    f"expires_at={expires_at!r} — rolled back, nothing billed"
                )
            payment_id = await conn.fetchval(
                "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                telegram_id, plan["payment_tariff"], round(amount_rubles * 100)
            )
            if not payment_id:
                raise RuntimeError("auto-renewal payments INSERT returned no id — rolled back, nothing billed")
            # Owner rule 2026-09-14 (N17): a renewal paid from balance is a purchase →
            # referral cashback, once per payment, in this savepoint.
            from database.users import process_referral_reward
            await process_referral_reward(
                buyer_id=telegram_id, purchase_id=f"autorenew_{payment_id}",
                amount_rubles=amount_rubles, conn=conn,
            )
            job_id = await provisioning.enqueue(
                conn,
                key=f"autorenew:{payment_id}",
                telegram_id=telegram_id,
                ent=ent,
                premium_until=expires_at,
                source="autorenew",
                context={
                    "payment_id": payment_id,
                    "amount_rubles": amount_rubles,
                    "period_days": period_days,
                    "renewal_tariff_key": plan["key"],
                },
            )
    except _OutboxRenewalAborted as e:
        logger.error(f"Failed to decrease balance for auto-renewal: user={telegram_id} ({e})")
        return None

    logger.info(
        "AUTO_RENEWAL_OUTBOX_OK: user=%s tariff=%s period_days=%s amount=%.2f RUB payment_id=%s job=%s expires_at=%s",
        telegram_id, plan["key"], period_days, amount_rubles, payment_id, job_id, expires_at.isoformat(),
    )
    return {
        "telegram_id": telegram_id,
        "payment_id": payment_id,
        "language": language,
        "expires_str": expires_at.strftime("%d.%m.%Y"),
        "expires_at": expires_at,
        "duration_days": duration.days,
        "amount_rubles": amount_rubles,
        "tariff_type": ent.premium_tier,
        "period_days": period_days,
        "is_combo": plan["is_combo"],
        "xray_sync": None,
        "provisioning_job_id": job_id,
    }


async def renewal_quote(conn, telegram_id: int, subscription) -> dict:
    """What auto-renewal will bill for this subscription: {tariff_type,
    period_days, base_price, amount_rubles, outbox_plan}. DB reads on `conn`
    only (no HTTP). One rule for the renewal itself and for the reminders that
    tell the user «спишем N ₽» / «не хватает N ₽» (#8). Raises TariffConfigError
    from the outbox plan like before (the caller's per-user error path)."""
    # The last SUBSCRIPTION payment: the last approved payment of any
    # kind was a top-up / gift / GB pack / farm shield often enough,
    # and it parsed as «basic, 30 days» (P1, 2026-09-14).
    last_payment = await database.get_last_subscription_payment(telegram_id, conn=conn)

    # Парсим тариф из последнего платежа подписки
    # Формат может быть: "basic_30", "plus_90" или legacy "1", "3", "6", "12"
    if not last_payment:
        tariff_type = "basic"
        period_days = 30
    else:
        # Legacy biz_* payments renew as Plus at today's Plus price
        # (owner 2026-09-14; the old "falls back to basic 199 ₽" is gone).
        tariff_str = tariffs.normalize_payment_tariff(last_payment.get("tariff", "basic_30"))
        if "_" in tariff_str:
            parts = tariff_str.split("_")
            tariff_type = parts[0] if len(parts) > 0 else "basic"
            try:
                period_days = int(parts[1]) if len(parts) > 1 else 30
            except (ValueError, IndexError):
                period_days = 30
        else:
            tariff_type = "basic"
            try:
                months = int(tariff_str)
                period_days = months * 30
            except ValueError:
                period_days = 30

    if tariff_type not in config.TARIFFS or period_days not in config.TARIFFS[tariff_type]:
        tariff_type = "basic"
        period_days = 30

    base_price = config.TARIFFS[tariff_type][period_days]["price"]

    # T12: USE_NEW_PROVISIONING on for "autorenew" → bill and provision the
    # subscription's REAL tariff through the outbox (_outbox_renewal_plan).
    # The personal discount below applies to its base price unchanged.
    outbox_plan = None
    if provisioning_flags.is_on("autorenew"):
        outbox_plan = _outbox_renewal_plan(subscription, tariff_type, period_days)
        base_price = outbox_plan["base_price"]

    # Owner rule 2026-09-14: VIP removed; the largest single
    # discount wins (database.subscriptions.pick_largest_discount).
    # A renewal takes no promo code, and the special offer is for
    # an ENDED subscription — this one is active: personal only.
    from database.subscriptions import pick_largest_discount
    personal_discount = await database.get_user_discount(telegram_id, conn=conn)
    _kind, discount_percent = pick_largest_discount([
        ("personal", personal_discount["discount_percent"] if personal_discount else 0),
    ])
    if discount_percent:
        amount_rubles = round(base_price * (1 - discount_percent / 100), 2)
    else:
        amount_rubles = float(base_price)
    return {"tariff_type": tariff_type, "period_days": period_days, "base_price": base_price,
            "amount_rubles": amount_rubles, "outbox_plan": outbox_plan}


_FAILURE_NOTICE_COOLDOWN_S = 24 * 3600
_failure_notice_sent_at: dict = {}   # (telegram_id, key) → monotonic time of the last notice


async def _send_autorenew_failure_notices(bot, notices: list) -> None:
    """Tell users whose auto-renewal did not go through (debit refused /
    refunded) — after the batch committed. At most one notice per user and
    kind per 24 h (a refused debit may be retried on the next run). Never raises."""
    now = time.monotonic()
    for telegram_id, key, amount in notices:
        mark = (telegram_id, key)
        if now - _failure_notice_sent_at.get(mark, float("-inf")) < _FAILURE_NOTICE_COOLDOWN_S:
            continue
        _failure_notice_sent_at[mark] = now
        try:
            lang = await resolve_user_language(telegram_id)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n.get_text(lang, "buy.renew_button"), callback_data="menu_buy_vpn")],
                [InlineKeyboardButton(text=i18n.get_text(lang, "main.btn_topup_balance"), callback_data="topup_balance")],
            ])
            await safe_send_message(bot, telegram_id, i18n.get_text(lang, key, amount=float(amount)), reply_markup=kb)
            await asyncio.sleep(0.05)
        except Exception as e:  # noqa: BLE001
            logger.warning("AUTORENEW_FAILURE_NOTICE_FAILED user=%s: %s", telegram_id, type(e).__name__)


async def process_auto_renewals(bot: Bot):
    """
    Обработать автопродление подписок, которые истекают в течение RENEWAL_WINDOW
    
    ТРЕБОВАНИЯ:
    - Подписки со status='active' и auto_renew=TRUE
    - subscription_end <= now + RENEWAL_WINDOW (по умолчанию 6 часов)
    - Проверяем баланс >= цена подписки
    - Если баланса хватает: продлеваем через grant_access() (без создания нового UUID)
    - Если баланса не хватает: ничего не делаем (auto-expiry обработает)
    
    Защита от race conditions:
    - SELECT ... FOR UPDATE SKIP LOCKED: только один воркер может обработать подписку
    - last_auto_renewal_at устанавливается в НАЧАЛЕ транзакции (до обработки)
    - При ошибке транзакция откатывается, last_auto_renewal_at возвращается к предыдущему значению
    - Идемпотентность: при рестарте не будет двойного списания
    - Атомарные транзакции для баланса и подписки
    """
    pool = await database.get_pool()

    # N-02 (docs/notifications/bugs-and-risks.md): NO is_reachable filter here.
    # Renewal is a balance debit + access grant; whether Telegram can deliver the
    # "renewed" message is a separate concern (safe_send_message just fails for
    # a blocked chat). A stale is_reachable=FALSE used to make paid access lapse.
    due_query = """
        SELECT s.*, u.language, u.balance
        FROM subscriptions s
        JOIN users u ON s.telegram_id = u.telegram_id
        WHERE s.status = 'active'
        AND s.auto_renew = TRUE
        AND s.expires_at <= $1
        AND s.expires_at > $2
        AND s.uuid IS NOT NULL
        AND (s.last_auto_renewal_at IS NULL OR s.last_auto_renewal_at < s.expires_at - INTERVAL '12 hours')
        ORDER BY s.id ASC
        LIMIT $3
        FOR UPDATE SKIP LOCKED"""

    # Pool is created with acquire timeout in database._get_pool_config() (DB_POOL_ACQUIRE_TIMEOUT, default 10s).
    # This worker does not call VPN API (no httpx); only DB and Telegram.
    # Pool timeout is already configured (10s); acquire_connection uses pool.acquire() which respects that timeout.
    # For extra safety, we wrap acquire in wait_for to ensure cancellation if pool hangs.
    while True:
        # P1-9: a fresh `now` per batch — grant_access re-checks expiry with its
        # own clock, a stale run-start `now` widens the selection/grant race.
        now = datetime.now(timezone.utc)
        renewal_threshold = now + RENEWAL_WINDOW
        cm = acquire_connection(pool, "auto_renewal_main")
        try:
            conn = await asyncio.wait_for(cm.__aenter__(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("auto_renewal: pool.acquire() timed out after 10s")
            raise
        try:
            notifications_to_send = []
            deferred_alerts = []
            # (telegram_id, i18n key, amount): «auto-renewal did not go through» —
            # sent after the commit (08 #14: a refused debit / a refund was silent).
            failure_notices = []
            async with _batch_transaction(conn, bot, notifications_to_send, deferred_alerts):
                subscriptions = await conn.fetch(
                    due_query,
                    database._to_db_utc(renewal_threshold),
                    database._to_db_utc(now),
                    BATCH_SIZE
                )

                if not subscriptions:
                    break

                if not isinstance(subscriptions, list):
                    logger.error("auto_renewal unexpected fetch result (not list)")
                    break

                logger.info(
                    f"Auto-renewal check: Found {len(subscriptions)} subscriptions expiring within {RENEWAL_WINDOW_HOURS} hours"
                )

                iteration_start = time.monotonic()
                for i, sub_row in enumerate(subscriptions):
                    if i > 0 and i % 50 == 0:
                        await cooperative_yield()
                    if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
                        logger.warning("Auto-renewal iteration time limit reached, breaking early")
                        break
                    telegram_id = sub_row["telegram_id"]
                    subscription = sub_row
                    language = sub_row.get("language", "en")
                    try:
                        # КРИТИЧНО: Обновляем last_auto_renewal_at в НАЧАЛЕ транзакции
                        # Это предотвращает обработку одной подписки несколькими воркерами
                        # даже при рестарте или параллельных вызовах
                        update_result = await conn.execute(
                        """UPDATE subscriptions 
                           SET last_auto_renewal_at = $1 
                           WHERE telegram_id = $2 
                           AND status = 'active'
                           AND auto_renew = TRUE
                           AND (last_auto_renewal_at IS NULL OR last_auto_renewal_at < expires_at - INTERVAL '12 hours')""",
                        database._to_db_utc(now), telegram_id
                        )
                        
                        # Если UPDATE не затронул ни одной строки - подписка уже обрабатывается или не подходит
                        if update_result == "UPDATE 0":
                            logger.debug(f"Subscription {telegram_id} already being processed or conditions changed, skipping")
                            continue
                        
                        # Дополнительная проверка: убеждаемся, что подписка еще не была обработана
                        # (дополнительная защита от race condition)
                        current_sub = await conn.fetchrow(
                            """SELECT auto_renew, expires_at, last_auto_renewal_at 
                               FROM subscriptions 
                               WHERE telegram_id = $1""",
                            telegram_id
                        )
                        
                        if not current_sub or not current_sub["auto_renew"]:
                            logger.debug(f"Subscription {telegram_id} no longer has auto_renew enabled, skipping")
                            # Откатываем транзакцию (last_auto_renewal_at будет откачен)
                            continue
                        
                        # PHASE A: Только DB по conn — без вложенного pool.acquire и без сетевых вызовов
                        quote = await renewal_quote(conn, telegram_id, subscription)
                        tariff_type, period_days = quote["tariff_type"], quote["period_days"]
                        outbox_plan, amount_rubles = quote["outbox_plan"], quote["amount_rubles"]

                        user_balance_kopecks = subscription.get("balance", 0) or 0
                        balance_rubles = user_balance_kopecks / 100.0
                        
                        if balance_rubles >= amount_rubles and outbox_plan is not None:
                            item = await _autorenew_via_outbox(
                                conn,
                                telegram_id=telegram_id,
                                language=language,
                                plan=outbox_plan,
                                amount_rubles=amount_rubles,
                            )
                            if item is not None:
                                notifications_to_send.append(item)
                            else:
                                # The debit was refused (nothing billed): tell the user
                                # and the admin (08 #14 — it was log-only).
                                failure_notices.append((telegram_id, "autorenew.failed_debit", amount_rubles))
                                deferred_alerts.append(
                                    f"Auto-renewal debit failed (nothing billed)\n"
                                    f"User: {telegram_id}\nAmount: {amount_rubles:.2f} RUB\n"
                                    f"Action: check the balance / DB; the user was asked to renew manually."
                                )

                        elif balance_rubles >= amount_rubles:
                            # P1-9: one SAVEPOINT per legacy user, like the outbox path:
                            # a per-user exception (e.g. grant_access INVARIANT_VIOLATION
                            # when the subscription expired between the selection and
                            # this grant) rolls back THIS user's debit; the per-user
                            # handler below alerts, the rest of the batch commits.
                            async with conn.transaction():
                                duration = timedelta(days=period_days)
                                months = period_days // 30
                                tariff_name = "Basic" if tariff_type == "basic" else "Plus"
                                success = await database.decrease_balance(
                                    telegram_id=telegram_id,
                                    amount=amount_rubles,
                                    source="auto_renew",
                                    description=f"Автопродление подписки {tariff_name} на {months} месяц(ев)",
                                    conn=conn
                                )
                            
                                if not success:
                                    # decrease_balance(conn) swallows SQL errors (deadlock …) as
                                    # False. Raise so THIS savepoint rolls back: `continue` issued
                                    # RELEASE on an aborted savepoint, the rest of the batch failed
                                    # and COMMIT silently rolled everything back (N1).
                                    raise _OutboxRenewalAborted("decrease_balance returned False")
                            
                                # M-AUTORENEW-SYNC-IN-TX (docs/audit/03_payment_matrix.md): the
                                # panel sync must run AFTER commit (Phase B). Without
                                # _caller_holds_transaction grant_access synced inline, inside
                                # this batch transaction; a panel failure raised after the
                                # debit + DB extension, the per-user handler swallowed it and
                                # the batch committed a debit without a payments row.
                                result = await database.grant_access(
                                    telegram_id=telegram_id,
                                    duration=duration,
                                    source="auto_renew",
                                    admin_telegram_id=None,
                                    admin_grant_days=None,
                                    conn=conn,
                                    _caller_holds_transaction=True,
                                    tariff_period_days=period_days,  # calendar months
                                )
                            
                                expires_at = result["subscription_end"]
                                action_type = result.get("action", "unknown")
                            
                                if action_type != "renewal" or result.get("vless_url") is not None:
                                    logger.error(
                                        f"Auto-renewal ERROR: UUID was regenerated instead of renewal! "
                                        f"user={telegram_id}, action={action_type}, has_vless_url={result.get('vless_url') is not None}"
                                    )
                                    refund_ok = await database.increase_balance(
                                        telegram_id=telegram_id,
                                        amount=amount_rubles,
                                        source="refund",
                                        description=f"Возврат средств: ошибка автопродления (UUID пересоздан)",
                                        conn=conn
                                    )
                                    if not refund_ok:
                                        logger.critical(
                                            f"REFUND_FAILED: user={telegram_id}, amount={amount_rubles} RUB, "
                                            f"reason=UUID_regenerated, refund_returned=False"
                                        )
                                        from app.services.admin_alerts import alert_payment_failure
                                        await alert_payment_failure(
                                            bot, "auto_renewal", telegram_id,
                                            f"refund_uuid_regen_{telegram_id}",
                                            RuntimeError(f"Refund failed after UUID regeneration, amount={amount_rubles}"),
                                            is_transient=False,
                                            amount_rubles=amount_rubles,
                                            tariff=tariff_type,
                                            period_days=period_days,
                                        )
                                    else:
                                        # 08 #14: the refund went through silently — the user
                                        # did not know the renewal failed, the admin not about the anomaly.
                                        failure_notices.append((telegram_id, "autorenew.failed_refunded", amount_rubles))
                                        deferred_alerts.append(
                                            f"Auto-renewal refunded: grant_access regenerated the key instead of renewing\n"
                                            f"User: {telegram_id}\nAmount: {amount_rubles:.2f} RUB (back on the balance)\n"
                                            f"Action: check the subscription; the user was asked to renew manually."
                                        )
                                    continue
                            
                                subscription_row = await conn.fetchrow(
                                    "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                                    telegram_id
                                )
                                vpn_key = None
                                if subscription_row and subscription_row.get("vpn_key"):
                                    vpn_key = subscription_row["vpn_key"]
                                else:
                                    vpn_key = result.get("uuid", "")
                            
                                if expires_at is None:
                                    logger.error(f"Failed to renew subscription for auto-renewal: user={telegram_id}, expires_at=None")
                                    refund_ok = await database.increase_balance(
                                        telegram_id=telegram_id,
                                        amount=amount_rubles,
                                        source="refund",
                                        description=f"Возврат средств за неудачное автопродление",
                                        conn=conn
                                    )
                                    if not refund_ok:
                                        logger.critical(
                                            f"REFUND_FAILED: user={telegram_id}, amount={amount_rubles} RUB, "
                                            f"reason=expires_at_None, refund_returned=False"
                                        )
                                        from app.services.admin_alerts import alert_payment_failure
                                        await alert_payment_failure(
                                            bot, "auto_renewal", telegram_id,
                                            f"refund_renewal_fail_{telegram_id}",
                                            RuntimeError(f"Refund failed after renewal failure, amount={amount_rubles}"),
                                            is_transient=False,
                                            amount_rubles=amount_rubles,
                                            tariff=tariff_type,
                                            period_days=period_days,
                                        )
                                    else:
                                        failure_notices.append((telegram_id, "autorenew.failed_refunded", amount_rubles))
                                        deferred_alerts.append(
                                            f"Auto-renewal refunded: grant_access returned no expires_at\n"
                                            f"User: {telegram_id}\nAmount: {amount_rubles:.2f} RUB (back on the balance)\n"
                                            f"Action: check the subscription; the user was asked to renew manually."
                                        )
                                    continue
                            
                                # Auto-renewal is funded from the user's internal
                                # balance, so no new money enters the business
                                # here — it was already counted when the balance
                                # was topped up. Deliberately NOT mirrored into
                                # pending_purchases: doing so would count the same
                                # ruble twice (once on top-up, once on renewal).
                                # See database/revenue.py for the cash-in model.
                                tariff_str = f"{tariff_type}_{period_days}"
                                payment_id = await conn.fetchval(
                                    "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                                    telegram_id, tariff_str, round(amount_rubles * 100)
                                )

                                if not payment_id:
                                    logger.error(f"Failed to create payment record for auto-renewal: user={telegram_id}")
                                    continue

                                # Owner rule 2026-09-14 (N17): a renewal paid from balance is a
                                # purchase → referral cashback, once per payment, in this savepoint.
                                from database.users import process_referral_reward
                                await process_referral_reward(
                                    buyer_id=telegram_id, purchase_id=f"autorenew_{payment_id}",
                                    amount_rubles=amount_rubles, conn=conn,
                                )
                            
                                notification_already_sent = await notification_service.check_notification_idempotency(
                                    payment_id, conn=conn
                                )
                                if notification_already_sent:
                                    logger.info(
                                        f"NOTIFICATION_IDEMPOTENT_SKIP [type=auto_renewal, payment_id={payment_id}, user={telegram_id}]"
                                    )
                                    continue

                                expires_str = expires_at.strftime("%d.%m.%Y")
                                duration_days = duration.days
                                # Собираем payload для Phase B (после commit) — без Telegram и без вложенного acquire
                                xray_sync_info = result.get("renewal_xray_sync_after_commit")
                                notifications_to_send.append({
                                    "telegram_id": telegram_id,
                                    "payment_id": payment_id,
                                    "language": language,
                                    "expires_str": expires_str,
                                    "expires_at": expires_at,
                                    "duration_days": duration_days,
                                    "amount_rubles": amount_rubles,
                                    "tariff_type": tariff_type,
                                    "period_days": period_days,
                                    "xray_sync": xray_sync_info,
                                })
                                logger.info(f"Auto-renewal successful: user={telegram_id}, tariff={tariff_type}, period_days={period_days}, amount={amount_rubles} RUB, expires_at={expires_str}")

                        else:
                            logger.debug(f"Insufficient balance for auto-renewal: user={telegram_id}, balance={balance_rubles:.2f} RUB, required={amount_rubles:.2f} RUB")
                    
                    except _OutboxRenewalAborted as e:
                        logger.error(f"Failed to decrease balance for auto-renewal: user={telegram_id} ({e})")
                        # 08 #14: a refused debit burnt the attempt silently.
                        failure_notices.append((telegram_id, "autorenew.failed_debit", amount_rubles))
                        deferred_alerts.append(
                            f"Auto-renewal debit failed (nothing billed)\n"
                            f"User: {telegram_id}\nAmount: {amount_rubles:.2f} RUB\n"
                            f"Action: check the balance / DB; the user was asked to renew manually."
                        )
                    except Exception as e:
                        logger.exception(f"Error processing auto-renewal for user {telegram_id}: {e}")
                        if not await _tx_usable(conn):
                            # An error outside a savepoint aborted the batch transaction:
                            # roll the whole batch back (forced alert in _batch_transaction).
                            raise
                        deferred_alerts.append(
                            f"Auto-renewal processing error\n"
                            f"User: {telegram_id}\n"
                            f"Error: {type(e).__name__}: {str(e)[:200]}"
                        )

            # PHASE B: после commit — xray sync + отправка уведомлений (без финансовых мутаций)
            outbox_fast_path = True
            for item in notifications_to_send:
                # T12 (flag ON): the panel side is the committed outbox job. Fast path
                # here; on failure the job stays queued (retry + alert in run_now) and
                # the provisioning worker completes it. After one failed/skipped
                # run_now the rest of this batch is left to the worker, so a panel
                # outage cannot stall the batch for BATCH_SIZE × run_now timeout.
                outbox_job_id = item.get("provisioning_job_id")
                if outbox_job_id is not None and outbox_fast_path:
                    try:
                        from app.services import provisioning
                        outbox_fast_path = await provisioning.run_now(outbox_job_id, bot=bot)
                    except Exception as e:  # run_now never raises; belt and braces
                        outbox_fast_path = False
                        logger.error(
                            "AUTO_RENEWAL_RUN_NOW_FAILED: job=%s user=%s %s: %s",
                            outbox_job_id, item["telegram_id"], type(e).__name__, e,
                        )
                # B0: Post-commit Remnawave sync (renewal_xray_sync_after_commit
                # emitted by grant_access). ОБЯЗАТЕЛЬНО дёргаем
                # purchase_flow.sync_renewal_to_remnawave — иначе premium
                # expireAt в панели останется старым и ключ умрёт на
                # предыдущей дате даже после успешной DB-renewal.
                # Legacy vpn_utils.ensure_user_in_xray (samopis-мастер) —
                # больше не нужен, samopis мёртв.
                xray_sync = item.get("xray_sync")
                if xray_sync:
                    try:
                        from app.services import purchase_flow
                        await purchase_flow.sync_renewal_to_remnawave(xray_sync)
                    except Exception as e:
                        logger.error(
                            f"AUTO_RENEWAL_PREMIUM_SYNC_FAILED user={item['telegram_id']} error={e}"
                        )
                # Fire-and-forget: renew Remnawave bypass user (extend expireAt
                # для bypass entity — независимо от premium sync выше).
                try:
                    from app.services.remnawave_service import renew_remnawave_user_bg
                    _ar_tariff = item.get("tariff_type", "basic")
                    _ar_expires = item.get("expires_at")
                    if outbox_job_id is None and _ar_tariff in ("basic", "plus") and _ar_expires:
                        renew_remnawave_user_bg(item["telegram_id"], _ar_tariff, _ar_expires, period_days=item.get("period_days", 30))
                except Exception as rmn_err:
                    logger.warning("REMNAWAVE_AUTORENEW_FAIL: tg=%s %s", item["telegram_id"], rmn_err)
                if outbox_job_id is None:  # legacy: delayed panel check, alert-only
                    from app.services.payments import verify_delivery
                    verify_delivery.schedule_legacy_check(item["telegram_id"], source="auto_renewal", expect_bypass=item.get("tariff_type") in ("basic", "plus"))

                try:
                    _ar_is_combo = item.get("is_combo", False)
                    _ar_type = item.get("tariff_type", "basic")
                    user_lang = await resolve_user_language(item["telegram_id"])
                    # RU/EN tariff name and calendar period (08 #14/#21: «Комбо …» and
                    # «👤 Мой профиль» were hardcoded RU; «Срок: 30 дней» for a month).
                    from app.services.payments.success_message import period_display, tariff_display
                    # N-06: the rubles actually debited (both legacy and outbox payloads
                    # carry "amount_rubles"; the old item.get("amount") was always 0).
                    amount_val = item["amount_rubles"]
                    text = i18n.get_text(
                        user_lang, "purchase.auto_renewal_success",
                        tariff_name=tariff_display(user_lang, _ar_type, _ar_is_combo),
                        period=period_display(user_lang, item.get("period_days", 30)),
                        days=item.get("period_days", 30),
                        expires_date=item["expires_str"],
                        amount=amount_val
                    )
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=i18n.get_text(user_lang, "main.profile"), callback_data="menu_profile")],
                    ])
                    sent = await safe_send_message(bot, item["telegram_id"], text, reply_markup=keyboard)
                    if sent is None:
                        continue
                    await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec
                    # Explicit timeout for notification connection acquire (pool timeout is 10s)
                    notify_cm = acquire_connection(pool, "auto_renewal_notify")
                    try:
                        notify_conn = await asyncio.wait_for(notify_cm.__aenter__(), timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.error("auto_renewal: pool.acquire() timed out for notify_conn after 10s")
                        continue
                    try:
                        marked = await notification_service.mark_notification_sent(item["payment_id"], conn=notify_conn)
                        if marked:
                            logger.info(
                                f"NOTIFICATION_SENT [type=auto_renewal, payment_id={item['payment_id']}, user={item['telegram_id']}]"
                            )
                        else:
                            logger.warning(
                                f"NOTIFICATION_FLAG_ALREADY_SET [type=auto_renewal, payment_id={item['payment_id']}, user={item['telegram_id']}]"
                            )
                    finally:
                        # Release notification connection
                        try:
                            await notify_cm.__aexit__(None, None, None)
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(
                        f"CRITICAL: Failed to send/mark auto-renewal notification: payment_id={item.get('payment_id')}, user={item.get('telegram_id')}, error={e}"
                    )
                    try:
                        from app.services.admin_alerts import send_alert
                        await send_alert(
                            bot, "payment",
                            f"Auto-renewal notification failed\n"
                            f"User: {item.get('telegram_id')}\n"
                            f"Payment: {item.get('payment_id')}\n"
                            f"Error: {type(e).__name__}: {str(e)[:200]}"
                        )
                    except Exception:
                        pass
            # «Auto-renewal did not go through» (08 #14) — after the commit.
            await _send_autorenew_failure_notices(bot, failure_notices)
        finally:
            # Release connection (equivalent to __aexit__)
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass  # Ignore errors during cleanup

        await asyncio.sleep(0)


async def auto_renewal_task(bot: Bot):
    """
    Фоновая задача для автопродления подписок
    
    Запускается каждые AUTO_RENEWAL_INTERVAL_SECONDS (по умолчанию 10 минут, минимум 5, максимум 15)
    для проверки подписок, истекающих в течение RENEWAL_WINDOW (по умолчанию 6 часов).
    
    Это обеспечивает:
    - Своевременное продление (частые проверки, не пропустим подписки)
    - Безопасность при рестартах (не будет двойного списания благодаря last_auto_renewal_at)
    - Идемпотентность (повторные вызовы безопасны)
    - Атомарность (баланс и подписка обновляются в одной транзакции)
    - UUID стабильность (продление без пересоздания UUID через grant_access)
    """
    logger.info(
        f"Auto-renewal task started: interval={AUTO_RENEWAL_INTERVAL_SECONDS}s, "
        f"renewal_window={RENEWAL_WINDOW_HOURS}h"
    )
    from app.core import runtime_health  # dashboard liveness (in-memory)
    runtime_health.register(
        "auto_renewal",
        interval_s=AUTO_RENEWAL_INTERVAL_SECONDS + ITERATION_HARD_TIMEOUT_SECONDS,
        initial_delay_s=60,
    )
    
    # Первая проверка сразу при запуске
    try:
        async with _worker_lock:
            await process_auto_renewals(bot)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures don't crash the task
        logger.warning(f"auto_renewal: Initial check failed (DB temporarily unavailable): {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"auto_renewal: Unexpected error in initial check: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("auto_renewal: Full traceback for initial check", exc_info=True)

    # POOL STABILITY: One-time startup jitter to avoid 600s worker alignment burst.
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug(f"auto_renewal: startup jitter done ({jitter_s:.1f}s)")
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        iteration_outcome = "success"
        iteration_error_type = None
        should_exit_loop = False

        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="auto_renewal",
            iteration_number=iteration_number
        )

        try:
            # Feature flag check
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled or not feature_flags.auto_renewal_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Auto-renewal disabled, skipping iteration in auto_renewal "
                    f"(iteration={iteration_number}, workers_enabled={feature_flags.background_workers_enabled}, "
                    f"auto_renewal_enabled={feature_flags.auto_renewal_enabled})"
                )
                iteration_outcome = "skipped"
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue

            # Simple DB readiness check
            if not database.DB_READY:
                logger.warning("auto_renewal: skipping — DB not ready")
                iteration_outcome = "skipped"
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue

            # Wrap entire iteration body so a hung run is cancelled after 2 minutes (avoids holding DB forever, liveness watchdog)
            async def _run_iteration_body():
                async with _worker_lock:
                    await process_auto_renewals(bot)

            try:
                await asyncio.wait_for(_run_iteration_body(), timeout=ITERATION_HARD_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.error(
                    "auto_renewal: iteration timed out after %.0fs (worker=auto_renewal correlation_id=%s)",
                    ITERATION_HARD_TIMEOUT_SECONDS,
                    correlation_id,
                    extra={"worker": "auto_renewal", "correlation_id": correlation_id},
                )
                iteration_outcome = "timeout"
                iteration_error_type = "timeout"
                # Do NOT re-raise; continue to next iteration after finally

        except asyncio.CancelledError:
            logger.info("Auto-renewal task cancelled")
            iteration_outcome = "cancelled"
            should_exit_loop = True
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"auto_renewal: DB temporarily unavailable: {type(e).__name__}: {str(e)[:100]}")
            iteration_outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            logger.error(f"auto_renewal: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("auto_renewal: Full traceback for task loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "auto_renewal", e, iteration=iteration_number)
            except Exception:
                pass
        finally:
            # Always log ITERATION_END so production logs confirm the iteration completed (no indefinite hang)
            runtime_health.record("auto_renewal", iteration_outcome, iteration_error_type)
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="auto_renewal",
                outcome=iteration_outcome,
                items_processed=0,
                error_type=iteration_error_type,
                duration_ms=duration_ms,
            )
            if iteration_outcome not in ("success", "cancelled", "skipped"):
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)

        if should_exit_loop:
            break
        
        # Sleep after iteration completes (outside try/finally)
        # Ждем до следующей проверки (5-15 минут, по умолчанию 10 минут)
        await asyncio.sleep(AUTO_RENEWAL_INTERVAL_SECONDS)

