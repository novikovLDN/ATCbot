"""Экран «События»: маршруты журнала.

ЧТО ЗДЕСЬ ЗАЩИЩЕНО

    1. Отказ не превращается в пустоту. Прежний «Аудит» на упавшем
       запросе рисовал «Журнал пуст» — то есть «ничего не происходило».
       На журнале доступов и денег это самая вредная неправда: она
       успокаивает. Маршрут обязан отдать 500, а не 200 с items: [].

    2. Счётчики по категориям едут рядом со списком и считаются БЕЗ
       фильтра по категории. Иначе выбранная категория обнуляла бы
       соседние, и это читалось бы как «других событий нет».

    3. Порядок маршрутов. FastAPI отдаёт запрос ПЕРВОМУ подошедшему, и
       путь с параметром съедает литеральный, объявленный ниже. Ровно так
       умер экран отложенных рассылок: GET /scheduled стоял под
       GET /{broadcast_id} и отвечал 422.

    4. Гард админа: журнал показывает telegram_id и тексты действий.

    5. Секреты. В details записи аудита попадает текст исключения, а в
       него — URL метода Telegram с токеном бота. Токен обязан быть
       замаскирован до того, как строка уйдёт в браузер, — и в новой
       ленте, и в старом /audit/recent, который раньше отдавал строку из
       базы как есть.

    6. Одно правило «что событие, а что фон» на два экрана. admin_view_*
       и *_viewed пишутся при каждом открытии экрана в боте; две копии
       правила разошлись бы молча, и «Сводка» с «Событиями» показывали бы
       разное на одних данных.
"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from app.api.dashboard.deps import require_admin
from app.utils.security import scrub_secrets
from database.dashboard_events import CATEGORIES, NOISE_SQL

ROUTES = [
    ("GET", "/audit/events"),
    ("GET", "/audit/recent"),
]

EVENT = {
    "id": 12,
    "at": "2026-08-06T09:00:00+00:00",
    "action": "admin_revoke",
    "category": "access",
    "actor_id": 1,
    "actor_username": "owner",
    "target_id": 555,
    "target_username": None,
    "source": "admin",
    "result": None,
    "details": None,
}


@pytest.fixture(scope="module")
def app():
    from app.api.dashboard.routes import audit

    application = FastAPI()
    application.include_router(audit.router, prefix="/audit")
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
        database, "get_audit_events", AsyncMock(return_value=[dict(EVENT)]),
    )
    monkeypatch.setattr(
        database, "get_audit_category_counts",
        AsyncMock(return_value={
            "access": 3, "money": 2, "broadcast": 0, "users": 0, "other": 1,
        }),
    )
    monkeypatch.setattr(
        database, "get_last_audit_logs", AsyncMock(return_value=[]),
    )
    return database


# ── 1. Доступ и порядок маршрутов ─────────────────────────────────────

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
    """Слово в пути не должно уезжать в параметр соседнего маршрута."""
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


def test_bypass_fix_all_is_not_swallowed_by_fix_one():
    """POST /fix-all не должен уехать в /fix/{telegram_id}.

    Формы путей сейчас разные, и тест зелёный. Он стоит на будущее:
    появится /fix/all — и слово «all» пойдёт в параметр-число, экран
    ответит 422, а в логах не будет ничего.
    """
    from app.api.dashboard.routes import bypass_audit

    application = FastAPI()
    application.include_router(bypass_audit.router, prefix="/bypass-audit")
    application.dependency_overrides[require_admin] = lambda: {"sub": "1"}
    original = getattr(database, "get_bypass_overwrite_victims", None)
    database.get_bypass_overwrite_victims = AsyncMock(return_value=[])
    try:
        r = TestClient(application, raise_server_exceptions=False).post(
            "/bypass-audit/fix-all",
        )
        assert r.status_code == 200, f"fix-all перехвачен /fix/{{id}}: {r.text}"
        assert r.json()["total"] == 0
    finally:
        if original is not None:
            database.get_bypass_overwrite_victims = original
        application.dependency_overrides.clear()


# ── 2. Ошибка ≠ пустой журнал ─────────────────────────────────────────

def test_failure_is_an_error_not_an_empty_log(client, db, monkeypatch):
    """Пустая лента при отказе читается как «ничего не происходило»."""
    monkeypatch.setattr(
        database, "get_audit_events", AsyncMock(side_effect=RuntimeError("боль")),
    )
    r = client.get("/audit/events")
    assert r.status_code == 500, (
        "журнал отдал 200 — значит, где-то по дороге отказ превратился "
        "в пустой список"
    )
    assert '"items"' not in r.text


def test_counts_failure_is_an_error_too(client, db, monkeypatch):
    """Счётчики — часть ответа. Без них фильтр врёт молча."""
    monkeypatch.setattr(
        database, "get_audit_category_counts",
        AsyncMock(side_effect=RuntimeError("нет таблицы")),
    )
    assert client.get("/audit/events").status_code == 500


def test_empty_log_is_a_normal_two_hundred(client, db, monkeypatch):
    """Журнал действительно пуст — это 200 и пустой список, не ошибка."""
    monkeypatch.setattr(database, "get_audit_events", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        database, "get_audit_category_counts",
        AsyncMock(return_value={c: 0 for c in CATEGORIES}),
    )
    body = client.get("/audit/events").json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False


# ── 3. Счётчики ───────────────────────────────────────────────────────

def test_counts_cover_every_category(client, db):
    """Категория без записей приходит нулём явно.

    Отсутствие ключа фронту пришлось бы трактовать самому, а «ноль» и «не
    считали» — разные вещи.
    """
    counts = client.get("/audit/events").json()["counts"]
    assert set(counts) == set(CATEGORIES)


def test_counts_ignore_the_category_filter(client, db):
    """Выбранная категория не обнуляет соседние.

    Иначе на фильтре «Деньги» остальные показали бы ноль, и человек
    решил бы, что других событий нет вовсе.
    """
    body = client.get("/audit/events?category=access").json()
    assert body["counts"]["money"] == 2, (
        "счётчики посчитаны с фильтром по категории — соседние обнулились"
    )
    # total при этом сужается до выбранного, иначе «показано N из M» врёт.
    assert body["total"] == 3


def test_unknown_category_does_not_silently_show_everything(client, db):
    """Чужое значение в фильтре не должно молча снимать фильтр."""
    body = client.get("/audit/events?category=access&category=выдумка").json()
    assert body["total"] == 3


def test_person_filter_reaches_the_query(client, db):
    """Фильтр по человеку ищет и автора, и адресата — одним параметром."""
    client.get("/audit/events?who=555")
    kwargs = database.get_audit_events.await_args.kwargs
    assert kwargs["who"] == 555
    assert database.get_audit_category_counts.await_args.kwargs["who"] == 555


def test_zero_hours_means_all_time(client, db):
    """hours=0 — «за всё время», а не «за ноль часов»."""
    client.get("/audit/events?hours=0")
    assert database.get_audit_events.await_args.kwargs["hours"] is None


# ── 4. Секреты ────────────────────────────────────────────────────────

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
RAW = f"Telegram says: https://api.telegram.org/bot{TOKEN}/sendMessage 401"


def test_bot_token_is_masked_in_the_event_details():
    """details пишется из текста исключения, а туда попадает токен."""
    out = scrub_secrets(RAW)
    assert TOKEN not in out
    assert "sendMessage" in out, "вычистили заодно и полезный текст"


def test_legacy_recent_scrubs_details_too(client, db, monkeypatch):
    """Старый /audit/recent отдавал строку из базы как есть."""
    monkeypatch.setattr(
        database, "get_last_audit_logs",
        AsyncMock(return_value=[{"id": 1, "action": "x", "details": RAW}]),
    )
    r = client.get("/audit/recent")
    assert r.status_code == 200
    assert TOKEN not in r.text, "токен бота уехал в браузер через /audit/recent"


def test_details_leave_the_database_layer_scrubbed():
    """Маскировка стоит в слое базы, а не в маршруте.

    Уберут её оттуда — токен утечёт через любой новый вызов, а не только
    через тот, у которого есть тест.
    """
    src = Path("database/dashboard_events.py").read_text(encoding="utf-8")
    assert 'scrub_secrets(r["details"])' in src


# ── 5. Одно правило «событие или фон» на два экрана ───────────────────

def test_noise_rule_lives_in_one_place():
    """Ленту сводки и журнал фильтрует одна и та же константа."""
    assert "admin\\_view%" in NOISE_SQL
    assert "%\\_viewed" in NOISE_SQL

    summary = Path("database/dashboard_summary.py").read_text(encoding="utf-8")
    assert "from database.dashboard_events import NOISE_SQL" in summary
    assert "{NOISE_SQL}" in summary, (
        "лента сводки завела собственную копию правила — два экрана "
        "начнут показывать разное на одних и тех же данных"
    )
    assert "a.action NOT LIKE 'admin\\\\_view%'" not in summary, (
        "старая копия правила осталась в запросе сводки"
    )


def test_category_is_decided_in_sql_only():
    """Категорию считает CASE, по ней же фильтруют и группируют счётчики.

    Разложи действия по категориям ещё и в Python — фильтр, счётчики и
    список немедленно разъедутся, причём молча.
    """
    src = Path("database/dashboard_events.py").read_text(encoding="utf-8")
    # Ровно одна ветка «broadcast» на весь модуль — значит, CASE описан
    # единожды и вторая раскладка нигде не завелась.
    assert src.count("THEN 'broadcast'") == 1, (
        "категории раскладываются больше чем в одном месте"
    )
    # Обе функции ходят через общую выборку: объявление плюс два вызова.
    assert src.count("_FILTERED_CTE") == 3, (
        "список и счётчики считаются по разным выборкам — они разъедутся"
    )


# ── 6. DDL ────────────────────────────────────────────────────────────

def test_no_ddl_in_the_events_query():
    """Схему из обработчика и из отчётного запроса не правят."""
    src = Path("database/dashboard_events.py").read_text(encoding="utf-8").upper()
    for word in ("CREATE TABLE", "ALTER TABLE", "CREATE INDEX", "DROP TABLE"):
        assert word not in src, f"{word} в запросе журнала"
