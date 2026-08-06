"""Экраны «Пользователи» и «Платежи»: маршруты и честность ответов.

ЧТО ЗДЕСЬ ЗАЩИЩЕНО

    1. Порядок маршрутов. FastAPI отдаёт запрос ПЕРВОМУ подошедшему, и
       путь с параметром съедает литеральный, объявленный ниже. Так в этом
       проекте умер экран отложенных рассылок: `GET /scheduled` стоял под
       `GET /{broadcast_id}` и отвечал 422, а в логах не было ничего.
       Здесь под тем же риском два новых пути: `/users/list` рядом с
       `/users/{telegram_id}` и `/payments/purchases/{id}` рядом с
       `/payments/{payment_id}`.

    2. Ошибка не превращается в пустоту. Упавший запрос обязан отдать 500,
       а не `{"items": []}`: пустая таблица на этих экранах читается как
       «пользователей нет» и «оплат не было». Ровно из-за такого поведения
       переделывался весь дашборд.

    3. Число на плитке сводки и длина списка совпадают. Отборы `active` и
       `expiring_7d` обязаны ходить в database.list_paid_subscriptions —
       ту же функцию, по которой сводка считает своё число. Соберёт
       маршрут список своим запросом — плитка покажет одно, список другое.

    4. Гард админа: карточки, балансы и платежи наружу не отдаются.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from app.api.dashboard.deps import require_admin


@pytest.fixture()
def app():
    from app.api.dashboard.routes import payments as payments_routes
    from app.api.dashboard.routes import users as users_routes

    application = FastAPI()
    application.include_router(users_routes.router, prefix="/users")
    application.include_router(payments_routes.router, prefix="/payments")
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
    """Заглушки слоя базы: маршруты зовут их как database.<имя>."""
    monkeypatch.setattr(
        database, "list_users_dashboard",
        AsyncMock(return_value={
            "items": [{
                "telegram_id": 42,
                "username": "ivanov",
                "language": "ru",
                "created_at": "2026-01-01T00:00:00+00:00",
                "balance_rubles": 120.5,
                "is_vip": True,
                "subscription_type": "combo_plus",
                "is_combo": False,
                "is_bypass_only": False,
                "source": "payment",
                "status": "active",
                "auto_renew": True,
                "expires_at": "2026-09-01T00:00:00+00:00",
                "activated_at": None,
                "has_active_sub": True,
            }],
            "total": 1,
        }),
    )
    monkeypatch.setattr(
        database, "list_paid_subscriptions",
        AsyncMock(return_value=[{
            "telegram_id": 42,
            "username": "ivanov",
            "subscription_type": "plus",
            "is_combo": True,
            "source": "payment",
            "auto_renew": False,
            "expires_at": "2026-09-01T00:00:00+00:00",
            "activated_at": None,
        }]),
    )
    monkeypatch.setattr(
        database, "get_balances_bulk", AsyncMock(return_value={42: 7.0}),
    )
    monkeypatch.setattr(
        database, "get_purchase_detail",
        AsyncMock(return_value={
            "purchase": {"id": 7, "status": "paid"},
            "user": {"telegram_id": 42, "username": "ivanov", "balance_rubles": 0.0},
            "subscription": None,
            "errors": [],
        }),
    )
    return database


# ── 1. Порядок маршрутов ──────────────────────────────────────────────

def test_users_list_is_not_swallowed_by_the_user_card_route(client, db):
    """Слово «list» не должно уезжать в параметр-число."""
    r = client.get("/users/list?filter=all")
    assert r.status_code == 200, (
        f"список пользователей перехвачен карточкой /users/{{telegram_id}}: {r.text}"
    )
    assert r.json()["filter"] == "all"


def test_purchase_detail_is_not_swallowed_by_the_legacy_payment_route(client, db):
    r = client.get("/payments/purchases/7")
    assert r.status_code == 200, (
        f"разбор покупки перехвачен маршрутом /payments/{{payment_id}}: {r.text}"
    )
    assert r.json()["purchase"]["id"] == 7


def test_purchase_detail_says_not_found_instead_of_empty_object(client, db, monkeypatch):
    """404, а не пустой объект: «покупка без данных» — не состояние."""
    monkeypatch.setattr(database, "get_purchase_detail", AsyncMock(return_value=None))
    r = client.get("/payments/purchases/999999")
    assert r.status_code == 404


# ── 2. Ошибка не превращается в пустоту ───────────────────────────────

def test_users_list_fails_loudly(client, db, monkeypatch):
    monkeypatch.setattr(
        database, "list_users_dashboard", AsyncMock(side_effect=RuntimeError("db down")),
    )
    r = client.get("/users/list?filter=all")
    assert r.status_code == 500, (
        "отказ базы вернул 200 — на экране это станет пустой таблицей, "
        "то есть «пользователей нет»"
    )
    assert "items" not in r.json()


def test_payments_feed_fails_loudly(client, db, monkeypatch):
    monkeypatch.setattr(
        database, "get_recent_payments_feed", AsyncMock(side_effect=RuntimeError("db down")),
    )
    r = client.get("/payments/recent?hours=24")
    assert r.status_code == 500, (
        "отказ базы вернул 200 с пустым списком — это читается как "
        "«сегодня никто не платил»"
    )


# ── 3. Список совпадает с числом на плитке сводки ─────────────────────

@pytest.mark.parametrize("kind", ["active", "expiring_7d"])
def test_filtered_list_uses_the_same_source_as_the_summary_tile(client, db, kind):
    """Отбор берётся у dashboard_summary, а не собирается заново.

    Соберёт маршрут свой запрос — плитка покажет 412, список отдаст 380,
    и понять, какое число верное, будет нечем.
    """
    r = client.get(f"/users/list?filter={kind}")
    assert r.status_code == 200, r.text
    database.list_paid_subscriptions.assert_awaited()
    assert database.list_paid_subscriptions.await_args.args[0] == kind
    assert r.json()["total"] == 1


def test_filtered_list_enriches_rows_with_balance_and_tariff_name(client, db):
    """Строка отбора приходит без баланса и с техническим именем тарифа.

    Баланс дописывается пачкой (одним запросом на страницу), название
    тарифа считает справочник: в базе комбо хранится двумя способами, и
    интерфейс не должен знать про эту двойственность.
    """
    r = client.get("/users/list?filter=active")
    row = r.json()["items"][0]
    assert row["balance_rubles"] == 7.0
    assert row["tariff_display"] == "Комбо Плюс"
    assert row["is_combo"] is True


def test_query_narrows_the_filtered_list_but_never_widens_it(client, db):
    """Поиск по отобранным строкам: сузить можно, расширить нельзя."""
    r = client.get("/users/list?filter=active&q=никого_такого_нет")
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 0


# ── 4. Гард админа ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/users/list?filter=all",
    "/payments/purchases/7",
    "/payments/recent",
    "/payments/errors",
])
def test_routes_are_behind_the_admin_guard(app, path):
    r = TestClient(app).get(path)
    assert r.status_code == 401, (
        f"{path} ответил {r.status_code} вместо 401 — маршрут вне админской проверки"
    )
