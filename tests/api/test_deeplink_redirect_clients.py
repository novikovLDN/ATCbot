"""/open/{client} redirect page: import schemes for Karing, Stash, Clash Verge,
v2RayTun (plus Happ) and rejection of bad subscription URLs.

Schemes (official docs):
  karing://install-config?url=<enc>&name=<enc>  https://karing.app/en/cooperation/scheme
  stash://install-config?url=<enc>              https://stash.wiki/en/faq/url-schema
  clash://install-config?url=<enc>              https://www.clashverge.dev/guide/url_schemes.html
  v2raytun://import/<subscription_link>         https://docs.v2raytun.com/deep-link
"""
import json
import re
from html import unescape
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deeplink_redirect

SUB = "https://sub.atlassecure.ru/api/sub/AbC123_x-y?a=1&b=2"


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(deeplink_redirect.router)
    return TestClient(app)


def _deep_link(html: str) -> str:
    """The JS redirect target (json-encoded string literal in the page)."""
    m = re.search(r"window\.location\.href = (\".*?\");", html)
    assert m, "no redirect in page"
    return json.loads(m.group(1))


def _get(client, app_name, **params):
    return client.get(f"/open/{app_name}", params=params)


def test_karing_scheme_encodes_url_and_name(client):
    r = _get(client, "karing", url=SUB, name="Atlas Secure Обход")
    assert r.status_code == 200
    link = _deep_link(r.text)
    assert link == (
        "karing://install-config?url=" + quote(SUB, safe="")
        + "&name=" + quote("Atlas Secure Обход", safe="")
    )
    # A client parsing the query gets the subscription URL back intact,
    # the & inside it did not leak into Karing's own parameters.
    qs = parse_qs(urlsplit(link).query)
    assert qs == {"url": [SUB], "name": ["Atlas Secure Обход"]}
    # The visible button href is HTML-escaped but points at the same link.
    href = re.search(r'id="open" href="([^"]+)"', r.text).group(1)
    assert unescape(href) == link


def test_karing_default_name_and_name_sanitised(client):
    link = _deep_link(_get(client, "karing", url=SUB).text)
    assert parse_qs(urlsplit(link).query)["name"] == ["Atlas Secure"]

    link = _deep_link(_get(client, "karing", url=SUB, name="x\x00\n" + "y" * 100).text)
    name = parse_qs(urlsplit(link).query)["name"][0]
    assert name == ("x" + "y" * 100)[:64]


@pytest.mark.parametrize("name,prefix", [
    ("stash", "stash://install-config?url="),
    ("clash", "clash://install-config?url="),
    ("v2raytun", "v2raytun://import/"),
])
def test_other_client_schemes(client, name, prefix):
    r = _get(client, name, url=SUB)
    assert r.status_code == 200
    assert _deep_link(r.text) == prefix + quote(SUB, safe="")


def test_happ_still_works(client, monkeypatch):
    from app.services import happ_crypto
    monkeypatch.setattr(happ_crypto, "to_crypt_link", lambda u: "happ://crypt4/SEALED")
    r = _get(client, "happ", url=SUB)
    assert r.status_code == 200
    assert _deep_link(r.text) == "happ://crypt4/SEALED"


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "ftp://sub.atlassecure.ru/x",
    "karing://install-config?url=x",
    "/api/sub/relative",
    "https://",
    "https://sub.atlassecure.ru/a b",
    "https://sub.atlassecure.ru/a\nb",
    "https://sub.atlassecure.ru/\"><script>",
    "https://sub.atlassecure.ru/" + "a" * 3000,
    "",
])
@pytest.mark.parametrize("name", ["karing", "stash", "clash", "v2raytun", "happ", "incy"])
def test_bad_urls_rejected(client, name, bad):
    r = _get(client, name, url=bad)
    assert r.status_code == 400
    assert "://install-config" not in r.text
    assert "window.location" not in r.text


def test_unknown_client_and_missing_url(client):
    assert _get(client, "shadowrocket", url=SUB).status_code == 400
    assert client.get("/open/karing").status_code == 422


def test_page_escapes_client_name_and_link(client):
    r = _get(client, "clash", url=SUB)
    assert "Clash Verge" in r.text
    # the raw & of the query never appears unescaped inside HTML attributes
    href = re.search(r'id="open" href="([^"]+)"', r.text).group(1)
    assert "&amp;" in href or "&" not in unescape(href)
