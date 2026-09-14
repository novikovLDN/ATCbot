"""Harness smoke: the real app answers /health on the real DB and /start reaches the user."""
from tests.e2e.world import new_user


async def test_health_is_ok_on_real_db(e2e):
    r = await e2e.http.get("/health")
    assert r.status_code == 200, r.text
    assert r.json()["database"] == "connected"


async def test_start_registers_user_and_shows_captcha(e2e):
    u = new_user()
    status = await e2e.send(u, "/start")
    assert status == 200
    assert await e2e.val("SELECT count(*) FROM users WHERE telegram_id=$1", u.id) == 1
    assert e2e.tg.to(u.id), "bot said nothing"
