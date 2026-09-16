"""The dashboard answered 404 on every screen except «/dashboard/».

StaticFiles only knows files, so a client-side route — /dashboard/money,
/dashboard/health — had nothing to serve. The browser asks for one on
every reload of a screen, on a deep link, on a tap on a push notification
(the service worker navigates to data.url), and the installed PWA
relaunches on the route it was left on. All of them showed an empty
screen.

The same mount sent no caching headers either, so a phone re-validated
every fingerprinted chunk on every load.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

from app.api.spa import IMMUTABLE, SpaStaticFiles  # noqa: E402

INDEX = "<!doctype html><title>Admin</title><div id=root></div>"
CHUNK = "console.log('chunk')"


@pytest.fixture
def client(tmp_path):
    """A built dashboard/dist, mounted the way production mounts it."""
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "sw.js").write_text("// sw")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-2hFOOvh5.js").write_text(CHUNK)

    app = FastAPI()
    app.mount("/dashboard", SpaStaticFiles(directory=str(tmp_path), html=True), name="spa")
    return TestClient(app)


@pytest.mark.parametrize("route", ["/dashboard/money", "/dashboard/health", "/dashboard/broadcasts/new"])
def test_client_route_serves_the_app(client, route):
    r = client.get(route)

    assert r.status_code == 200, f"{route} must open the dashboard, not 404"
    assert r.headers["content-type"].startswith("text/html")
    assert "id=root" in r.text


def test_root_and_files_still_work(client):
    assert client.get("/dashboard/").status_code == 200
    assert client.get("/dashboard/sw.js").text == "// sw"
    assert client.get("/dashboard/assets/index-2hFOOvh5.js").text == CHUNK


def test_a_missing_chunk_is_not_html(client):
    """A stale build asks for a chunk that no longer exists. Answering it
    with index.html would hand HTML to a <script> and hide the reason."""
    r = client.get("/dashboard/assets/index-oldhash.js")

    assert r.status_code == 404
    assert "id=root" not in r.text


def test_fingerprinted_assets_are_cached_forever(client):
    assert client.get("/dashboard/assets/index-2hFOOvh5.js").headers["cache-control"] == IMMUTABLE


def test_the_entry_document_is_never_cached(client):
    """index.html points at the hashed chunks: cache it and the browser
    keeps loading a build whose chunks are gone."""
    for path in ("/dashboard/", "/dashboard/money", "/dashboard/sw.js"):
        assert "no-cache" in client.get(path).headers["cache-control"], path


def test_responses_are_compressed():
    """284 KB of JS went over the wire uncompressed on every first load."""
    from app.api import app

    names = [m.cls.__name__ for m in app.user_middleware]
    assert "GZipMiddleware" in names, f"no compression in the stack: {names}"


def test_api_paths_are_not_swallowed_by_the_spa():
    """The SPA fallback must never answer for /dashboard/api/* — an
    unknown endpoint has to stay a JSON 404, not become the app shell."""
    from app.api import app

    r = TestClient(app).get("/dashboard/api/definitely-not-a-route")

    assert r.status_code == 404
    assert "id=root" not in r.text


def test_the_dashboard_is_mounted_with_the_spa_handler():
    """The class above only helps if production mounts it: a plain
    StaticFiles is what answered 404 on every screen."""
    from starlette.routing import Mount

    from app.api import app

    mount = next((r for r in app.routes if isinstance(r, Mount) and r.name == "dashboard-spa"), None)
    if mount is None:
        pytest.skip("dashboard/dist is not built in this environment")
    assert isinstance(mount.app, SpaStaticFiles), type(mount.app).__name__
