"""Разрезание роута рассылок ничего не потеряло и ничего не открыло.

app/api/dashboard/routes/broadcasts.py был на 1033 строки и держал в одном
файле пять вещей: каталог сегментов (180 строк текста), вёрстку кнопок,
загрузку файлов в Telegram, фоновую отправку и планировщик. Разрезан на
пакет read / segments_catalog / uploads / keyboard / send / scheduled /
common со сборкой в __init__.py.

ГЛАВНЫЕ РИСКИ ТАКОЙ ОПЕРАЦИИ

    1. Порядок маршрутов. FastAPI отдаёт запрос ПЕРВОМУ подошедшему
       маршруту, а `/{broadcast_id}` ловит любой односегментный путь.
       Переставив подроутеры, легко получить 422 на экране истории — и
       узнать об этом от админа, а не из логов.

    2. Гард админа. Он висит на роутере пакета, а эндпоинты живут в
       подроутерах. Если зависимость перестанет доезжать до вложенных
       маршрутов, дашборд рассылок окажется открытым наружу — молча.

    3. POST на пустой путь (создание рассылки). FastAPI запрещает
       подключать подроутер с пустыми префиксом и путём, поэтому маршрут
       вешается прямо в __init__.py. Забыть его = кнопка «Отправить» в
       дашборде отвечает 405, хотя функция на месте.
"""
import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PKG = Path("app/api/dashboard/routes/broadcasts")

# Снято с роутера ДО разрезания: метод, путь, имя обработчика — по порядку.
ROUTES_BEFORE = [
    ("GET", "/broadcasts/recent", "broadcasts_recent"),
    ("GET", "/broadcasts/segments", "segments_list"),
    ("GET", "/broadcasts/{broadcast_id}", "broadcast_detail"),
    ("GET", "/broadcasts/{broadcast_id}/stats", "broadcast_stats"),
    ("GET", "/broadcasts/{broadcast_id}/analytics", "broadcast_analytics"),
    ("POST", "/broadcasts/upload-photo", "upload_photo"),
    ("POST", "/broadcasts/upload-animation", "upload_animation"),
    ("POST", "/broadcasts/{broadcast_id}/delete-from-users",
     "broadcast_delete_from_users"),
    ("POST", "/broadcasts/{broadcast_id}/delete-from-users/cancel",
     "broadcast_delete_cancel"),
    ("POST", "/broadcasts/test-self", "broadcast_test_self"),
    ("POST", "/broadcasts", "broadcast_create"),
    ("POST", "/broadcasts/schedule", "broadcast_schedule_create"),
    ("GET", "/broadcasts/scheduled", "broadcast_schedule_list"),
    ("GET", "/broadcasts/scheduled/{sched_id}", "broadcast_schedule_get"),
    ("DELETE", "/broadcasts/scheduled/{sched_id}", "broadcast_schedule_cancel"),
]

MODULES = [
    "common.py",
    "keyboard.py",
    "read.py",
    "scheduled.py",
    "segments_catalog.py",
    "send.py",
    "uploads.py",
]


@pytest.fixture(scope="module")
def app():
    from app.api.dashboard.routes import broadcasts

    application = FastAPI()
    application.include_router(broadcasts.router, prefix="/broadcasts")
    return application


@pytest.mark.parametrize("module", MODULES)
def test_module_exists_with_a_docstring(module):
    path = PKG / module
    assert path.exists(), f"{module} потерян"
    assert ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))), (
        f"{module} без докстринга: непонятно, что здесь и почему выделено"
    )


def test_old_single_file_module_is_gone():
    """Файл разрезан целиком: остаться копией он не может — иначе два
    объявления одних и тех же путей."""
    assert not Path("app/api/dashboard/routes/broadcasts.py").exists()


def test_route_table_did_not_change(app):
    """Тот же набор маршрутов В ТОМ ЖЕ ПОРЯДКЕ, что и до разрезания."""
    spec = app.openapi()
    # operationId у FastAPI = имя функции + путь + метод. Имя функции
    # сравниваем по префиксу, чтобы не завязываться на схему генерации id.
    actual = [
        (method.upper(), path, op["operationId"])
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
    ]
    assert len(actual) == len(ROUTES_BEFORE), (
        f"маршрутов было {len(ROUTES_BEFORE)}, стало {len(actual)}"
    )
    for (m_now, p_now, oid), (m_was, p_was, fn_was) in zip(actual, ROUTES_BEFORE):
        assert (m_now, p_now) == (m_was, p_was), (
            f"порядок или путь изменился: было {m_was} {p_was}, "
            f"стало {m_now} {p_now}"
        )
        assert oid.startswith(fn_was), (
            f"{m_was} {p_was} обслуживает не та функция: {oid}"
        )


def test_history_screen_is_not_swallowed_by_the_id_route(app):
    """`/recent` объявлен раньше `/{broadcast_id}` — и должен им остаться.

    Стоит подроутеру read уехать ниже — и экран истории начнёт отвечать
    422 «broadcast_id не число».
    """
    from unittest.mock import AsyncMock

    import database
    from app.api.dashboard.deps import require_admin

    app.dependency_overrides[require_admin] = lambda: {"sub": "1"}
    original = database.get_recent_broadcasts
    database.get_recent_broadcasts = AsyncMock(return_value=[{"id": 1}])
    try:
        r = TestClient(app).get("/broadcasts/recent")
        assert r.status_code == 200, r.text
        assert r.json() == [{"id": 1}]
    finally:
        database.get_recent_broadcasts = original
        app.dependency_overrides.clear()


@pytest.mark.parametrize("method,path", [
    ("get", "/broadcasts/recent"),
    ("get", "/broadcasts/segments"),
    ("get", "/broadcasts/5"),
    ("get", "/broadcasts/5/stats"),
    ("get", "/broadcasts/5/analytics"),
    ("post", "/broadcasts/upload-photo"),
    ("post", "/broadcasts/upload-animation"),
    ("post", "/broadcasts/5/delete-from-users"),
    ("post", "/broadcasts/5/delete-from-users/cancel"),
    ("post", "/broadcasts/test-self"),
    ("post", "/broadcasts"),
    ("post", "/broadcasts/schedule"),
    ("get", "/broadcasts/scheduled"),
    ("get", "/broadcasts/scheduled/1"),
    ("delete", "/broadcasts/scheduled/1"),
])
def test_every_route_stays_behind_the_admin_guard(app, method, path):
    """Гард объявлен на роутере пакета, эндпоинты — в подроутерах.

    Проверяем на живом приложении, а не на вере в документацию FastAPI:
    открытый наружу дашборд рассылок = чужой человек шлёт сообщения всей
    базе. Без токена обязано быть 401 ДО валидации тела запроса.
    """
    r = getattr(TestClient(app), method)(path)
    assert r.status_code == 401, (
        f"{method.upper()} {path} ответил {r.status_code} вместо 401 — "
        f"маршрут вне админской проверки"
    )


def test_create_endpoint_is_registered_on_the_empty_path(app):
    """POST на сам /broadcasts. Живёт в __init__.py, потому что FastAPI не
    даёт подключить подроутер с пустыми префиксом и путём."""
    paths = app.openapi()["paths"]
    assert "/broadcasts" in paths and "post" in paths["/broadcasts"]


def test_reexports_survived():
    """На эти имена ссылались, когда всё лежало одним файлом."""
    from app.api.dashboard.routes import broadcasts

    for name in (
        "router",
        "normalize_premium_emoji",
        "_build_reply_markup",
        "_BUTTON_TYPES",
        "_MD_TG_EMOJI_RE",
        "_get_bot",
        "_serialize",
        "SEGMENTS",
        "BroadcastCreateRequest",
        "ScheduleBroadcastRequest",
        "_parse_msk",
    ):
        assert hasattr(broadcasts, name), f"потерян реэкспорт: {name}"


def test_segment_catalog_is_data_only():
    """В каталоге сегментов не должно появляться логики: это текст для UI."""
    tree = ast.parse((PKG / "segments_catalog.py").read_text(encoding="utf-8"))
    funcs = [
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert not funcs, f"в каталоге завелась логика: {funcs}"


def test_segment_keys_are_unique():
    """Дубль ключа тихо удваивает строку в визарде и лишний раз считает
    аудиторию — по запросу в базу на каждый дубль."""
    from app.api.dashboard.routes.broadcasts.segments_catalog import SEGMENTS

    keys = [row[0] for row in SEGMENTS]
    assert len(keys) == len(set(keys)), "дубли ключей сегментов"
    assert len(keys) >= 50, "каталог сегментов усох при переносе"
