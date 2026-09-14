"""Sales funnel (owner 2026-09-14, docs/audit/SCOPE.md «Воронка продаж»).

Three chains, counted from an event after the deploy (database.funnel):

  start — /start without trial and subscription: +1 h trial; +1 d trial (what
          the VPN gives); +3 d trial or −20 % 48 h; +7 d −25 % 72 h; +30 d −30 % 7 d.
  trial — the trial ended without a purchase (the −30 % 7 d message at the end
          is sent by the existing trial workers): +1 d «still valid»; +6 d
          «burns soon»; +14 d −25 % 72 h; +30 d −30 % 7 d; +90 d −40 % 7 d.
  paid  — a paid subscription ended, not renewed (the −15 % 72 h message at the
          end is sent by the expiry worker): +6 h reminder (same −15 %); +1 d
          «still valid»; +3 d −20 % 72 h; +7 d −25 % 72 h; +30 d −30 % 7 d;
          +90 d −40 % 7 d.

Global rules: a purchase / trial activation stops the chain (rechecked in the
claim right before the send); ≤ 1 funnel message per user per day (MSK);
nothing within 6 h after another lifecycle notification; sends only
10:00–21:00 MSK (due steps wait); unreachable users are skipped; ≤ 25 msg/s.

Discounts: the existing personal discount (user_discounts), granted when the
step is sent with an exact deadline, keep_max — a bigger active discount is
never lowered and an equal one is not extended. The text shows what checkout
will apply (the largest of the −15 % special offer and the personal discount,
as calculate_final_price picks it) and its deadline in MSK. The button only
opens the purchase screen: pressing it again changes nothing.

Texts: i18n funnel.* (RU + EN), editable in the dashboard (automated
notifications, category «Напоминания»); a step switched off there is skipped.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.structured_logger import log_event

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
SEND_FROM_HOUR = 10          # MSK, inclusive
SEND_UNTIL_HOUR = 21         # MSK, exclusive
OTHER_NOTIFICATION_GAP = timedelta(hours=6)
BATCH_PER_CHAIN = 200
# An anchor older than the last step + this is no longer looked at.
LOOKBACK_GRACE = timedelta(days=7)
# A «still valid» reminder is not sent when the discount ends sooner than this.
MIN_REMAINING = timedelta(hours=2)
SYSTEM_CREATOR = 0           # user_discounts.created_by for system grants (as the trial −30 %)


@dataclass(frozen=True)
class Step:
    chain: str
    name: str
    offset: timedelta
    # a NEW discount granted when the step is sent: percent for `grant_for`
    grant_percent: int = 0
    grant_for: Optional[timedelta] = None
    # «the discount you already have»: make sure ≥ keep_percent is active until
    # anchor + keep_until (granted then only if missing); the deadline never moves
    keep_percent: int = 0
    keep_until: Optional[timedelta] = None
    buttons: Tuple[str, ...] = ("buy",)

    @property
    def key(self) -> str:
        return f"funnel.{self.chain}_{self.name}"

    @property
    def has_discount(self) -> bool:
        return bool(self.grant_percent or self.keep_percent)


H, D = timedelta(hours=1), timedelta(days=1)

CHAINS: Dict[str, Tuple[Step, ...]] = {
    "start": (
        Step("start", "1h", 1 * H, buttons=("trial",)),
        Step("start", "1d", 1 * D, buttons=("trial",)),
        Step("start", "3d", 3 * D, grant_percent=20, grant_for=48 * H, buttons=("trial", "buy")),
        Step("start", "7d", 7 * D, grant_percent=25, grant_for=72 * H, buttons=("buy", "trial")),
        Step("start", "30d", 30 * D, grant_percent=30, grant_for=7 * D, buttons=("buy", "trial")),
    ),
    "trial": (
        Step("trial", "1d", 1 * D, keep_percent=30, keep_until=7 * D),
        Step("trial", "6d", 6 * D, keep_percent=30, keep_until=7 * D),
        Step("trial", "14d", 14 * D, grant_percent=25, grant_for=72 * H),
        Step("trial", "30d", 30 * D, grant_percent=30, grant_for=7 * D),
        Step("trial", "90d", 90 * D, grant_percent=40, grant_for=7 * D),
    ),
    "paid": (
        Step("paid", "6h", 6 * H, keep_percent=15, keep_until=72 * H),
        Step("paid", "1d", 1 * D, keep_percent=15, keep_until=72 * H),
        Step("paid", "3d", 3 * D, grant_percent=20, grant_for=72 * H),
        Step("paid", "7d", 7 * D, grant_percent=25, grant_for=72 * H),
        Step("paid", "30d", 30 * D, grant_percent=30, grant_for=7 * D),
        Step("paid", "90d", 90 * D, grant_percent=40, grant_for=7 * D),
    ),
}

ALL_STEPS: Tuple[Step, ...] = tuple(s for steps in CHAINS.values() for s in steps)
STEP_BY_KEY: Dict[str, Step] = {s.key: s for s in ALL_STEPS}


def get_step(chain: str, name: str) -> Step:
    return STEP_BY_KEY[f"funnel.{chain}_{name}"]


# ── time rules ───────────────────────────────────────────────────────────


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def in_send_window(now: datetime) -> bool:
    """10:00 ≤ Moscow time < 21:00."""
    return SEND_FROM_HOUR <= _utc(now).astimezone(MSK).hour < SEND_UNTIL_HOUR


def msk_day_start(now: datetime) -> datetime:
    """Start of the current Moscow calendar day, as aware UTC (the daily cap)."""
    local = _utc(now).astimezone(MSK)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def due_step(chain: str, anchor: datetime, now: datetime) -> Optional[Step]:
    """The latest step whose time has come (the SQL in database.funnel does the
    same); earlier unsent steps are skipped, never sent late in a row."""
    due = None
    for step in CHAINS[chain]:
        if _utc(anchor) + step.offset <= _utc(now):
            due = step
    return due


def earlier_steps(step: Step) -> Tuple[str, ...]:
    names = []
    for s in CHAINS[step.chain]:
        if s.name == step.name:
            break
        names.append(s.name)
    return tuple(names)


def lookback_start(chain: str, cutoff: datetime, now: datetime) -> datetime:
    """Oldest anchor still looked at: the cut-off, or last step + grace ago."""
    last = CHAINS[chain][-1].offset
    return max(_utc(cutoff), _utc(now) - last - LOOKBACK_GRACE)


# ── discounts ────────────────────────────────────────────────────────────


async def effective_discount(telegram_id: int) -> Tuple[int, Optional[datetime]]:
    """(percent, deadline) that checkout applies right now without a promo
    code — the largest of the −15 % special offer and the personal discount,
    picked exactly as calculate_final_price does (a tie keeps the special
    offer). (0, None) when there is none."""
    import database
    from database.subscriptions import pick_largest_discount
    special = await database.get_special_offer_info(telegram_id)
    personal = await database.get_user_discount(telegram_id)
    kind, percent = pick_largest_discount([
        ("special_offer", special["discount_percent"] if special else 0),
        ("personal", personal["discount_percent"] if personal else 0),
    ])
    if kind == "special_offer":
        return percent, _utc(special["expires_at"])
    if kind == "personal":
        exp = personal.get("expires_at")
        return percent, (_utc(exp) if exp else None)
    return 0, None


async def apply_discount(telegram_id: int, step: Step, anchor: datetime,
                         now: datetime) -> Optional[Tuple[int, Optional[datetime]]]:
    """Grant the step's discount (keep_max) and return what the user will get
    at checkout. None → nothing true to promise: skip the step.

    A PERMANENT personal discount (no expiry, set by an admin) is never
    overwritten: the keep_max upsert would replace a smaller one with a
    time-limited one and leave the user at 0 % after it ends. Bigger or equal
    → it stays and nothing is granted; smaller → the step is skipped."""
    import database
    personal = await database.get_user_discount(telegram_id)
    permanent = personal if personal and personal.get("expires_at") is None else None
    if step.keep_percent:
        until = _utc(anchor) + step.keep_until
        if _utc(now) >= until - MIN_REMAINING:
            return None
        percent, _deadline = await effective_discount(telegram_id)
        if percent < step.keep_percent:
            if step.chain == "paid":
                # «The same −15 %» = the period's ONE 72 h window (SCOPE «Срок
                # скидки −15 %»): shown, or opened if none was offered in this
                # period yet; ran out → skip. Never a second −15 % window.
                from database.subscriptions import claim_special_offer
                if await claim_special_offer(telegram_id, _utc(anchor)) is None:
                    return None
            elif permanent is not None:
                return None
            else:
                await database.create_user_discount(
                    telegram_id=telegram_id, discount_percent=step.keep_percent, expires_at=until,
                    created_by=SYSTEM_CREATOR, keep_max=True,
                )
    elif step.grant_percent:
        if permanent is None:
            await database.create_user_discount(
                telegram_id=telegram_id, discount_percent=step.grant_percent,
                expires_at=_utc(now) + step.grant_for, created_by=SYSTEM_CREATOR, keep_max=True,
            )
        elif permanent["discount_percent"] < step.grant_percent:
            return None
    percent, deadline = await effective_discount(telegram_id)
    if percent <= 0:
        return None
    return percent, deadline


# ── message ──────────────────────────────────────────────────────────────


def format_deadline(language: str, deadline: Optional[datetime]) -> str:
    from app.i18n import get_text
    from app.services.notifications.special_offer import format_deadline as _fmt
    if deadline is None:
        return get_text(language, "funnel.no_deadline")
    return _fmt(language, deadline)


def days_left_text(language: str, deadline: Optional[datetime], now: datetime) -> str:
    """«6 дней» / «2 дня» / «1 день» (whole days left, at least 1)."""
    from app.i18n import get_text
    if deadline is None:
        return get_text(language, "funnel.no_deadline")
    n = max(1, int((_utc(deadline) - _utc(now)).total_seconds() // 86400))
    if language != "en":         # Russian plural rules (other languages fall back to RU)
        if n % 10 == 1 and n % 100 != 11:
            form = "funnel.days_one"
        elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            form = "funnel.days_few"
        else:
            form = "funnel.days_many"
    else:
        form = "funnel.days_one" if n == 1 else "funnel.days_many"
    return get_text(language, form, n=n)


def keyboard(language: str, step: Step) -> InlineKeyboardMarkup:
    from app.i18n import get_text
    rows = []
    for kind in step.buttons:
        if kind == "trial":
            rows.append([InlineKeyboardButton(
                text=get_text(language, "funnel.btn_trial"), callback_data="activate_trial",
                style="success" if step.buttons[0] == "trial" else "primary",
            )])
        elif kind == "buy":
            label = "funnel.btn_renew_discount" if step.chain == "paid" else "funnel.btn_buy_discount"
            rows.append([InlineKeyboardButton(
                text=get_text(language, label), callback_data=f"funnel_buy:{step.chain}",
                style="success" if step.buttons[0] == "buy" else "primary",
            )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render(language: str, step: Step, discount: Optional[Tuple[int, Optional[datetime]]],
                 now: datetime) -> str:
    """The dashboard text for RU (custom or default), i18n for other languages."""
    from app.i18n import get_text
    from app.services.automated_notifications import get_notification_text
    params = {}
    if discount is not None:
        percent, deadline = discount
        params = {
            "percent": percent,
            "deadline": format_deadline(language, deadline),
            "days_left": days_left_text(language, deadline, now),
        }
    text = await get_notification_text(step.key, params=params, language=language)
    return text if text else get_text(language, step.key, **params)


# ── one candidate / one pass ─────────────────────────────────────────────


async def process_candidate(bot, chain: str, telegram_id: int, anchor: datetime, step_name: str, *,
                            now: datetime, lower: datetime, pacer=None) -> str:
    """Claim → (discount) → send → record. Returns the outcome."""
    from database import funnel as funnel_db
    from app.services.automated_notifications import is_notification_enabled, log_notification_send
    from app.services.language_service import resolve_user_language
    from app.utils.telegram_safe import safe_send_message

    step = get_step(chain, step_name)
    claim_id, reason = await funnel_db.claim(
        telegram_id, chain, anchor, step.name, earlier_steps(step),
        now=now, lower=lower, day_start=msk_day_start(now), other_since=_utc(now) - OTHER_NOTIFICATION_GAP,
    )
    if claim_id is None:
        return reason
    # Claimed: the DB connection is released; no connection is held below.
    try:
        if not await is_notification_enabled(step.key):
            await funnel_db.finish(claim_id, "skipped")
            await log_notification_send(step.key, telegram_id, status="skipped_disabled")
            return "disabled"
        discount = None
        if step.has_discount:
            discount = await apply_discount(telegram_id, step, anchor, now)
            if discount is None:
                await funnel_db.finish(claim_id, "skipped")
                return "no_discount"
        language = await resolve_user_language(telegram_id)
        text = await render(language, step, discount, now)
        markup = keyboard(language, step)
        if pacer is not None:
            await pacer.wait()
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=markup, parse_mode="HTML")
        status = "sent" if sent is not None else "failed"
        await funnel_db.finish(claim_id, status, *(discount or (None, None)))
        await log_notification_send(step.key, telegram_id, status=status)
        logger.info("SALES_FUNNEL_STEP user=%s chain=%s step=%s outcome=%s", telegram_id, chain, step.name, status)
        return status
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — one user never breaks the pass
        logger.warning("SALES_FUNNEL_STEP_FAILED user=%s chain=%s step=%s err=%s",
                       telegram_id, chain, step.name, type(e).__name__)
        try:
            await funnel_db.finish(claim_id, "failed")
        except Exception:  # noqa: BLE001
            pass
        return "error"


def _new_pacer():
    from app.services.broadcast_sender import BROADCAST_MAX_PER_SEC, _Pacer
    return _Pacer(BROADCAST_MAX_PER_SEC)


async def run_pass(bot, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """One worker pass: every chain, a bounded batch each. Returns outcome counts."""
    from database import funnel as funnel_db

    started = time.monotonic()
    now = _utc(now or datetime.now(timezone.utc))
    counts: Dict[str, int] = {}
    # The cut-off is fixed by the FIRST pass, whatever the hour.
    cutoff = await funnel_db.get_or_init_started_at(now)
    if not in_send_window(now):
        return {"outside_window": 1}
    pacer = _new_pacer()
    for chain, steps in CHAINS.items():
        lower = lookback_start(chain, cutoff, now)
        due = await funnel_db.fetch_due(
            chain, now=now, lower=lower,
            steps=[(s.name, s.offset.total_seconds()) for s in steps],
            day_start=msk_day_start(now), limit=BATCH_PER_CHAIN,
        )
        for cand in due:
            outcome = await process_candidate(
                bot, chain, cand["telegram_id"], cand["anchor_at"], cand["step"],
                now=now, lower=lower, pacer=pacer,
            )
            counts[outcome] = counts.get(outcome, 0) + 1
    log_event(
        logger, component="worker", operation="sales_funnel_pass", outcome="success",
        duration_ms=int((time.monotonic() - started) * 1000),
        reason=",".join(f"{k}={v}" for k, v in sorted(counts.items())) or "idle",
    )
    return counts


__all__ = [
    "ALL_STEPS", "CHAINS", "MSK", "STEP_BY_KEY", "Step", "apply_discount", "days_left_text", "due_step",
    "earlier_steps", "effective_discount", "format_deadline", "get_step", "in_send_window", "keyboard",
    "lookback_start", "msk_day_start", "process_candidate", "render", "run_pass",
]
