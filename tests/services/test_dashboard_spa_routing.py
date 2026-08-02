"""Отдача собранного дашборда: прямые ссылки работают, наружу ничего не течёт.

Дефект-1 (UX). SPA монтировался как StaticFiles(html=True). Такой mount
отдаёт index.html только для каталогов, а для отсутствующего файла ищет
404.html и, не найдя, поднимает 404. Роутер дашборда — BrowserRouter с
реальными путями (/dashboard/users, /dashboard/payments). Переходы кнопками
работали, но перезагрузка страницы и ссылка, присланная в мессенджере,
давали 404 — открыть раздел по ссылке было нельзя.

Дефект-2 (безопасность). Первая версия замены проверяла путь через
normpath + startswith(dist). Две дыры: normpath чистит «..» только
лексически и не знает о симлинках, а голый startswith пропускает соседний
каталог dist-backup — его имя тоже начинается с «dist». Проверка переписана
на realpath + сравнение с разделителем и живёт в app/api/spa_files.py.

Здесь тестируется настоящая функция, а не её копия.
"""
import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.api.spa_files import safe_asset_path


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    """Каталог сборки + сосед dist-backup + секрет рядом с ними."""
    root = tmp_path_factory.mktemp("spa")
    d = root / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    sibling = root / "dist-backup"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("НЕ ОТДАВАТЬ", encoding="utf-8")

    (root / "secret.env").write_text("TOKEN=НЕ ОТДАВАТЬ", encoding="utf-8")
    return os.path.realpath(str(d))


@pytest.fixture(scope="module")
def client(dist):
    app = FastAPI()
    index = os.path.join(dist, "index.html")

    @app.get("/dashboard/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/dashboard", include_in_schema=False)
    @app.get("/dashboard/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str = ""):
        asset = safe_asset_path(dist, spa_path)
        return FileResponse(asset or index)

    return TestClient(app)


# ── Поведение: приложение должно открываться по любой ссылке ──────────

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


# ── Безопасность: наружу из dist не отдаётся ничего ───────────────────

@pytest.mark.parametrize("attack", [
    "../secret.env",
    "../../etc/passwd",
    "assets/../../secret.env",
    "./../secret.env",
    "..%2fsecret.env",          # уже раскодированный вариант доедет как ../
    "/etc/passwd",              # абсолютный путь
    "/../secret.env",
])
def test_traversal_is_refused(dist, attack):
    assert safe_asset_path(dist, attack) is None, (
        f"путь {attack!r} прошёл проверку — файл вне dist отдаётся наружу"
    )


def test_sibling_directory_with_same_prefix_is_refused(dist):
    """Голый startswith(dist) пропускал бы dist-backup: имя начинается так же."""
    assert safe_asset_path(dist, "../dist-backup/secret.txt") is None


def test_symlink_out_of_dist_is_refused(dist):
    """normpath не знает о симлинках: ссылка внутри dist уводила наружу."""
    target = os.path.join(os.path.dirname(dist), "secret.env")
    link = os.path.join(dist, "escape.txt")
    if not os.path.exists(link):
        os.symlink(target, link)
    assert safe_asset_path(dist, "escape.txt") is None


def test_http_traversal_falls_back_to_index(client):
    """Через HTTP отказ не должен превращаться в 500 или в чужой файл:
    отдаём приложение, как и на любой неизвестный путь."""
    r = client.get("/dashboard/..%2f..%2fsecret.env")
    assert r.status_code in (200, 404)
    assert "НЕ ОТДАВАТЬ" not in r.text
    assert "TOKEN=" not in r.text


def test_legitimate_paths_still_resolve(dist):
    """Проверка не должна быть настолько строгой, чтобы сломать ассеты."""
    assert safe_asset_path(dist, "assets/app.js") is not None
    assert safe_asset_path(dist, "index.html") is not None
    assert safe_asset_path(dist, "") is None          # корень → index.html
    assert safe_asset_path(dist, "no-such-file") is None
