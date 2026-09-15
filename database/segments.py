"""Broadcast audience segments: the catalog, the key grammar and the SQL of the
segments added in 2026-09 (parametric ones + DB-only fixed ones).

Keys
----
* fixed:       ``paid_lapsed_any``, ``bypass_only_now`` … (``FIXED_SEGMENTS``)
* parametric:  ``<base>:<window>`` — ``paid_ended:30d``, ``trial_ended:6m``,
  ``any_ended:any``. Window = ``Nd`` (1–3650 days), ``Nm`` (1–120 calendar
  months) or ``any`` (no lower bound). Past bases select an event in
  (now − window, now]; ``paid_expiring*`` select an end in (now, now + window];
  ``inactive`` selects «not seen for ≥ window».

One parser (``parse_segment_key``) is used by every consumer: the resolver
(``database.admin._segment_user_ids``), the broadcast create / schedule /
count endpoints and the automated-notification segment filter. The window
becomes validated ints → a naive-UTC bound passed as a query parameter; no
user input is ever formatted into SQL.

The older fixed keys keep their SQL in ``database/admin.py``; this module only
owns the SQL of ``NEW_FIXED_SQL`` and ``PARAMETRIC``.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from database.core import _to_db_utc

MAX_DAYS = 3650
MAX_MONTHS = 120

_WINDOW_RE = re.compile(r"^(?:([1-9][0-9]{0,3})([dm])|any)$")
# Lower bound of an `any` window: older than every row in the DB.
_BEGINNING = datetime(1900, 1, 1, tzinfo=timezone.utc)


class SegmentKeyError(ValueError):
    """Unknown segment or a bad window — the API answers 400."""


@dataclass(frozen=True)
class Window:
    n: Optional[int]          # None = any
    unit: Optional[str]       # "d" | "m" | None

    @property
    def is_any(self) -> bool:
        return self.n is None

    def key(self) -> str:
        return "any" if self.n is None else f"{self.n}{self.unit}"

    def shift(self, now: datetime, sign: int) -> datetime:
        """now ∓ window. Months are calendar months with the day clamped to the
        month's end — the same as Postgres ``ts - interval 'N months'``."""
        if self.n is None:
            return _BEGINNING
        if self.unit == "d":
            return now + sign * timedelta(days=self.n)
        total = now.year * 12 + (now.month - 1) + sign * self.n
        year, month = divmod(total, 12)
        month += 1
        day = min(now.day, calendar.monthrange(year, month)[1])
        return now.replace(year=year, month=month, day=day)


def parse_window(raw: str) -> Window:
    m = _WINDOW_RE.match(raw or "")
    if not m:
        raise SegmentKeyError(f"bad window {raw!r}: use Nd (1–{MAX_DAYS}), Nm (1–{MAX_MONTHS}) or any")
    if raw == "any":
        return Window(None, None)
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d" and n > MAX_DAYS:
        raise SegmentKeyError(f"window too long: {n}d > {MAX_DAYS}d")
    if unit == "m" and n > MAX_MONTHS:
        raise SegmentKeyError(f"window too long: {n}m > {MAX_MONTHS}m")
    return Window(n, unit)


# ── Russian labels ────────────────────────────────────────────────────


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _unit_word(w: Window) -> str:
    if w.unit == "d":
        return _plural(w.n, "день", "дня", "дней")
    return _plural(w.n, "месяц", "месяца", "месяцев")


def window_phrase(direction: str, w: Window) -> str:
    """«за последние 6 месяцев», «за всё время», «в ближайшие 7 дней», «30 дней и дольше»."""
    if direction == "future":
        return "в ближайший " + _unit_word(w) if w.n == 1 else f"в ближайшие {w.n} {_unit_word(w)}"
    if direction == "idle":
        return f"{w.n} {_unit_word(w)} и дольше"
    if w.is_any:
        return "за всё время"
    return "за последний " + _unit_word(w) if w.n == 1 else f"за последние {w.n} {_unit_word(w)}"


# ── Parametric bases ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ParamSpec:
    base: str
    title: str
    description: str
    group: str
    default: str               # default window key
    direction: str = "past"    # past | future | idle
    allow_any: bool = True


PARAMETRIC: dict[str, ParamSpec] = {s.base: s for s in (
    ParamSpec(
        "paid_ended", "Платная истекла, не продлил",
        "Хотя бы раз платил за подписку (покупка / продление / автопродление), последний период "
        "premium закончился в выбранном окне, после этого не продлевал. Сейчас нет активной "
        "подписки (ГБ обхода не в счёт — bypass-only тоже попадает).",
        "Платная", "30d"),
    ParamSpec(
        "paid_expiring", "Платная заканчивается",
        "Активная платная подписка (оплата или автопродление) заканчивается в выбранное число "
        "дней / месяцев вперёд. Включены и те, у кого включено автопродление.",
        "Платная", "7d", direction="future", allow_any=False),
    ParamSpec(
        "paid_expiring_manual", "Платная заканчивается, автопродление выключено",
        "То же, что «Платная заканчивается», но только те, у кого автопродление ВЫКЛЮЧЕНО — "
        "с включённым подписка продлится сама, скидка им только срежет выручку.",
        "Платная", "7d", direction="future", allow_any=False),
    ParamSpec(
        "trial_ended", "Пробный закончился, не купил",
        "Пробный период закончился в выбранном окне, подписку не покупал ни разу, сейчас нет "
        "активной подписки (ГБ обхода не в счёт).",
        "Триал", "30d"),
    ParamSpec(
        "any_ended", "Любая подписка истекла",
        "Последний доступ любого вида (пробный / платная / подарок / дни от админа) закончился в "
        "выбранном окне, сейчас активной подписки нет (bypass-only тоже попадает).",
        "Истёкшие (любые)", "12m"),
    ParamSpec(
        "cold_start", "Нажал /start и ничего",
        "Пришёл в бот (первый /start) в выбранном окне и с тех пор ничего: пробный не брал, "
        "не платил, подписки / ключа не было ни разу.",
        "Cold-start", "7d"),
    ParamSpec(
        "bought_sub", "Купил подписку",
        "Успешная оплата подписки (Basic / Plus / Combo, любой способ, включая баланс и "
        "автопродление) в выбранном окне. Пакеты ГБ, пополнения и подарки другим не в счёт.",
        "Недавно купили", "30d"),
    ParamSpec(
        "premium_ended_bypass", "Premium закончился, остался обход",
        "Premium (пробный, платный, подарок, дни от админа) закончился в выбранном окне, и сейчас "
        "у юзера bypass-only строка — купленные ГБ обхода на месте. По данным БД, без запроса в панель.",
        "Обход", "30d"),
    ParamSpec(
        "grant_ended", "Подарок / дни от админа закончились, не платил",
        "Последний период был подарком, днями от админа, промо-ссылкой или призом — он закончился "
        "в выбранном окне. За подписку не платил ни разу, сейчас активной подписки нет.",
        "Подарки и дни от админа", "30d"),
    ParamSpec(
        "inactive", "Не заходил в бот",
        "Последнее действие в боте (users.last_seen_at) было раньше выбранного срока. Кто не "
        "заходил с момента, как это начали записывать, считается по дате первого /start.",
        "Активность", "30d", direction="idle", allow_any=False),
)}


def parse_segment_key(key: str) -> tuple[str, Optional[Window]]:
    """(base, window) for a parametric key, (key, None) for a known fixed key.
    Raises SegmentKeyError for anything else."""
    if not isinstance(key, str) or not key:
        raise SegmentKeyError("empty segment")
    if ":" not in key:
        if key in FIXED_KEYS:
            return key, None
        raise SegmentKeyError(f"unknown segment {key!r}")
    base, _, raw = key.partition(":")
    spec = PARAMETRIC.get(base)
    if spec is None:
        raise SegmentKeyError(f"unknown segment {base!r}")
    w = parse_window(raw)
    if w.is_any and not spec.allow_any:
        raise SegmentKeyError(f"{base} needs a finite window")
    return base, w


def validate_segment_key(key: str) -> str:
    parse_segment_key(key)
    return key


def is_parametric_key(key: str) -> bool:
    return isinstance(key, str) and ":" in key


def segment_label(key: Optional[str]) -> str:
    """Human label of a stored key («Платная истекла, не продлил за последние 6 месяцев»).
    Unknown / broken keys come back as they are."""
    if not key:
        return ""
    try:
        base, w = parse_segment_key(key)
    except SegmentKeyError:
        return key
    if w is None:
        return FIXED_LABELS.get(base, base)
    spec = PARAMETRIC[base]
    return f"{spec.title} {window_phrase(spec.direction, w)}"


# ── SQL ───────────────────────────────────────────────────────────────
#
# One row per user in `subscriptions` (UNIQUE telegram_id). Timestamps are
# naive UTC; bounds come in as parameters ($1 / $2). Fragments below are
# constants — nothing user-supplied is formatted in.

_TRIAL_END = "COALESCE(u.trial_expires_at, u.trial_used_at + INTERVAL '3 days')"
_PAID_ACTIONS = "('purchase', 'renewal', 'auto_renew')"
_SUB_PAYMENT = ("p.status IN ('paid', 'approved') "
                "AND p.tariff ~ '^(basic|plus|combo_basic|combo_plus|biz_[a-z]+)_[0-9]+$'")
_PAID_SOURCES = "('payment', 'auto_renew')"
_BYPASS_ROW = "(COALESCE(s.is_bypass_only, FALSE) OR s.source = 'bypass_only')"


def _no_active_premium(alias: str, now: str) -> str:
    """No subscription row giving premium right now (trial / gift / admin count;
    a bypass-only row with its +10 years placeholder does not)."""
    return f"""NOT EXISTS (
        SELECT 1 FROM subscriptions s
         WHERE s.telegram_id = {alias}.telegram_id
           AND s.status = 'active' AND s.expires_at > {now}
           AND NOT {_BYPASS_ROW})"""


def _never_paid_sub(alias: str) -> str:
    """Never paid for a subscription: no subscription payment, no paid period in history."""
    return f"""NOT EXISTS (
        SELECT 1 FROM payments p WHERE p.telegram_id = {alias}.telegram_id AND {_SUB_PAYMENT})
      AND NOT EXISTS (
        SELECT 1 FROM subscription_history ph
         WHERE ph.telegram_id = {alias}.telegram_id AND ph.action_type IN {_PAID_ACTIONS})"""


_NOW = "(NOW() AT TIME ZONE 'UTC')"

# Last premium period per user from history (trial and bypass-only rows aside),
# for users that had a period ending after $1. Shared by paid_ended / grant_ended.
_LAST_PERIOD_CTE = f"""
    WITH cand AS (
        SELECT DISTINCT telegram_id FROM subscription_history
         WHERE end_date > $1 AND action_type NOT IN ('trial', 'bypass_only')
    ), h AS (
        SELECT sh.telegram_id, MAX(sh.end_date) AS last_end,
               BOOL_OR(sh.action_type IN {_PAID_ACTIONS}) AS was_paid
          FROM subscription_history sh
          JOIN cand c ON c.telegram_id = sh.telegram_id
         WHERE sh.action_type NOT IN ('trial', 'bypass_only')
         GROUP BY sh.telegram_id
    )"""

# (sql, args) — "past": ($1 lower, $2 now); "future": ($1 now, $2 upper);
# "idle": ($1 threshold).
_PARAM_SQL: dict[str, str] = {
    "paid_ended": _LAST_PERIOD_CTE + f"""
    SELECT u.telegram_id FROM h JOIN users u ON u.telegram_id = h.telegram_id
     WHERE h.was_paid AND h.last_end > $1 AND h.last_end <= $2
       AND {_no_active_premium('u', '$2')}""",

    # Candidates start from free periods (never from payers' rows), then the
    # user's whole non-trial history must hold no paid period.
    "grant_ended": f"""
    WITH cand AS (
        SELECT DISTINCT telegram_id FROM subscription_history
         WHERE end_date > $1
           AND action_type NOT IN ('trial', 'bypass_only', 'purchase', 'renewal', 'auto_renew',
                                   'reissue', 'manual_reissue', 'admin_revoke')
    ), h AS (
        SELECT sh.telegram_id, MAX(sh.end_date) AS last_end
          FROM subscription_history sh
          JOIN cand c ON c.telegram_id = sh.telegram_id
         WHERE sh.action_type NOT IN ('trial', 'bypass_only')
         GROUP BY sh.telegram_id
        HAVING NOT BOOL_OR(sh.action_type IN {_PAID_ACTIONS})
    )
    SELECT u.telegram_id FROM h JOIN users u ON u.telegram_id = h.telegram_id
     WHERE h.last_end > $1 AND h.last_end <= $2
       AND {_no_active_premium('u', '$2')}
       AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.telegram_id = u.telegram_id AND {_SUB_PAYMENT})""",

    "trial_ended": f"""
    SELECT u.telegram_id FROM users u
     WHERE u.trial_used_at IS NOT NULL
       AND {_TRIAL_END} > $1 AND {_TRIAL_END} <= $2
       AND {_no_active_premium('u', '$2')}
       AND {_never_paid_sub('u')}""",

    # The last end of any access. A premium / trial row (one per user) holds it
    # in expires_at — and ended ⇔ no active premium; a bypass-only row keeps a
    # +10 years placeholder, so there it comes from history / the trial.
    "any_ended": f"""
    WITH bo AS (
        SELECT s.telegram_id FROM subscriptions s WHERE {_BYPASS_ROW}
    ), ends AS (
        SELECT s.telegram_id, s.expires_at AS last_end FROM subscriptions s
         WHERE s.expires_at > $1 AND s.expires_at <= $2 AND NOT {_BYPASS_ROW}
        UNION ALL
        SELECT x.telegram_id, MAX(x.ended) FROM (
            SELECT sh.telegram_id, sh.end_date AS ended
              FROM subscription_history sh JOIN bo ON bo.telegram_id = sh.telegram_id
             WHERE sh.action_type <> 'bypass_only'
            UNION ALL
            SELECT u.telegram_id, {_TRIAL_END}
              FROM users u JOIN bo ON bo.telegram_id = u.telegram_id
             WHERE u.trial_used_at IS NOT NULL
        ) x GROUP BY x.telegram_id
        HAVING MAX(x.ended) > $1 AND MAX(x.ended) <= $2
    )
    SELECT u.telegram_id FROM ends e JOIN users u ON u.telegram_id = e.telegram_id""",

    "cold_start": """
    SELECT u.telegram_id FROM users u
     WHERE u.created_at > $1 AND u.created_at <= $2
       AND u.trial_used_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.telegram_id = u.telegram_id)
       AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.telegram_id = u.telegram_id
                          AND p.status IN ('paid', 'approved'))""",

    "bought_sub": f"""
    SELECT DISTINCT p.telegram_id FROM payments p
      JOIN users u ON u.telegram_id = p.telegram_id
     WHERE {_SUB_PAYMENT} AND p.created_at > $1 AND p.created_at <= $2""",

    "premium_ended_bypass": f"""
    WITH bo AS (
        SELECT s.telegram_id FROM subscriptions s
         WHERE s.status = 'active' AND {_BYPASS_ROW}
    ), e AS (
        SELECT sh.telegram_id, sh.end_date AS ended
          FROM subscription_history sh JOIN bo ON bo.telegram_id = sh.telegram_id
         WHERE sh.action_type <> 'bypass_only'
        UNION ALL
        SELECT u.telegram_id, {_TRIAL_END}
          FROM users u JOIN bo ON bo.telegram_id = u.telegram_id
         WHERE u.trial_used_at IS NOT NULL
    ), last AS (
        SELECT telegram_id, MAX(ended) AS last_end FROM e GROUP BY telegram_id
    )
    SELECT u.telegram_id FROM last l JOIN users u ON u.telegram_id = l.telegram_id
     WHERE l.last_end > $1 AND l.last_end <= $2""",

    "inactive": """
    SELECT u.telegram_id FROM users u
     WHERE u.last_seen_at <= $1
        OR (u.last_seen_at IS NULL AND u.created_at <= $1)""",

    "paid_expiring": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND s.expires_at > $1 AND s.expires_at <= $2
       AND COALESCE(s.source, '') IN {_PAID_SOURCES}
       AND NOT {_BYPASS_ROW}""",

    "paid_expiring_manual": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND s.expires_at > $1 AND s.expires_at <= $2
       AND COALESCE(s.source, '') IN {_PAID_SOURCES}
       AND NOT {_BYPASS_ROW}
       AND NOT COALESCE(s.auto_renew, FALSE)""",
}

_ACTIVE_PAID = f"""s.status = 'active' AND s.expires_at > {_NOW}
       AND COALESCE(s.source, '') IN {_PAID_SOURCES} AND NOT {_BYPASS_ROW}"""

NEW_FIXED_SQL: dict[str, str] = {
    "bypass_only_now": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND {_BYPASS_ROW}""",

    "trial_ended_bypass": f"""
    SELECT u.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND {_BYPASS_ROW}
       AND u.trial_used_at IS NOT NULL AND {_TRIAL_END} <= {_NOW}
       AND {_never_paid_sub('u')}""",

    "gb_only_buyers": f"""
    SELECT u.telegram_id FROM (
        SELECT p.telegram_id FROM payments p
         WHERE p.status IN ('paid', 'approved')
         GROUP BY p.telegram_id
        HAVING BOOL_OR(p.tariff LIKE 'traffic\\_%' OR p.tariff LIKE 'bypass\\_%')
           AND NOT BOOL_OR({_SUB_PAYMENT})
    ) g JOIN users u ON u.telegram_id = g.telegram_id
     WHERE NOT EXISTS (SELECT 1 FROM subscription_history ph
                        WHERE ph.telegram_id = u.telegram_id AND ph.action_type IN {_PAID_ACTIONS})""",

    "gift_active": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND s.expires_at > {_NOW}
       AND s.source = 'gift' AND NOT {_BYPASS_ROW}""",

    "granted_active": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE s.status = 'active' AND s.expires_at > {_NOW}
       AND COALESCE(s.source, '') NOT IN ('payment', 'auto_renew', 'trial', 'gift', 'bypass_only')
       AND NOT {_BYPASS_ROW}""",

    "paid_once": f"""
    SELECT u.telegram_id FROM (
        SELECT p.telegram_id FROM payments p WHERE {_SUB_PAYMENT}
         GROUP BY p.telegram_id HAVING COUNT(*) = 1
    ) x JOIN users u ON u.telegram_id = x.telegram_id""",

    "paid_loyal": f"""
    SELECT u.telegram_id FROM (
        SELECT p.telegram_id FROM payments p WHERE {_SUB_PAYMENT}
         GROUP BY p.telegram_id HAVING COUNT(*) >= 2
    ) x JOIN users u ON u.telegram_id = x.telegram_id""",

    "autorenew_on": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE {_ACTIVE_PAID} AND COALESCE(s.auto_renew, FALSE)""",

    "autorenew_off": f"""
    SELECT s.telegram_id FROM subscriptions s
      JOIN users u ON u.telegram_id = s.telegram_id
     WHERE {_ACTIVE_PAID} AND NOT COALESCE(s.auto_renew, FALSE)""",

    "referrers_paid": """
    SELECT u.telegram_id FROM users u
     WHERE u.telegram_id IN (
        SELECT r.referrer_user_id FROM referrals r
         WHERE EXISTS (SELECT 1 FROM payments p
                        WHERE p.telegram_id = r.referred_user_id
                          AND p.status IN ('paid', 'approved') AND p.tariff <> 'balance_topup'))""",

    "referred_never_paid": f"""
    SELECT u.telegram_id FROM referrals r
      JOIN users u ON u.telegram_id = r.referred_user_id
     WHERE {_never_paid_sub('u')}""",

    "lang_en": "SELECT u.telegram_id FROM users u WHERE u.language = 'en'",
}


def _query(key: str, now: Optional[datetime] = None) -> Optional[tuple[str, tuple]]:
    """(sql, args) of a segment owned by this module; None → a legacy key
    (resolved by database.admin). Raises SegmentKeyError for a bad parametric key."""
    if key in NEW_FIXED_SQL:
        return NEW_FIXED_SQL[key], ()
    if ":" not in key:
        return None
    base, w = parse_segment_key(key)
    spec = PARAMETRIC[base]
    now = now or datetime.now(timezone.utc)
    if spec.direction == "future":
        args = (_to_db_utc(now), _to_db_utc(w.shift(now, +1)))
    elif spec.direction == "idle":
        args = (_to_db_utc(w.shift(now, -1)),)
    else:
        args = (_to_db_utc(w.shift(now, -1)), _to_db_utc(now))
    return _PARAM_SQL[base], args


async def fetch_ids(conn, key: str, now: Optional[datetime] = None) -> Optional[list]:
    """Members of a segment owned by this module (unreachable ones included —
    get_users_by_segment drops them); None → not ours."""
    q = _query(key, now)
    if q is None:
        return None
    rows = await conn.fetch(q[0], *q[1])
    return [r["telegram_id"] for r in rows]


async def count(conn, key: str, now: Optional[datetime] = None) -> Optional[int]:
    """len(get_users_by_segment(key)) in SQL: every query above yields distinct
    telegram_ids; the same rule drops is_reachable = FALSE. None → not ours."""
    q = _query(key, now)
    if q is None:
        return None
    sql = ("SELECT COUNT(*) FROM (" + q[0] + ") m JOIN users ru ON ru.telegram_id = m.telegram_id "
           "WHERE ru.is_reachable IS DISTINCT FROM FALSE")
    return int(await conn.fetchval(sql, *q[1]))


# ── Catalog (GET /broadcasts/segments) ────────────────────────────────
# (key, label, description, group). Parametric bases are listed first inside
# their group (dashboard/src/pages/BroadcastCreate.tsx keeps the order).

FIXED_SEGMENTS: tuple[tuple[str, str, str, str], ...] = (
    # ── Базовые ──────────────────────────────────────────────────
    ("all_users", "Все юзеры",
     "Все записи в таблице users — включая тех, кто нажал /start и ушёл.",
     "Базовые"),
    ("active_subscriptions", "Активные подписки",
     "У пользователя есть подписка с expires_at > NOW (любого типа: триал, платная, gift, admin_grant).",
     "Базовые"),
    ("no_subscription", "Без активной подписки",
     "Нет строки в subscriptions с expires_at > NOW. Включает и тех, кто никогда не подписывался, и тех, у кого истекла.",
     "Базовые"),
    ("no_remnawave", "Без Remnawave",
     "Никогда не было entity в панели Remnawave — ни premium, ни bypass. То есть не завёл ни одного ключа.",
     "Базовые"),
    ("lang_en", "Английский язык",
     "Язык бота у юзера — английский (users.language = 'en'). Для рассылок на английском.",
     "Базовые"),

    # ── Воронка: старая база (одна рассылка, SCOPE «Воронка продаж») ─
    # Только события ДО запуска воронки (app_settings.sales_funnel_started_at):
    # всё, что позже, воронка ведёт сама — сюда не попадает.
    ("funnel_start_no_trial", "Нажали /start, без пробного и подписки",
     "Старая база: нажал /start до запуска воронки, пробный не включал, подписки и оплат не было ни разу. Для разовой рассылки со скидкой (кнопка «Купить со скидкой»).",
     "Воронка — старая база"),
    ("funnel_trial_ended_no_purchase", "Пробный закончился без покупки",
     "Старая база: пробный закончился до запуска воронки, после начала пробного не было ни одной оплаты, сейчас нет активной подписки (ГБ обхода не в счёт).",
     "Воронка — старая база"),
    ("funnel_paid_ended_no_renewal", "Платная закончилась без продления",
     "Старая база: хотя бы раз платил (покупка / продление / автопродление), последний период закончился до запуска воронки, сейчас нет активной подписки (ГБ обхода не в счёт) и после окончания не платил.",
     "Воронка — старая база"),

    # ── Cold-start (новые молчуны) ───────────────────────────────
    ("started_1d_cold", "Cold — старт за 24ч, ничего",
     "Нажал /start за последние 24 часа И до сих пор не активировал триал, не купил, не завёл ключ. Свежий молчун, самое время догреть.",
     "Cold-start"),
    ("started_3d_cold", "Cold — старт за 3 дня, ничего",
     "Нажал /start за последние 3 дня И до сих пор ничего. Ещё помнит про бот.",
     "Cold-start"),
    ("started_7d_cold", "Cold — старт за 7 дней, ничего",
     "Нажал /start за последние 7 дней И до сих пор ничего.",
     "Cold-start"),
    ("started_14d_cold", "Cold — старт за 14 дней, ничего",
     "Нажал /start за последние 14 дней И до сих пор ничего. Уже подзабыл, нужен сильный оффер.",
     "Cold-start"),
    ("started_30d_cold", "Cold — старт за 30 дней, ничего",
     "Нажал /start за последние 30 дней И до сих пор ничего. Крайний край — «уходящий».",
     "Cold-start"),

    # ── Триальная воронка (кто активировал триал) ────────────────
    ("trial_active_any", "Триал — сейчас идёт (любой)",
     "У всех, у кого сейчас активен пробный период (не истёк, платной ещё нет). Годится для мидл-триал коммуникаций «второй день с нами», FAQ, кейсы.",
     "Триал"),
    ("trial_activated_today", "Триал — активирован за 24ч",
     "Юзеры, которые активировали пробный период за последние 24 часа. Свежая аудитория для welcome-серии и объяснения фич.",
     "Триал"),
    ("trial_active_day1", "Триал — 1-й день (0–24ч)",
     "Активировали триал в последние 24ч, триал ещё идёт. Welcome / первый месседж.",
     "Триал"),
    ("trial_active_day2", "Триал — 2-й день (24–48ч)",
     "Второй день триала, триал ещё идёт. «Уже 2 дня с нами, что успели попробовать?»",
     "Триал"),
    ("trial_active_day3", "Триал — 3-й день (48–72ч)",
     "Последний день триала (обычно ~72ч). «Завтра истечёт — оформи сейчас».",
     "Триал"),
    ("trial_ends_in_1d", "Триал — заканчивается через 24ч",
     "Триал ещё идёт, но истечёт в ближайшие 24 часа. Ключевой момент конверсии — «оформи, чтобы не потерять».",
     "Триал"),
    ("trial_expired_6h", "Триал — истёк 6ч назад",
     "Триал закончился ~6 часов назад, платной подписки не оформлено. Свежий «упавший» триал.",
     "Триал"),
    ("trial_expired_1d", "Триал — истёк 1 день назад",
     "Триал закончился ~1 день назад, платной нет. Первое напоминание после разрыва.",
     "Триал"),
    ("trial_expired_2d", "Триал — истёк 2 дня назад",
     "Триал закончился ~2 дня назад, платной нет.",
     "Триал"),
    ("trial_expired_3d", "Триал — истёк 3 дня назад",
     "Триал закончился ~3 дня назад, платной нет.",
     "Триал"),
    ("trial_expired_7d", "Триал — истёк 7 дней назад (не купил)",
     "Триал закончился ~7 дней назад И НИКОГДА не покупал подписку. Холодная реактивация недельной давности.",
     "Триал"),
    ("trial_expired_14d", "Триал — истёк 14 дней назад (не купил)",
     "Триал закончился ~14 дней назад И никогда не покупал. Двухнедельная реактивация.",
     "Триал"),
    ("trial_expired_30d", "Триал — истёк 30 дней назад (не купил)",
     "Триал закончился ~30 дней назад И никогда не покупал. Месячная реактивация.",
     "Триал"),
    ("trial_expired_60d", "Триал — истёк 60 дней назад (не купил)",
     "Триал закончился ~60 дней назад И никогда не покупал. Двухмесячная реактивация.",
     "Триал"),
    ("trial_expired_90d", "Триал — истёк 3 мес назад (не купил)",
     "Триал закончился ~90 дней назад И никогда не покупал. «Последний шанс» — сильный оффер обязателен.",
     "Триал"),
    ("trial_expired_180d", "Триал — истёк полгода назад (не купил)",
     "Триал закончился ~180 дней назад И никогда не покупал. Крайняя точка реактивации.",
     "Триал"),
    ("trial_expired_365d", "Триал — истёк год назад (не купил)",
     "Триал закончился ~365 дней назад И никогда не покупал. Год без активности — либо забыл, либо ушёл к конкуренту.",
     "Триал"),
    ("trial_expired_within_6m", "Триал — истёк за последние 6 мес (не купил)",
     "Кумулятивное окно: триал закончился в любой момент за последние 180 дней И юзер никогда не покупал, сейчас без подписки. Массовая реактивация всех отвалившихся за полгода — один раскат по большой аудитории.",
     "Триал"),

    # ── Платные churn / реактивация ──────────────────────────────
    ("paid_expires_in_1d", "Платная — заканчивается за 1 день",
     "Платная подписка ещё активна, истечёт в ближайшие 24 часа. Финальное напоминание — «продли сейчас, чтобы не отключилось».",
     "Платная"),
    ("paid_expires_in_3d", "Платная — заканчивается за 3 дня",
     "Платная активна, истечёт за 72 часа. Мягкий пре-напоминающий пуш «пора продлить».",
     "Платная"),
    ("paid_expires_in_7d", "Платная — заканчивается за 7 дней",
     "Платная активна, истечёт за неделю. Хорошо ложится оффер «продли заранее — фиксируешь цену».",
     "Платная"),
    ("paid_expires_in_14d", "Платная — заканчивается за 14 дней",
     "Платная активна, истечёт за 2 недели. Ранний пуш для тех, кто планирует бюджет заранее.",
     "Платная"),
    ("expires_in_3d", "Любая — заканчивается за 3 дня (legacy)",
     "То же что paid_expires_in_3d — оставлено для совместимости с ранее созданными рассылками.",
     "Платная"),
    ("paid_expired_1d", "Платная — истекла 1 день назад",
     "Платная истекла ~1 день назад, сейчас платной нет. Свежий churn — первое напоминание.",
     "Платная"),
    ("paid_expired_7d", "Платная — истекла 7 дней назад",
     "Платная истекла ~7 дней назад, сейчас платной нет. Недельная реактивация.",
     "Платная"),
    ("paid_expired_14d", "Платная — истекла 14 дней назад",
     "Платная истекла ~14 дней назад, сейчас нет.",
     "Платная"),
    ("paid_expired_30d", "Платная — истекла за последние 30 дней",
     "По истории подписок последняя платная закончилась в окне 1–30 дней назад и сейчас неактивна.",
     "Платная"),
    ("paid_expired_60d", "Платная — истекла 60 дней назад",
     "Платная истекла ~60 дней назад. Двухмесячный churn.",
     "Платная"),
    ("paid_expired_90d", "Платная — истекла 3 мес назад",
     "Платная истекла ~90 дней назад. Крайний край реактивации.",
     "Платная"),
    ("paid_expired_180d", "Платная — истекла полгода назад",
     "Платная истекла ~180 дней назад. Полугодовой churn — «мы соскучились».",
     "Платная"),
    ("paid_expired_365d", "Платная — истекла год назад",
     "Платная истекла ~365 дней назад. Год без подписки — реактивация «с чистого листа».",
     "Платная"),
    ("paid_expired_730d", "Платная — истекла 2 года назад",
     "Платная истекла ~730 дней назад. Максимально дальний churn — редкая, но всё же аудитория.",
     "Платная"),
    ("paid_lapsed_any", "Платная — когда-либо платил, сейчас не активен",
     "Когда-либо оплачивал (purchase / renewal / auto_renew) и сейчас без активной подписки. Максимальная реактивационная аудитория — всех «ушедших».",
     "Платная"),

    # ── Недавно купившие — cross-sell / thanks / upsell ──────────
    ("paid_bought_within_7d", "Купил платную за 7 дней",
     "Оформил успешную оплату (payments.status='paid'|'approved') в течение последних 7 дней. Целевая для благодарности, upsell-оффера, feedback-опроса.",
     "Недавно купили"),
    ("paid_bought_within_14d", "Купил платную за 14 дней",
     "Оформил успешную оплату в течение последних 14 дней. Двухнедельное окно — свежая активная аудитория, есть с чем работать.",
     "Недавно купили"),
    ("paid_bought_within_30d", "Купил платную за 30 дней",
     "Оформил успешную оплату в течение последних 30 дней. Месячная когорта — большая, годится для широких кампаний по «активным».",
     "Недавно купили"),

    # ── Любая (комбинированные) ──────────────────────────────────
    ("expired_1d", "Истекла (любая) 1 день назад",
     "Любая подписка (триал ∪ платная) истекла ~1 день назад.",
     "Истёкшие (любые)"),
    ("expired_2d", "Истекла (любая) 2 дня назад",
     "Любая подписка истекла ~2 дня назад.",
     "Истёкшие (любые)"),
    ("expired_3d", "Истекла (любая) 3 дня назад",
     "Любая подписка истекла ~3 дня назад.",
     "Истёкшие (любые)"),
    ("expired_within_1y", "Истекла (любая) за последний год",
     "Любая подписка (триал ∪ платная ∪ gift ∪ admin) была и истекла в "
     "течение последних 365 дней. Сейчас активной подписки НЕТ. "
     "Максимальная годовая реактивационная аудитория «всех, кто был с нами "
     "за год и ушёл».",
     "Истёкшие (любые)"),

    # ── Обход (ГБ) — по данным БД, без запросов в панель ─────────
    ("bypass_only_now", "Только обход — premium закончился",
     "Сейчас у юзера bypass-only строка: premium нет, купленные / оставшиеся ГБ обхода на месте. Целевая для «вернуть основные серверы».",
     "Обход"),
    ("trial_ended_bypass", "Пробный закончился, остались ГБ обхода",
     "Пробный закончился, подписку не покупал ни разу, сейчас bypass-only строка — обход работает на ГБ. Обычно это покупатели ГБ с экрана «Только обход», получившие 3 дня premium в подарок.",
     "Обход"),
    ("gb_only_buyers", "Покупали только ГБ, подписку — никогда",
     "Есть успешная оплата пакета ГБ (traffic_ / bypass_), но ни одной оплаты подписки. Кандидаты на первую подписку.",
     "Обход"),

    # ── Подарки и дни от админа ──────────────────────────────────
    ("gift_active", "Подарок — сейчас активен",
     "Активная подписка получена подарком (subscriptions.source = 'gift'). Подарок заканчивается — пора предложить продление.",
     "Подарки и дни от админа"),
    ("granted_active", "Дни от админа / промо — сейчас активны",
     "Активная подписка выдана бесплатно: админом из дашборда, промо-ссылкой, призом игры, реферальным бонусом (не оплата, не пробный, не подарок).",
     "Подарки и дни от админа"),

    # ── Лояльность ───────────────────────────────────────────────
    ("paid_once", "Платил за подписку ровно 1 раз",
     "Ровно одна успешная оплата подписки за всё время (любой способ). Сейчас может быть активен или нет. Цель — вторая покупка.",
     "Лояльность"),
    ("paid_loyal", "Лояльные — платил 2+ раз",
     "Две и больше успешных оплат подписки (покупки, продления, автопродления). Для благодарности, раннего доступа, апселла на год.",
     "Лояльность"),
    ("autorenew_on", "Активная платная, автопродление включено",
     "Сейчас активна платная подписка и включено автопродление (спишется с баланса).",
     "Лояльность"),
    ("autorenew_off", "Активная платная, автопродление выключено",
     "Сейчас активна платная подписка, автопродление выключено. Цель — «включи автопродление» или пополнение баланса.",
     "Лояльность"),

    # ── Рефералы ─────────────────────────────────────────────────
    ("referrers_paid", "Пригласил друга, который оплатил",
     "Хотя бы один приглашённый по реферальной ссылке совершил успешную покупку (пополнения баланса не в счёт). Для благодарности и «пригласи ещё».",
     "Рефералы"),
    ("referred_never_paid", "Пришёл по приглашению, не платил",
     "Пришёл по реферальной ссылке друга и ни разу не оплатил подписку.",
     "Рефералы"),

    # ── Апселл / балансовый ──────────────────────────────────────
    ("basic_active", "Активные Basic",
     "Сейчас активна подписка Basic. Целевая для upsell на Plus / Combo.",
     "Апселл / особые"),
    ("plus_active", "Активные Plus",
     "Сейчас активна подписка Plus. Целевая для upsell на Combo или продление на 1 год.",
     "Апселл / особые"),
    ("combo_active", "Активные Combo",
     "Сейчас активная подписка типа Combo (Basic/Plus). Целевая для апселла на большие GB-паки обхода / доп. устройств.",
     "Апселл / особые"),
    ("discount_active", "Активная персональная скидка",
     "У пользователя действует скидка в user_discounts (не broadcast). Напомнить: «у тебя действует скидка N% — воспользуйся».",
     "Апселл / особые"),
    ("has_balance_50plus", "Баланс ≥ 50₽",
     "На балансе не меньше 50₽. Напоминание использовать балансовый чекаут.",
     "Апселл / особые"),
    ("bought_proxy", "Купил прокси",
     "Юзер купил отдельный товар «Telegram MT Прокси» (users.proxy_purchased_at IS NOT NULL). "
     "Целевая для допродажи VPN-подписки, апдейтов по прокси или лояльных предложений.",
     "Апселл / особые"),
)

FIXED_LABELS: dict[str, str] = {k: label for k, label, _d, _g in FIXED_SEGMENTS}
FIXED_KEYS = frozenset(FIXED_LABELS)


def catalog() -> list[dict]:
    """Fixed + parametric entries in display order: each group's parametric
    bases first, then its fixed keys; groups in first-seen order."""
    groups: dict[str, list[dict]] = {}
    for spec in PARAMETRIC.values():
        groups.setdefault(spec.group, [])
    order: list[str] = []
    for _k, _l, _d, g in FIXED_SEGMENTS:
        if g not in order:
            order.append(g)
    for g in groups:
        if g not in order:
            order.append(g)
    out: dict[str, list[dict]] = {g: [] for g in order}
    for spec in PARAMETRIC.values():
        out[spec.group].append({
            "key": spec.base,
            "label": spec.title,
            "description": spec.description,
            "group": spec.group,
            "parametric": True,
            "default_window": spec.default,
            "direction": spec.direction,
            "allow_any": spec.allow_any,
            "units": ["d", "m"],
            "max_days": MAX_DAYS,
            "max_months": MAX_MONTHS,
        })
    for key, label, description, group in FIXED_SEGMENTS:
        out[group].append({"key": key, "label": label, "description": description,
                           "group": group, "parametric": False})
    return [item for g in order for item in out[g]]
