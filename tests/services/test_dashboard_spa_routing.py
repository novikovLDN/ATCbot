"""Прямая ссылка и F5 на любом разделе дашборда должны открывать приложение.

Дефект: SPA монтировался как StaticFiles(html=True). Такой mount отдаёт
index.html только для каталогов, а для отсутствующего файла ищет 404.html и,
не найдя, поднимает 404. Роутер дашборда — BrowserRouter с реальными путями
(/dashboard/users, /dashboard/payments, ...). Переходы кнопками работали, но
перезагрузка страницы или ссылка, присланная в мессенджере, давали 404.

Тест собирает ту же конструкцию маршрутов, что и app/api/__init__.py, на
временном каталоге: поднимать настоящее приложение бота в юнит-тестах нельзя
(нужны токены, БД, воркеры).
"""
import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    dist = tempfile.mkdtemp()
    with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>SPA</html>")
    os.makedirs(os.path.join(dist, "assets"), exist_ok=True)
    with open(os.path.join(dist, "assets", "app.js"), "w", encoding="utf-8") as f:
        f.write("console.log(1)")

    app = FastAPI()

    @app.get("/dashboard/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/dashboard", include_in_schema=False)
    @app.get("/dashboard/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str = ""):
        candidate = os.path.normpath(os.path.join(dist, spa_path))
        if spa_path and candidate.startswith(dist) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(dist, "index.html"))

    return TestClient(app)


def test_api_routes_win_over_spa(client):
    """API объявлен раньше — catch-all не должен его перехватывать."""
    r = client.get("/dashboard/api/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_deep_link_returns_spa(client):
    """Главный регресс: прямая ссылка на раздел вместо 404 отдаёт приложение."""
    r = client.get("/dashboard/users")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_nested_deep_link_returns_spa(client):
    r = client.get("/dashboard/users/12345/history")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_root_returns_spa(client):
    assert client.get("/dashboard").status_code == 200


def test_existing_asset_is_served_as_file(client):
    """Ассеты обязаны отдаваться собой, а не index.html — иначе браузер
    получит HTML вместо JS и приложение не запустится."""
    r = client.get("/dashboard/assets/app.js")
    assert r.status_code == 200
    assert r.text == "console.log(1)"


def test_path_traversal_is_refused(client):
    """Наружу из dist отдавать нечего."""
    r = client.get("/dashboard/../../etc/passwd")
    assert r.status_code == 404
