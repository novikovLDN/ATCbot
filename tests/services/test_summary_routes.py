"""Сводка: маршруты главного экрана.

ЧТО ЗДЕСЬ ЗАЩИЩЕНО

    1. Ошибка не превращается в ноль. Это главный дефект прежней главной:
       упавший запрос рисовал «0 платежей», и человек читал это как «сегодня
       никто не платил». Проверяем на всех трёх уровнях — деньги отдают 500,
       число в зоне B приходит с value=null и пометкой, зона C сообщает про
       источник отдельно от списка.

    2. Порядок маршрутов. FastAPI отдаёт запрос ПЕРВОМУ подошедшему, и путь
       с параметром съедает литеральный, объявленный ниже. Ровно так умер
       экран отложенных рассылок: `GET /scheduled` стоял под
       `GET /{broadcast_id}` и отвечал 422. Проверка живым запросом, а не
       разбором app.routes, — по тем же причинам, что расписаны в
       tests/services/test_broadcasts_route_split.py.

    3. Гард админа. Маршруты сводки отдают выручку и телефоны воронки;
       открытыми наружу им быть нельзя.

    4. Секреты. В last_error рассылки и в details аудита попадает текст
       исключения, а туда — URL метода Telegram с токеном бота. Токен
       обязан быть замаскирован до того, как строка уйдёт в браузер.
"""
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from app.api.dashboard.deps import require_admin
from database.dashboard_summary import _scrub_secrets

ROUTES = [
    ("GET", "/summary/money"),
    ("GET", "/summary/business"),
    ("GET", "/summary/attention"),
    ("GET", "/summary/events"),
]


@pytest.fixture(scope="module")
def app():
    from app.api.dashboard.routes import summary

    application = FastAPI()
    application.include_router(summary.router, prefix="/summary")
    return application


@pytest.fixture()
def client(app):
    app.dependency_overrides[require_admin] = lambda: {"sub": "1"}
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def db(monkeypatch):
    """Заглушки слоя базы. Маршруты зовут их как database.<имя>."""
    monkeypatch.setattr(
        database, "get_revenue_today_vs_yesterday",
        AsyncMock(return_value={
            "tz": "Europe/Moscow",
            "elapsed_minutes": 671,
            "today_rubles": 1234.0,
            "today_payments": 7,
            "yesterday_same_time_rubles": 980.0,
            "yesterday_same_time_payments": 5,
            "sparkline": [{"date": "2026-08-05", "rubles": 1234.0}],
        }),
    )
    monkeypatch.setattr(
        database, "get_summary_subscription_counts",
        AsyncMock(return_value={"active": 412, "expiring_7d": 37}),
    )
    monkeypatch.setattr(
        database, "get_failed_payments_count", AsyncMock(return_value=3),
    )
    monkeypatch.setattr(
        database, "find_over_issuance_candidates", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        database, "get_stuck_payments", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        database, "get_failed_broadcasts", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        database, "get_summary_events", AsyncMock(return_value=[]),
    )
    return database


# ── 1. Порядок и доступность маршрутов ────────────────────────────────

@pytest.mark.parametrize("method,path", ROUTES)
def test_route_is_behind_the_admin_guard(app, method, path):
    """Без токена — 401, и до валидации параметров."""
    r = TestClient(app).request(method, path)
    assert r.status_code == 401, (
        f"{method} {path} ответил {r.status_code} вместо 401 — маршрут "
        f"вне админской проверки"
    )


@pytest.mark.parametrize("method,path", ROUTES)
def test_literal_route_is_not_swallowed_by_a_parametrized_one(client, db, method, path):
    """Слово в пути не должно уезжать в параметр соседнего маршрута.

    Сейчас путей с параметром в сводке нет вовсе, и тест зелёный
    тривиально. Он стоит здесь на будущее: в тот день, когда кто-то
    добавит `GET /{item_id}` выше остальных, экран молча начнёт отвечать
    422, и узнают об этом от владельца, а не из логов.
    """
    r = client.request(method, path)
    if r.status_code != 422:
        return
    stolen = [
        d.get("loc") for d in r.json().get("detail", [])
        if d.get("loc") and d["loc"][0] == "path"
    ]
    assert not stolen, (
        f"{method} {path} перехвачен маршрутом с параметром в пути: {stolen}. "
        f"Литеральные пути объявляются раньше параметрических."
    )


def test_subscriptions_list_is_not_swallowed_by_the_user_card_route():
    """`GET /users/subscriptions` не должен уезжать в `/{telegram_id}`.

    Плитки зоны B ведут сюда. Стоит объявить маршрут ниже карточки
    пользователя — и слово «subscriptions» пойдёт в параметр-число, экран
    ответит 422, а в логах не будет ничего: 422 на живом маршруте,
    который никто не звал.
    """
    from unittest.mock import AsyncMock

    from app.api.dashboard.routes import users as users_routes

    application = FastAPI()
    application.include_router(users_routes.router, prefix="/users")
    application.dependency_overrides[require_admin] = lambda: {"sub": "1"}
    original = getattr(database, "list_paid_subscriptions", None)
    database.list_paid_subscriptions = AsyncMock(return_value=[])
    try:
        r = TestClient(application, raise_server_exceptions=False).get(
            "/users/subscriptions?filter=expiring_7d",
        )
        assert r.status_code == 200, (
            f"список подписок перехвачен карточкой пользователя: {r.text}"
        )
        assert r.json()["filter"] == "expiring_7d"
    finally:
        if original is not None:
            database.list_paid_subscriptions = original
        application.dependency_overrides.clear()


def test_all_four_zones_are_registered(app):
    """Зон на экране четыре, маршрутов тоже четыре — по одному на зону."""
    paths = set(app.openapi()["paths"])
    assert paths == {
        "/summary/money", "/summary/business",
        "/summary/attention", "/summary/events",
    }


# ── 2. Ошибка ≠ ноль ──────────────────────────────────────────────────

def test_money_failure_is_an_error_not_a_zero(client, db, monkeypatch):
    """Зона A — одно число. Частично его показать нечем, поэтому 500.

    Ноль здесь был бы враньём: «сегодня заработали 0 ₽» и «не смогли
    посчитать» человек обязан различать.
    """
    monkeypatch.setattr(
        database, "get_revenue_today_vs_yesterday",
        AsyncMock(side_effect=RuntimeError("боль")),
    )
    r = client.get("/summary/money")
    assert r.status_code == 500, (
        "деньги отдали 200 — значит, где-то по дороге появился ноль "
        "вместо отказа"
    )
    assert "today_rubles" not in r.text


def test_business_metric_failure_does_not_zero_the_others(client, db, monkeypatch):
    """Упавшее число приходит как value=null с пометкой, соседние — живут."""
    monkeypatch.setattr(
        database, "get_failed_payments_count",
        AsyncMock(side_effect=RuntimeError("нет таблицы")),
    )
    r = client.get("/summary/business")
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]

    assert m["failed_payments_24h"]["value"] is None, (
        "упавшее число прикинулось нулём — это тот самый дефект, "
        "из-за которого переделывали экран"
    )
    assert m["failed_payments_24h"]["error"]
    assert m["active_subscriptions"]["value"] == 412
    assert m["expiring_7d"]["value"] == 37


def test_business_has_exactly_four_numbers(client, db):
    """Ровно четыре, не больше: решение владельца, research §9.3."""
    m = client.get("/summary/business").json()["metrics"]
    assert set(m) == {
        "active_subscriptions", "expiring_7d",
        "failed_payments_24h", "panel_mismatches",
    }


def test_unreachable_panel_is_not_reported_as_zero_mismatches(client, db, monkeypatch):
    """Недоступная панель — не «расхождений нет».

    Сверка отдаёт строку-маркер panel_unreachable вместо пустого списка
    (database/reconciliation_candidates.py). Если маршрут посчитает её
    обычной строкой, получится «одно расхождение»; если проглотит —
    «расхождений нет». Оба варианта врут.
    """
    monkeypatch.setattr(
        database, "find_over_issuance_candidates",
        AsyncMock(return_value=[{"telegram_id": 0, "panel_unreachable": True}]),
    )
    m = client.get("/summary/business").json()["metrics"]
    assert m["panel_mismatches"]["value"] is None
    assert m["panel_mismatches"]["error"] == "panel_unreachable"


def test_attention_tells_no_problems_apart_from_could_not_check(client, db, monkeypatch):
    """Зона C: пустой список при живых источниках и при упавшем — разное.

    Без sources оба случая выглядели бы одинаково — пустым списком, — и
    «всё чисто» рисовалось бы поверх неработающей проверки.
    """
    ok = client.get("/summary/attention").json()
    assert ok["items"] == []
    assert ok["sources"] == {"payments": "ok", "panel": "ok", "broadcasts": "ok"}

    monkeypatch.setattr(
        database, "get_stuck_payments", AsyncMock(side_effect=RuntimeError("бум")),
    )
    broken = client.get("/summary/attention").json()
    assert broken["items"] == []
    assert broken["sources"]["payments"] == "error"
    assert broken["sources"]["panel"] == "ok"


def test_attention_never_returns_more_than_ten(client, db, monkeypatch):
    """0–10 объектов. Список, который не помещается на экран, не читают."""
    monkeypatch.setattr(
        database, "get_stuck_payments",
        AsyncMock(return_value=[
            {
                "payment_id": i, "telegram_id": 100 + i, "username": None,
                "amount_rubles": 199.0, "tariff": "plus_30",
                "provider": "platega", "at": "2026-08-05T09:00:00+00:00",
            }
            for i in range(10)
        ]),
    )
    monkeypatch.setattr(
        database, "get_failed_broadcasts",
        AsyncMock(return_value=[{
            "broadcast_id": 7, "title": "Акция", "total": 100,
            "failed": 90, "error": None, "at": "2026-08-05T08:00:00+00:00",
        }]),
    )
    body = client.get("/summary/attention").json()
    assert len(body["items"]) == 10
    assert body["truncated"] is True


# ── 3. Секреты ────────────────────────────────────────────────────────

def test_bot_token_is_masked_before_it_reaches_the_browser():
    """Токен из текста исключения не уходит наружу.

    aiogram кладёт в сообщение об ошибке URL метода целиком, вместе с
    токеном. Эта строка попадает в scheduled_broadcasts.last_error, а
    оттуда — прямо на экран сводки.
    """
    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    raw = f"Telegram server says: https://api.telegram.org/bot{token}/sendMessage 401"
    out = _scrub_secrets(raw)
    assert token not in out
    assert "sendMessage" in out, "вычистили заодно и полезный текст"


def test_scrub_shortens_long_errors():
    """Длинный трейсбэк в карточке — это стена текста вместо сути."""
    out = _scrub_secrets("э" * 5000)
    assert len(out) <= 200


# ── 4. Определение выручки ────────────────────────────────────────────

def test_money_query_counts_only_external_income():
    """Выручка сводки считается по тому же правилу, что и весь проект.

    Забыть фильтр — не ошибка, а тихое удвоение: пополнение баланса,
    покупка с него и автопродление посчитаются тремя приходами вместо
    одного. Главное число экрана вырастет, и заметят это не сразу.
    """
    src = Path("database/analytics_revenue.py").read_text(encoding="utf-8")
    start = src.index("async def get_revenue_today_vs_yesterday")
    end = src.index("async def get_payments_breakdown")
    body = src[start:end]

    paid_lines = [ln for ln in body.splitlines() if "status = 'paid'" in ln]
    assert paid_lines, "запрос перестал ходить в pending_purchases"
    for ln in paid_lines:
        assert "COALESCE(payment_provider, '') <> 'balance'" in ln, (
            f"строка без фильтра выручки: {ln.strip()}"
        )
    assert "Europe/Moscow" in body, (
        "сутки режутся не по Москве — тайл и суточный график снова "
        "разойдутся на три часа"
    )


def test_event_feed_deliberately_has_no_revenue_filter():
    """В ленте событий фильтра выручки НЕТ, и это записано словами.

    Покупка с баланса — произошедшее событие, даже если в выручку она не
    входит. Тест сторожит не код, а объяснение: без него следующий
    читатель либо добавит фильтр в ленту, либо скопирует ленту в
    денежный запрос.
    """
    src = Path("database/dashboard_summary.py").read_text(encoding="utf-8")
    start = src.index("async def get_summary_events")
    body = src[start:]
    assert "REVENUE_EXTERNAL_ONLY_SQL" in body
    assert re.search(r"СОЗНАТЕЛЬНО\s+нет", body)


def test_promo_code_never_leaves_the_events_query():
    """У покупок Spotify в promo_code лежит пароль клиента."""
    src = Path("database/dashboard_summary.py").read_text(encoding="utf-8")
    start = src.index("async def get_summary_events")
    body = src[start:]
    assert "promo_code" not in body.split('"""', 2)[2], (
        "promo_code попал в выборку ленты событий"
    )


def test_combo_counts_as_its_own_product():
    """combo_basic / combo_plus — отдельные продукты, а не разновидность plus.

    Пропадут из списка — активных подписок станет меньше, ошибки при этом
    не будет: число просто станет другим.
    """
    from database.dashboard_summary import _PAID_SUBSCRIPTION_TYPES

    assert "combo_basic" in _PAID_SUBSCRIPTION_TYPES
    assert "combo_plus" in _PAID_SUBSCRIPTION_TYPES
    assert "basic" in _PAID_SUBSCRIPTION_TYPES
    assert "plus" in _PAID_SUBSCRIPTION_TYPES
