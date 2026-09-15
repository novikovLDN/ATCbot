"""Broadcast segment API: /segments shape, /segments/count, 400 on bad keys
everywhere a key comes in, send / schedule accept a parametric key, and the
Overview quick action «Предложить продление со скидкой»."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import tests.conftest  # noqa: F401  (env before config)
from app.api.dashboard.routes import automated_notifications as an_route
from app.api.dashboard.routes import broadcasts as br
from app.services.renewal_offer import service as ro
from app.utils.telegram_html import telegram_html_errors

ADMIN = {"sub": "1"}


@pytest.fixture
def world(monkeypatch):
    br.reset_segment_counts_cache()
    st = {"asked": [], "members": {}, "created": [], "discounts": [], "sent": []}

    async def members(key):
        st["asked"].append(key)
        return st["members"].get(key, [11, 12, 13])

    async def create_broadcast(**kw):
        st["created"].append(kw)
        return 501

    async def save_discount(*a):
        st["discounts"].append(a)

    async def send_broadcast(**kw):
        st["sent"].append(kw)

    async def count(key):
        return len(await members(key))

    monkeypatch.setattr(br.database, "get_users_by_segment", members)
    monkeypatch.setattr(br.database, "count_users_by_segment", count)
    monkeypatch.setattr(br.database, "create_broadcast", create_broadcast, raising=False)
    monkeypatch.setattr(br.database, "save_broadcast_discount", save_discount, raising=False)
    monkeypatch.setattr(br, "_get_bot", lambda: object())
    monkeypatch.setattr(br.bus, "publish", lambda *_a, **_k: None)
    import app.services.broadcast_sender as sender
    monkeypatch.setattr(sender, "send_broadcast", send_broadcast)
    yield st
    br.reset_segment_counts_cache()


def _body(**kw):
    base = dict(title="t", message="hello", segment="paid_ended:6m", buttons=[])
    base.update(kw)
    return br.BroadcastCreateRequest(**base)


# ── /segments ────────────────────────────────────────────────────────


async def test_segments_marks_parametric_ones_and_counts_their_default_window(world):
    items = {s["key"]: s for s in await br.segments_list()}
    p = items["paid_ended"]
    assert p["parametric"] is True and p["default_window"] == "30d" and p["allow_any"] is True
    assert p["units"] == ["d", "m"] and p["direction"] == "past"
    assert p["count"] == 3 and p["default_label"] == "Платная истекла, не продлил за последние 30 дней"
    assert items["paid_expiring"]["allow_any"] is False and items["paid_expiring"]["direction"] == "future"
    assert "paid_ended:30d" in world["asked"] and "paid_ended" not in world["asked"]
    fixed = items["bypass_only_now"]
    assert fixed["parametric"] is False and fixed["group"] == "Обход" and "default_window" not in fixed
    for g in ("Обход", "Подарки и дни от админа", "Лояльность", "Активность", "Рефералы"):
        assert any(s["group"] == g for s in items.values()), g


async def test_count_endpoint_labels_and_caches_per_full_key(world):
    r = await br.segment_count(key="trial_ended:any")
    assert r == {"key": "trial_ended:any", "label": "Пробный закончился, не купил за всё время", "count": 3}
    await br.segment_count(key="trial_ended:any")
    await br.segment_count(key="trial_ended:6m")
    assert world["asked"].count("trial_ended:any") == 1 and world["asked"].count("trial_ended:6m") == 1


@pytest.mark.parametrize("key", ["nope", "paid_ended", "paid_ended:0d", "paid_ended:3651d",
                                 "paid_ended:121m", "inactive:any", "x:7d", "paid_lapsed_any:7d"])
async def test_count_endpoint_rejects_bad_keys_with_400(world, key):
    with pytest.raises(HTTPException) as e:
        await br.segment_count(key=key)
    assert e.value.status_code == 400 and "invalid_segment" in e.value.detail
    assert world["asked"] == []


async def test_a_failed_count_is_minus_one_and_not_cached(world, monkeypatch):
    async def boom(_key):
        raise RuntimeError("db down")
    monkeypatch.setattr(br.database, "count_users_by_segment", boom)
    assert (await br.segment_count(key="paid_ended:7d"))["count"] == -1
    assert "paid_ended:7d" not in br._segment_counts


# ── send / schedule / notification filter ────────────────────────────


async def test_create_sends_to_the_full_parametric_key(world):
    world["members"]["paid_ended:6m"] = [7, 8]
    r = await br.broadcast_create(_body(), admin=ADMIN)
    assert r == {"ok": True, "broadcast_id": 501, "audience": 2}
    assert world["created"][0]["segment"] == "paid_ended:6m"
    assert world["asked"] == ["paid_ended:6m"]


@pytest.mark.parametrize("key", ["paid_ended:0d", "nope", "trial_ended:1y"])
async def test_create_rejects_bad_keys_before_touching_the_db(world, key):
    with pytest.raises(HTTPException) as e:
        await br.broadcast_create(_body(segment=key), admin=ADMIN)
    assert e.value.status_code == 400 and world["asked"] == [] and world["created"] == []


async def test_create_on_a_db_failure_is_a_server_error_not_a_bad_key(world, monkeypatch):
    async def boom(_key):
        raise RuntimeError("timeout")
    monkeypatch.setattr(br.database, "get_users_by_segment", boom)
    with pytest.raises(HTTPException) as e:
        await br.broadcast_create(_body(), admin=ADMIN)
    assert e.value.status_code >= 500


async def test_schedule_accepts_a_parametric_override_and_rejects_a_bad_one(world, monkeypatch):
    made = []
    monkeypatch.setattr(br.database, "get_broadcast", AsyncMock(return_value={"title": "t", "message": "m",
                                                                              "segment": "all_users"}))
    monkeypatch.setattr(br.database, "get_broadcast_discount", AsyncMock(return_value=None))

    async def create_sched(**kw):
        made.append(kw)
        return 9
    monkeypatch.setattr(br.database, "create_scheduled_broadcast", create_sched)
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone(timedelta(hours=3))) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    ok = await br.broadcast_schedule_create(
        br.ScheduleBroadcastRequest(source_broadcast_id=1, scheduled_at_msk=at, segment="any_ended:12m"),
        admin=ADMIN)
    assert ok["sched_id"] == 9 and made[0]["segment"] == "any_ended:12m"
    with pytest.raises(HTTPException) as e:
        await br.broadcast_schedule_create(
            br.ScheduleBroadcastRequest(source_broadcast_id=1, scheduled_at_msk=at, segment="any_ended:0m"),
            admin=ADMIN)
    assert e.value.status_code == 400 and len(made) == 1


async def test_notification_segment_filter_is_validated(monkeypatch):
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(an_route, "update_notification", update)
    ok = an_route.UpdatePayload(trigger_config={"segment_filter": "paid_ended:3m"})
    assert (await an_route.patch_notification(ok, key="trial.reminder_24h", admin=ADMIN))["ok"]
    assert update.await_args.kwargs["trigger_config"] == {"segment_filter": "paid_ended:3m"}
    bad = an_route.UpdatePayload(trigger_config={"segment_filter": "paid_ended:3y"})
    with pytest.raises(HTTPException) as e:
        await an_route.patch_notification(bad, key="trial.reminder_24h", admin=ADMIN)
    assert e.value.status_code == 400
    cleared = an_route.UpdatePayload(trigger_config={"segment_filter": ""})
    assert (await an_route.patch_notification(cleared, key="trial.reminder_24h", admin=ADMIN))["ok"]


async def test_stored_keys_come_back_with_a_human_label(monkeypatch):
    monkeypatch.setattr(br.database, "get_recent_broadcasts", AsyncMock(return_value=[
        {"id": 1, "segment": "paid_ended:any"}, {"id": 2, "segment": "all_users"},
        {"id": 3, "segment": "gone_key"}]))
    rows = await br.broadcasts_recent(limit=3)
    assert [r["segment_label"] for r in rows] == [
        "Платная истекла, не продлил за всё время", "Все юзеры", "gone_key"]


# ── quick action: renewal offer ─────────────────────────────────────


@pytest.mark.parametrize("tpl", ro.TEMPLATES, ids=lambda t: t["id"])
@pytest.mark.parametrize("pct", ro.DISCOUNT_CHOICES)
def test_templates_render_to_valid_telegram_html(tpl, pct):
    text = ro.render(tpl["text"], pct, ro.DEFAULT_HOURS)
    assert telegram_html_errors(text) == []
    assert "{" not in text and f"{pct}%" in text and f"{ro.DEFAULT_HOURS} ч" in text


def test_templates_are_date_free_and_as_agreed():
    texts = {t["id"]: ro.render(t["text"], 15, 72) for t in ro.TEMPLATES}
    assert "Подписка Atlas Secure скоро заканчивается. Продлите сейчас со скидкой <b>15%</b>" in texts["early"]
    assert "доступ не прервётся ни на секунду 🤍" in texts["early"] and "Скидка действует 72 ч." in texts["early"]
    assert "на 3, 6 или 12 месяцев выгоднее всего" in texts["long"] and "Предложение действует 72 ч." in texts["long"]
    assert "обход продолжит работать, пока есть ГБ" in texts["keep"] and "действует 72 ч." in texts["keep"]
    assert [t["title"] for t in ro.TEMPLATES] == ["Продлите заранее", "Выгоднее на длинный срок",
                                                   "Не потеряйте доступ"]


async def test_renewal_offer_info_lists_templates_and_both_audiences(world):
    world["members"]["paid_expiring:7d"] = [1, 2, 3, 4]
    world["members"]["paid_expiring_manual:7d"] = [1, 2]
    info = await br.renewal_offer_info()
    assert info["audience"] == {"all": 4, "manual": 2}
    assert info["segments"] == {"all": "paid_expiring:7d", "manual": "paid_expiring_manual:7d"}
    assert info["default_discount"] == 15 and info["default_hours"] == 72
    assert info["discount_choices"] == [10, 15, 20] and len(info["templates"]) == 3


async def test_renewal_offer_needs_confirmation(world):
    body = br.RenewalOfferRequest(message=ro.TEMPLATES[0]["text"])
    with pytest.raises(HTTPException) as e:
        await br.renewal_offer_send(body, admin=ADMIN)
    assert e.value.status_code == 400 and e.value.detail == "confirm_required"
    assert world["created"] == []


@pytest.mark.parametrize("kw,detail", [
    ({"discount_percent": 12}, "discount_percent"),
    ({"discount_hours": 0}, "discount_hours"),
    ({"discount_hours": 169}, "discount_hours"),
    ({"message": "<b>скидка {discount}%"}, "invalid_html"),
])
async def test_renewal_offer_rejects_bad_input(world, kw, detail):
    body = br.RenewalOfferRequest(**{"message": ro.TEMPLATES[0]["text"], "confirm": True, **kw})
    with pytest.raises(HTTPException) as e:
        await br.renewal_offer_send(body, admin=ADMIN)
    assert e.value.status_code == 400 and detail in e.value.detail and world["created"] == []


async def test_renewal_offer_sends_to_manual_renewers_with_the_promo_discount(world):
    world["members"]["paid_expiring_manual:7d"] = [21, 22]
    body = br.RenewalOfferRequest(message=ro.TEMPLATES[2]["text"], discount_percent=20, discount_hours=48,
                                  confirm=True)
    r = await br.renewal_offer_send(body, admin=ADMIN)
    assert r["audience"] == 2 and r["broadcast_id"] == 501
    made = world["created"][0]
    assert made["segment"] == "paid_expiring_manual:7d" and made["buttons"] == ["promo_buy"]
    assert "со скидкой <b>20%</b> — действует 48 ч." in made["message"] and "{" not in made["message"]
    assert made["tag"] == "продление"
    assert world["discounts"] == [(501, 20, 48, "48 ч")]
    await __import__("asyncio").sleep(0)
    assert world["sent"] and world["sent"][0]["user_ids"] == [21, 22]
    markup = world["sent"][0]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "broadcast_promo_buy:501"


async def test_renewal_offer_can_include_auto_renewers(world):
    body = br.RenewalOfferRequest(message="Продлите со скидкой {discount}%", exclude_auto_renew=False,
                                  confirm=True)
    await br.renewal_offer_send(body, admin=ADMIN)
    assert world["created"][0]["segment"] == "paid_expiring:7d"
    assert world["discounts"] == [(501, 15, 72, "72 ч")]
