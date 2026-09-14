"""«Мой профиль» / «Моя подписка» show the ACTUAL state (owner 2026-09-14:
«актуальную информацию надо давать 100%») — the panel reconciled with the DB,
every state: trial, paid, paid + GB, expired + GB, expired without GB,
bypass-only, pending activation, panel down. The screen's panel read also
feeds the traffic threshold check (no extra request)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from app import i18n
from app.handlers.common import screens
from app.services.subscriptions import live_state as ls

TG = 4242
GB = 1024 ** 3
NOW = datetime.now(timezone.utc)
UNTIL = (NOW + timedelta(days=20)).replace(hour=21, minute=30, second=0, microsecond=0)   # 00:30 MSK next day
MSK_DATE = (UNTIL + timedelta(hours=3)).strftime("%d.%m.%Y")


def _sub(**kw):
    row = {"telegram_id": TG, "status": "active", "source": "payment", "is_bypass_only": False,
           "activation_status": "active", "expires_at": UNTIL, "subscription_type": "basic",
           "is_combo": False, "auto_renew": True, "uuid": "u-1", "remnawave_uuid": "rw-1"}
    row.update(kw)
    return row


def _bypass(used, limit, status="ACTIVE"):
    return ls.BypassInfo("present", used=used, limit=limit, status=status)


PANEL_UNTIL = UNTIL
STATES = {
    "trial": (_sub(source="trial", expires_at=NOW + timedelta(days=2)),
              ls.PremiumInfo("present", NOW + timedelta(days=2), "ACTIVE"), _bypass(100 * 1024 ** 2, 500 * 1024 ** 2)),
    "paid": (_sub(), ls.PremiumInfo("present", PANEL_UNTIL, "ACTIVE"), ls.BypassInfo("absent")),
    "paid_gb": (_sub(), ls.PremiumInfo("present", PANEL_UNTIL, "ACTIVE"), _bypass(3 * GB, 10 * GB)),
    "expired_gb": (_sub(source="bypass_only", is_bypass_only=True, uuid=None,
                        expires_at=NOW + timedelta(days=3650)), ls.PremiumInfo("absent"), _bypass(2 * GB, 10 * GB)),
    "expired_no_gb": (_sub(status="expired", uuid=None, expires_at=NOW - timedelta(days=2)),
                      ls.PremiumInfo("absent"), _bypass(10 * GB, 10 * GB, "LIMITED")),
    "bypass_only": (_sub(source="bypass_only", is_bypass_only=True, uuid=None, auto_renew=False,
                         expires_at=NOW + timedelta(days=3650)), ls.PremiumInfo("absent"), _bypass(0, 50 * GB)),
    "pending": (_sub(activation_status="pending", uuid=None), ls.PremiumInfo("absent"), ls.BypassInfo("absent")),
    "panel_down": (_sub(), ls.UNAVAILABLE_PREMIUM, ls.UNAVAILABLE_BYPASS),
}


@pytest.fixture
def env(monkeypatch):
    sent = {}

    async def screen_photo(bot, chat_id, photo, text, reply_markup=None, parse_mode="HTML"):
        sent["text"], sent["kb"] = text, reply_markup
        return MagicMock()

    live = AsyncMock()
    monkeypatch.setattr(screens, "_send_screen_photo", screen_photo)
    monkeypatch.setattr(screens, "check_subscription_expiry_service", AsyncMock(return_value=False))
    monkeypatch.setattr(screens, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(database, "get_user", AsyncMock(return_value={"telegram_id": TG, "first_name": "Аня"}))
    monkeypatch.setattr(database, "get_user_balance", AsyncMock(return_value=123.5))
    monkeypatch.setattr(database, "get_referral_stats", AsyncMock(return_value={"total_referred": 2}))
    monkeypatch.setattr(database, "get_remnawave_uuid", AsyncMock(return_value="rw-1"))
    monkeypatch.setattr(database, "has_purchased_proxy", AsyncMock(return_value=False))
    from app.services import remnawave_service
    monkeypatch.setattr(remnawave_service, "_fire_and_forget", lambda *_a, **_k: None)
    from app.workers import traffic_monitor
    monkeypatch.setattr(traffic_monitor, "check_live", live)
    sent["live"] = live
    return sent


def _event(lang="ru"):
    ev = MagicMock()
    ev.from_user = SimpleNamespace(id=TG, first_name="Аня", username=None)
    ev.message.chat.id = TG
    ev.message.delete = AsyncMock()
    ev.answer = AsyncMock()
    ev.bot = MagicMock()
    return ev


def _view(name):
    sub, premium, bypass = STATES[name]
    return ls.reconcile(sub, premium, bypass)


async def _open(which, monkeypatch, name, lang="ru"):
    monkeypatch.setattr(ls, "get_view", AsyncMock(return_value=_view(name)))
    import asyncio
    ev = _event()
    if which == "profile":
        await screens.show_profile(ev, lang)
    else:
        await screens._open_my_subscription_screen(ev, ev.bot)
    await asyncio.sleep(0)


def t(key, **kw):
    return i18n.get_text("ru", key, **kw)


@pytest.mark.parametrize("which", ["profile", "my_sub"])
async def test_paid_with_gb(env, monkeypatch, which):
    await _open(which, monkeypatch, "paid_gb")
    text = env["text"]
    active = t("profile.info_active_until", date=MSK_DATE) if which == "profile" else t("main.my_sub_active_until", date=MSK_DATE)
    assert active in text, text                                   # MSK date (#21)
    assert "⚡️ Basic" in text
    left = "profile.info_bypass_left" if which == "profile" else "main.my_sub_bypass_left"
    assert t(left, remaining="7 ГБ", limit="10 ГБ") in text
    assert t("profile.info_auto_renew_on") in text and "123.50" in text
    assert t("profile.panel_unavailable") not in text
    env["live"].assert_awaited_once()
    assert env["live"].await_args.kwargs == {"used": 3 * GB, "limit": 10 * GB, "premium": True}


@pytest.mark.parametrize("which", ["profile", "my_sub"])
async def test_trial(env, monkeypatch, which):
    await _open(which, monkeypatch, "trial")
    assert t("profile.tariff_trial") in env["text"]
    assert "400 МБ" in env["text"] and "500 МБ" in env["text"]


@pytest.mark.parametrize("which", ["profile", "my_sub"])
@pytest.mark.parametrize("name", ["expired_gb", "bypass_only"])
async def test_premium_ended_bypass_works(env, monkeypatch, which, name):
    await _open(which, monkeypatch, name)
    text = env["text"]
    assert (t("profile.info_inactive") if which == "profile" else t("main.my_sub_active_until_none")) in text
    assert t("profile.info_auto_renew_none") in text
    left = "profile.info_bypass_left" if which == "profile" else "main.my_sub_bypass_left"
    b = STATES[name][2]
    assert t(left, remaining=ls.format_bytes("ru", b.remaining), limit=ls.format_bytes("ru", b.limit)) in text
    assert env["live"].await_args.kwargs["premium"] is False


@pytest.mark.parametrize("which", ["profile", "my_sub"])
async def test_expired_without_gb(env, monkeypatch, which):
    await _open(which, monkeypatch, "expired_no_gb")
    text = env["text"]
    assert (t("profile.info_inactive") if which == "profile" else t("main.my_sub_active_until_none")) in text
    left = "profile.info_bypass_left" if which == "profile" else "main.my_sub_bypass_left"
    assert t(left, remaining="0 ГБ", limit="10 ГБ") in text


@pytest.mark.parametrize("which", ["profile", "my_sub"])
async def test_pending_activation(env, monkeypatch, which):
    await _open(which, monkeypatch, "pending")
    assert (t("profile.status_pending") if which == "profile" else t("main.my_sub_pending")) in env["text"]
    env["live"].assert_not_awaited()                              # no bypass entity yet


@pytest.mark.parametrize("which", ["profile", "my_sub"])
async def test_panel_down_shows_db_values_and_says_so(env, monkeypatch, which):
    await _open(which, monkeypatch, "panel_down")
    text = env["text"]
    active = t("profile.info_active_until", date=MSK_DATE) if which == "profile" else t("main.my_sub_active_until", date=MSK_DATE)
    assert active in text                                         # the DB date
    assert t("profile.panel_unavailable") in text                 # …said so
    none = "profile.info_bypass_none" if which == "profile" else "main.my_sub_bypass_none"
    assert t(none) in text                                        # never a stale GB number
    env["live"].assert_not_awaited()


@pytest.mark.parametrize("which", ["profile", "my_sub"])
async def test_panel_date_wins_over_the_db(env, monkeypatch, which):
    sub, _p, bypass = STATES["paid"]
    later = UNTIL + timedelta(days=30)
    monkeypatch.setattr(ls, "get_view", AsyncMock(return_value=ls.reconcile(
        sub, ls.PremiumInfo("present", later, "ACTIVE"), bypass)))
    ev = _event()
    if which == "profile":
        await screens.show_profile(ev, "ru")
    else:
        await screens._open_my_subscription_screen(ev, ev.bot)
    assert (later + timedelta(hours=3)).strftime("%d.%m.%Y") in env["text"]


async def test_english_screen_has_no_russian_units(env, monkeypatch):
    monkeypatch.setattr(ls, "get_view", AsyncMock(return_value=_view("paid_gb")))
    monkeypatch.setattr(screens, "resolve_user_language", AsyncMock(return_value="en"))
    ev = _event()
    await screens._open_my_subscription_screen(ev, ev.bot)
    assert "7 GB" in env["text"] and "ГБ" not in env["text"]
