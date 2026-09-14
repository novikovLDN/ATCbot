"""Tariff catalog: what a purchase / grant entitles the user to.

Pure, no I/O. Reads only config.TARIFFS, COMBO_TARIFFS, TRAFFIC_LIMITS,
TRAFFIC_PACKS(_EXTENDED), TRIAL_BYPASS_MB — adds no prices or constants
of its own. Spec: docs/audit/02_payment_core_plan.md §A.1 + §G0.

Rules (owner decisions, docs/audit/SCOPE.md):
  basic / plus        premium for the period + 10 GB bypass (TRAFFIC_LIMITS), once
  combo_basic / plus  premium for the period + COMBO_TARIFFS[key][period]["gb"],
                      no extra 10 GB; unknown period → TariffConfigError
  traffic pack N GB   +N GB bypass, premium untouched
  trial               TRIAL_BYPASS_MB MB bypass + 3 days premium
  bypass-only gift    3 days premium, 0 GB (GB pack from «Только обход» without
                      a subscription; one-time, same store as the trial)
  day grants          (admin/game/promo/bonus) premium only, 0 GB
  bypass gift N GB    +N GB bypass, premium untouched
  paid period         CALENDAR months (owner, 2026-09-14): period_days 30/90/180/
                      365/730 is only the catalog KEY; the new end is
                      extend_expiry(max(current end, now), period_days) —
                      Oct 20 + 1 month = Nov 20, Jan 31 + 1 month = Feb 28/29.
                      Day grants (admin, game, promo, bonus, trial) stay days.
  legacy biz_*        business tariffs were removed (owner, 2026-09-14). Any
                      stored biz_* value is Plus — normalize_tier() is the ONE
                      place that says so; tariff_key()/for_grant() apply it.

Invalid input always raises TariffConfigError — never a silent fallback.

Prices: renewal_price_rub() returns the catalog price from config only.
Admin price overrides / global discount (migration 069) are applied by the
async, DB-backed app.services.pricing.get_effective_price, which only knows
config.TARIFFS keys (basic/plus); callers that need the effective price for
basic/plus must call it themselves — this module stays I/O-free.
"""
from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

GIB = 1024 ** 3
MIB = 1024 ** 2

BASE_TARIFFS = ("basic", "plus")
COMBO_KEYS = ("combo_basic", "combo_plus")
TRIAL_DAYS = 3

_PACK_TARIFF_RE = re.compile(r"^(?:traffic|bypass)_(\d+)gb$")


class TariffConfigError(ValueError):
    """Unknown tariff/period/pack size or invalid amount."""


@dataclass(frozen=True)
class Entitlement:
    tariff_key: str               # basic|plus|combo_basic|combo_plus|trial|pack|grant|bypass_gift
    premium_days: int             # 0 = premium untouched
    premium_tier: Optional[str]   # value for subscriptions.subscription_type (None = untouched)
    bypass_bytes: int             # bytes to ADD to the bypass limit, once


def _require_positive_int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TariffConfigError(f"{what} must be a positive int, got {value!r}")
    return value


LEGACY_BIZ_PREFIX = "biz_"


def normalize_tier(value):
    """The single legacy-tariff rule: a stored business tariff (biz_*) is Plus.

    Business tariffs were removed (owner decision 2026-09-14); old rows in
    subscriptions.subscription_type / pending_purchases.tariff / gifts may
    still carry biz_starter … biz_ultimate. Every read of such a value that
    feeds display, renewal, provisioning or GB rules goes through here, so
    a legacy biz user is a Plus user everywhere. Anything else is returned
    unchanged (None stays None).
    """
    if isinstance(value, str) and value.strip().lower().startswith(LEGACY_BIZ_PREFIX):
        return "plus"
    return value


def normalize_payment_tariff(value):
    """payments.tariff "<tier>_<days>" with the tier normalized:
    "biz_team_30" → "plus_30", "biz_team" → "plus". Anything else unchanged."""
    if not (isinstance(value, str) and value.strip().lower().startswith(LEGACY_BIZ_PREFIX)):
        return value
    last = value.strip().rsplit("_", 1)[-1]
    return f"plus_{last}" if last.isdigit() else "plus"


def tariff_key(tariff: str, is_combo: bool) -> str:
    """Explicit catalog key from the (tariff, is_combo) pair stored in
    pending_purchases / subscriptions.

    ("basic", True) → "combo_basic", ("plus", True) → "combo_plus",
    otherwise the tariff as-is (must be a known tariff). Legacy biz_* → plus.
    """
    tariff = normalize_tier(tariff)
    if is_combo:
        if tariff in BASE_TARIFFS:
            return f"combo_{tariff}"
        if tariff in COMBO_KEYS:
            return tariff
        raise TariffConfigError(f"no combo variant for tariff {tariff!r}")
    if tariff in BASE_TARIFFS or tariff in COMBO_KEYS:
        return tariff
    raise TariffConfigError(f"unknown tariff {tariff!r}")


def _combo_row(key: str, period_days: int) -> dict:
    row = (config.COMBO_TARIFFS.get(key) or {}).get(period_days)
    if not row:
        raise TariffConfigError(f"no period {period_days}d for {key!r}")
    return row


def for_purchase(key: str, period_days: int) -> Entitlement:
    """Entitlement for a paid purchase or renewal of catalog tariff `key`."""
    _require_positive_int(period_days, "period_days")
    if key in BASE_TARIFFS:
        bypass = (config.TRAFFIC_LIMITS.get(key) or {}).get(period_days)
        if not bypass:
            raise TariffConfigError(f"no period {period_days}d for {key!r}")
        return Entitlement(key, period_days, key, int(bypass))
    if key in COMBO_KEYS:
        row = _combo_row(key, period_days)
        gb = _require_positive_int(row.get("gb"), f"COMBO_TARIFFS[{key}][{period_days}].gb")
        tier = row.get("base_tariff")
        if tier not in BASE_TARIFFS:
            raise TariffConfigError(f"bad base_tariff {tier!r} for {key!r}")
        return Entitlement(key, period_days, tier, gb * GIB)
    raise TariffConfigError(f"unknown tariff {key!r}")


def parse_pack_gb(tariff: str) -> int:
    """GB size from a traffic-pack tariff string: "traffic_15gb" / "bypass_15gb" → 15."""
    m = _PACK_TARIFF_RE.match(tariff) if isinstance(tariff, str) else None
    if not m or int(m.group(1)) <= 0:
        raise TariffConfigError(f"not a traffic-pack tariff: {tariff!r}")
    return int(m.group(1))


def for_pack(gb: int) -> Entitlement:
    """Traffic pack: +gb GB bypass, premium untouched. Size must be in the catalog."""
    _require_positive_int(gb, "pack gb")
    if gb not in config.TRAFFIC_PACKS and gb not in config.TRAFFIC_PACKS_EXTENDED:
        raise TariffConfigError(f"unknown traffic pack size {gb} GB")
    return Entitlement("pack", 0, None, gb * GIB)


def for_trial(days: int = TRIAL_DAYS) -> Entitlement:
    """Trial: TRIAL_BYPASS_MB MB bypass + `days` of basic premium."""
    _require_positive_int(days, "trial days")
    mb = _require_positive_int(config.TRIAL_BYPASS_MB, "TRIAL_BYPASS_MB")
    return Entitlement("trial", days, "basic", mb * MIB)


def for_bypass_purchase_gift(days: int = TRIAL_DAYS) -> Entitlement:
    """GB pack bought from «🌐 Только обход блокировок» without a subscription
    (owner, 2026-09-14): the trial's `days` of basic premium as a one-time gift,
    WITHOUT the trial's TRIAL_BYPASS_MB — the buyer's GB are the pack's GB."""
    _require_positive_int(days, "gift days")
    return Entitlement("trial", days, "basic", 0)


def for_grant(tariff: str, days: int) -> Entitlement:
    """Day grant without a purchase (admin, game, promo, bonus): premium only, 0 GB."""
    _require_positive_int(days, "grant days")
    tariff = normalize_tier(tariff)
    if tariff in BASE_TARIFFS:
        tier = tariff
    elif tariff in COMBO_KEYS:
        tier = tariff.removeprefix("combo_")
    else:
        raise TariffConfigError(f"unknown tariff {tariff!r}")
    return Entitlement("grant", days, tier, 0)


def for_grant_duration(tariff: str, duration: timedelta) -> Entitlement:
    """Day grant for an arbitrary positive duration (admin minutes/hours grants).

    Same as for_grant, premium only, 0 GB. premium_days is the duration rounded UP
    to whole days (>= 1, so "premium touched" holds). It is metadata only: the
    exact end is the premium_until passed with the job, never derived from
    premium_days. A whole number of days gives exactly for_grant(tariff, days).
    """
    if not isinstance(duration, timedelta) or duration <= timedelta(0):
        raise TariffConfigError(f"grant duration must be a positive timedelta, got {duration!r}")
    days = duration.days + (1 if (duration.seconds or duration.microseconds) else 0)
    return for_grant(tariff, days)


def for_bypass_gift(gb: int) -> Entitlement:
    """GB reward (bypass gift link, broadcast key, …): +gb GB, premium untouched."""
    _require_positive_int(gb, "gift gb")
    return Entitlement("bypass_gift", 0, None, gb * GIB)


# Owner decision 2026-09-14: a paid tariff period is a number of CALENDAR months.
# period_days stays the catalog KEY everywhere (prices, DB columns, payloads,
# callback_data, dashboard) — only the expiry arithmetic changes.
PERIOD_MONTHS = {30: 1, 90: 3, 180: 6, 365: 12, 730: 24}


def months_for_period(period_days) -> Optional[int]:
    """Calendar months of a catalog period (30→1, 90→3, 180→6, 365→12, 730→24);
    None for anything else (day grants, odd values)."""
    if isinstance(period_days, bool) or not isinstance(period_days, int):
        return None
    return PERIOD_MONTHS.get(period_days)


def add_calendar_months(base: datetime, months: int) -> datetime:
    """`base` + `months` calendar months, same time of day. A day the target month
    does not have is clamped to its last day (Jan 31 + 1 → Feb 28/29)."""
    idx = base.month - 1 + months
    year, month = base.year + idx // 12, idx % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def extend_expiry(base: datetime, period_days: int) -> datetime:
    """THE end of a paid tariff period that starts at `base` (the end of the current
    subscription, or now): a catalog period → calendar months (add_calendar_months);
    any other positive period → `period_days` days. `base` must be timezone-aware;
    the result is UTC and always later than `base` (premium is never shortened).
    DB and panel get this same value (the panel PATCH reads the DB end)."""
    _require_positive_int(period_days, "period_days")
    if not isinstance(base, datetime) or base.utcoffset() is None:
        raise TariffConfigError(f"extend_expiry needs a timezone-aware datetime, got {base!r}")
    base = base.astimezone(timezone.utc)
    months = months_for_period(period_days)
    if months is None:
        return base + timedelta(days=period_days)
    return add_calendar_months(base, months)


def vpn_period_days(tariff, period_days) -> Optional[int]:
    """`period_days` when it is a paid VPN catalog period that counts in calendar
    months (basic / plus / combo_*, legacy biz_* = plus); None otherwise — e.g. the
    VPN bonus of a shop purchase (spotify_*) keeps plain days. The value to pass as
    grant_access(tariff_period_days=…)."""
    t = normalize_tier(tariff)
    t = t.strip().lower() if isinstance(t, str) else t
    if (t in BASE_TARIFFS or t in COMBO_KEYS) and months_for_period(period_days):
        return period_days
    return None


def premium_device_limit(tier) -> int:
    """Devices allowed on the PREMIUM panel entity (hwidDeviceLimit) for a tier —
    owner 2026-09-14: Basic 10, Plus 14 (config.PREMIUM_DEVICE_LIMITS), as the
    tariff texts say. combo_basic / combo_plus → their base tier; legacy biz_* →
    Plus; the trial is a Basic premium. Anything else (unknown / None) → the
    REMNAWAVE_PREMIUM_DEVICE_LIMIT env value (a fallback only)."""
    t = normalize_tier(tier)
    t = t.strip().lower() if isinstance(t, str) else t
    if t in COMBO_KEYS:
        t = t.removeprefix("combo_")
    if t == "trial":
        t = "basic"
    limits = getattr(config, "PREMIUM_DEVICE_LIMITS", {}) or {}
    if t in limits:
        return int(limits[t])
    return int(getattr(config, "REMNAWAVE_PREMIUM_DEVICE_LIMIT", 5))


def stars_for_rub(rub: float) -> int:
    """Stars for a RUB price: ceil(rub × STARS_MARKUP / RUB_PER_STAR) — the rule
    the top-up and gift Stars invoices use."""
    return math.ceil(float(rub) * config.STARS_MARKUP / config.RUB_PER_STAR)


def stars_price(key: str, period_days: int) -> int:
    """Telegram Stars price of catalog tariff `key` for `period_days`:
    basic/plus — the TARIFFS_STARS table; combo_* — stars_for_rub(combo RUB price).
    Fixed price: no promo / personal discounts. Unknown → TariffConfigError."""
    _require_positive_int(period_days, "period_days")
    if key in BASE_TARIFFS:
        row = (config.TARIFFS_STARS.get(key) or {}).get(period_days)
        if not row:
            raise TariffConfigError(f"no Stars price for {key!r} {period_days}d")
        return _require_positive_int(row.get("price"), f"Stars price of {key!r} {period_days}d")
    if key in COMBO_KEYS:
        return stars_for_rub(renewal_price_rub(key, period_days))
    raise TariffConfigError(f"no Stars price for tariff {key!r}")


def stars_for_purchase(key: str, period_days: int, price_kopecks: int) -> int:
    """Stars for a purchase whose RUB price (what the payment screen showed) is
    `price_kopecks`. At or above the catalog price → the fixed stars_price. A
    discounted price (promo, −15 %, personal / broadcast offers, dashboard
    discount — the largest single one) → the same RUB→Stars rule as top-ups
    (stars_for_rub), never more than the undiscounted Stars price. Both the
    invoice and the successful_payment amount check use this one rule."""
    full = stars_price(key, period_days)
    list_kopecks = renewal_price_rub(key, period_days) * 100
    price_kopecks = int(price_kopecks or 0)
    if price_kopecks <= 0 or price_kopecks >= list_kopecks:
        return full
    return max(1, min(full, stars_for_rub(price_kopecks / 100)))


def renewal_price_rub(key: str, period_days: int) -> int:
    """Catalog price (RUB) for renewing `key`: TARIFFS for basic/plus,
    COMBO_TARIFFS for combo_*. Pass a tariff_key() result (legacy biz_* is
    mapped to plus there). Config only — see module docstring on overrides."""
    _require_positive_int(period_days, "period_days")
    if key in BASE_TARIFFS:
        row = (config.TARIFFS.get(key) or {}).get(period_days)
        if not row:
            raise TariffConfigError(f"no period {period_days}d for {key!r}")
    elif key in COMBO_KEYS:
        row = _combo_row(key, period_days)
    else:
        raise TariffConfigError(f"no renewal price for tariff {key!r}")
    return _require_positive_int(row.get("price"), f"price of {key!r} {period_days}d")
