"""The owner's example, end to end (docs/audit/03_payment_matrix.md §3):

  «подписка до 20 октября, купил месяц → и в БД, и в панели 19/20 ноября»,

and the variant where the panel PATCH fails: never silent — forced admin
alert + payment_errors, then retry / re-sync brings the panel to the DB date.

Owner decision (merged 439828ec): paid periods are calendar months —
20 Oct + 1 month = 20 Nov, the same date in the DB and in the panel.
"""
from datetime import datetime, timedelta

import pytest

import wata_service
from tests.e2e import flows
from tests.e2e.world import GIB, UTC, new_user, utcnow

OCT20 = datetime(utcnow().year + (1 if utcnow().month >= 10 else 0), 10, 20, 12, 0, tzinfo=UTC)
EXPECTED_END = OCT20.replace(month=11)              # 20 Nov 12:00 UTC (calendar month)


async def _until_oct20(e2e, u, *, seed_flag=None):
    await flows.seed_active(e2e, u, "basic", OCT20, flag=seed_flag)
    assert (await e2e.sub(u.id))["expires_at"] == OCT20
    assert e2e.panel.premium_expire(u.id) == OCT20


async def _pay(e2e, u, method):
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    if method == "balance":
        await e2e.pool.execute("UPDATE users SET balance = balance + 19900 WHERE telegram_id=$1", u.id)
    pending = await flows.buy(e2e, u, "basic", 30, method)
    return None if pending is None else await flows.pay(e2e, u, pending, method)


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("method", ["sbp", "wata", "crypto", "card", "stars", "balance"])
async def test_owner_example_until_oct20_buys_month_panel_shows_nov20(e2e, method, flag):
    u = new_user()
    await _until_oct20(e2e, u)
    e2e.provisioning(flag)
    limit = e2e.panel.bypass_limit(u.id)
    mark = e2e.tg.mark()

    res = await _pay(e2e, u, method)
    if res is not None and hasattr(res, "status"):
        assert (res.status, res.body["status"]) == (200, "ok"), res
    await e2e.provisioning_tick()

    assert (await e2e.sub(u.id))["expires_at"] == EXPECTED_END, "DB end date"
    assert abs((e2e.panel.premium_expire(u.id) - EXPECTED_END).total_seconds()) <= 1, "panel expireAt"
    assert e2e.panel.bypass_limit(u.id) == limit + 10 * GIB
    assert e2e.user_texts(u.id, mark) and e2e.admin_texts(mark) == []


def _premium_patch(e2e, u):
    return lambda req, body: (req.url.path == "/api/users" and body.get("id") == e2e.panel.premium(u.id)["id"]
                              and "expireAt" in body)


async def test_owner_example_panel_patch_fails_legacy_alerts_and_resyncs(e2e):
    """Flag off: the premium PATCH fails → the payment is kept, the user is told,
    forced alert + payment_errors(renewal_sync), 5xx so the provider retries;
    the background re-sync brings the panel to the DB date; the retry is
    already_processed. (The subscriber has the Remnawave cache columns — the
    usual state; without them see the xfail below.)"""
    u = new_user()
    await _until_oct20(e2e, u, seed_flag="on")
    await _patch_fails_then_resyncs(e2e, u)


async def test_owner_example_panel_patch_fails_without_cache_still_resyncs(e2e):
    """Same without the premium cache (E2E-CACHE): the renewal goes through the
    adopt path. Before fix E2E-ADOPT-SILENT a failed adopt PATCH was only
    logged — 200 to the provider, no alert, no re-sync, panel on the old date."""
    u = new_user()
    await _until_oct20(e2e, u)
    await _patch_fails_then_resyncs(e2e, u)


async def _patch_fails_then_resyncs(e2e, u):
    e2e.panel.fail("PATCH", _premium_patch(e2e, u), status=500)
    mark = e2e.tg.mark()

    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    hook_res = await flows.pay(e2e, u, pending, "sbp")
    assert hook_res.status == 500, hook_res                 # provider retries
    assert (await e2e.sub(u.id))["expires_at"] == EXPECTED_END   # money + DB kept
    assert len(await e2e.payments(u.id)) == 2
    assert e2e.panel.premium_expire(u.id) == OCT20          # panel still behind…
    alerts = e2e.admin_texts(mark)
    assert alerts, "panel failure was silent"
    assert any(e["stage"] == "renewal_sync" for e in await e2e.payment_errors())
    assert e2e.user_texts(u.id, mark), "user not told about the payment"

    e2e.panel.clear_failures()                             # panel is back
    await e2e.release_resync()
    assert abs((e2e.panel.premium_expire(u.id) - EXPECTED_END).total_seconds()) <= 1, "re-sync did not fix the panel"

    retry = await flows.pay(e2e, u, pending, "sbp")        # the provider's retry
    assert (retry.status, retry.body["status"]) == (200, "already_processed")
    assert (await e2e.sub(u.id))["expires_at"] == EXPECTED_END
    assert len(await e2e.payments(u.id)) == 2


async def test_owner_example_panel_patch_fails_outbox_alerts_and_worker_finishes(e2e):
    """Flag on: committed billing + a pending provisioning job + a forced alert;
    the worker finishes the job when the panel is back."""
    u = new_user()
    await _until_oct20(e2e, u)
    e2e.provisioning("on")
    e2e.panel.fail("PATCH", _premium_patch(e2e, u), status=500)
    mark = e2e.tg.mark()

    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.status == 200 and res.body["status"] == "ok", res
    assert (await e2e.sub(u.id))["expires_at"] == EXPECTED_END
    jobs = await e2e.jobs(u.id)
    assert jobs and jobs[-1]["status"] == "pending", jobs
    assert e2e.admin_texts(mark), "panel failure was silent"
    assert e2e.panel.premium_expire(u.id) == OCT20

    e2e.panel.clear_failures()
    await e2e.pool.execute("UPDATE provisioning_jobs SET next_attempt_at = NOW() - interval '1 second' "
                           "WHERE telegram_id=$1", u.id)
    await e2e.provisioning_tick()
    assert (await e2e.jobs(u.id))[-1]["status"] == "done"
    assert abs((e2e.panel.premium_expire(u.id) - EXPECTED_END).total_seconds()) <= 1


async def test_premium_is_never_shortened_by_a_shorter_purchase(e2e):
    """Owner rule «premium never shortened»: 1 year left, buy 1 month → +1 calendar month on top."""
    u = new_user()
    far = utcnow() + timedelta(days=365)
    await flows.seed_active(e2e, u, "basic", far)
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    await flows.pay(e2e, u, pending, "sbp")
    exp = (await e2e.sub(u.id))["expires_at"]
    assert abs((exp - flows.add_months(far, 1)).total_seconds()) < 2
    assert e2e.panel.premium_expire(u.id) >= far
