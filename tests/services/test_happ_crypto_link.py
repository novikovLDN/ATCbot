"""
Unit tests for Task 4 — Happ Crypto Link wiring.

Coverage:
  * remnawave_api.encrypt_happ_crypto_link — happy path + None on
    panel failure / malformed response.
  * user_subscription_links.get_user_premium_happ_crypto_link —
    cache hit, cache-miss-then-encrypt-and-persist, encrypt failure
    returns None, REMNAWAVE_ENABLED=false short-circuit.
  * user_subscription_links.get_user_premium_displayable_url —
    prefers crypto link, falls back to plain when crypto unavailable.
  * migration_broadcast.render_for_user — displayable URL ends up in
    the <code> block, plain URL drives the 🔄 Обновить button.
"""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── remnawave_api.encrypt_happ_crypto_link ────────────────────────────

def _raw(status: int, *, response=None, body=None, ok=None):
    if ok is None:
        ok = status < 400
    return {"ok": ok, "status": status, "response": response, "body": body}


@pytest.fixture(autouse=True)
def _reset_endpoint_cache():
    from app.services import remnawave_api
    remnawave_api._reset_happ_crypto_endpoint_cache_for_tests()
    yield
    remnawave_api._reset_happ_crypto_endpoint_cache_for_tests()


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_link_on_documented_shape():
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value=_raw(200, response={"encryptedLink": "happ://crypto/abc123"}))
    with patch.object(remnawave_api, "_request_raw", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out == "happ://crypto/abc123"
    # First call hits the documented path
    assert "encrypt-happ-crypto-link" in req_mock.call_args.args[1]


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_accepts_alternative_field_names():
    """Different panel forks use different field names — parser must
    survive `cryptoLink`, `happLink`, `link`, etc."""
    from app.services import remnawave_api
    for field in ("cryptoLink", "happLink", "link", "data", "encrypted_link"):
        remnawave_api._reset_happ_crypto_endpoint_cache_for_tests()
        payload = {field: "happ://crypto/xyz"}
        req_mock = AsyncMock(return_value=_raw(200, response=payload))
        with patch.object(remnawave_api, "_request_raw", req_mock):
            out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
        assert out == "happ://crypto/xyz", f"Failed to extract from field {field!r}"


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_substring_fallback():
    """Panel returns the link under an unknown field name — last-resort
    substring search rescues us."""
    from app.services import remnawave_api
    payload = {"weird_unknown_field": "  happ://crypto/from-substring  "}
    req_mock = AsyncMock(return_value=_raw(200, response=payload))
    with patch.object(remnawave_api, "_request_raw", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out == "happ://crypto/from-substring"


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_tries_alternative_paths_on_404():
    """404 on the documented path → automatically tries the next path
    until one returns a usable link.  Winning path is cached."""
    from app.services import remnawave_api

    payload_ok = _raw(200, response={"encryptedLink": "happ://crypto/found"})
    req_mock = AsyncMock(side_effect=[
        _raw(404, body="not found"),                       # 1st path 404s
        _raw(404, body="not found"),                       # 2nd path 404s
        payload_ok,                                        # 3rd path wins
    ])
    with patch.object(remnawave_api, "_request_raw", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out == "happ://crypto/found"
    assert req_mock.await_count == 3
    # Second call should hit only the cached winning path.
    req_mock.reset_mock(side_effect=True)
    req_mock.side_effect = [payload_ok]
    with patch.object(remnawave_api, "_request_raw", req_mock):
        out2 = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/Y")
    assert out2 == "happ://crypto/found"
    assert req_mock.await_count == 1  # cached path used


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_none_on_real_panel_error():
    """5xx → returns None without trying alternative paths (panel is up
    but rejecting our request, no point trying lookalikes)."""
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value=_raw(500, body="panel down"))
    with patch.object(remnawave_api, "_request_raw", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out is None


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_none_when_no_link_in_any_path():
    """Every documented + fallback path responds 200 with garbage — None."""
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value=_raw(200, response={"foo": "bar"}))
    happ_su_mock = AsyncMock(return_value=None)
    with patch.object(remnawave_api, "_request_raw", req_mock), \
         patch.object(remnawave_api, "_encrypt_via_happ_su", happ_su_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out is None


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_falls_back_to_happ_su_when_all_panel_paths_404():
    """v2.7.4 deployment: all in-panel paths 404 — external happ.su API
    is consulted as a last resort.  This is the actual production path
    until the panel ever ships the in-panel endpoint."""
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value=_raw(404, body="not found"))
    happ_su_mock = AsyncMock(return_value="happ://crypto/from-happ-su")
    with patch.object(remnawave_api, "_request_raw", req_mock), \
         patch.object(remnawave_api, "_encrypt_via_happ_su", happ_su_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out == "happ://crypto/from-happ-su"
    happ_su_mock.assert_awaited_once_with("https://rmnw/sub/X")


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_none_when_both_panel_and_happ_su_fail():
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value=_raw(404, body="not found"))
    happ_su_mock = AsyncMock(return_value=None)
    with patch.object(remnawave_api, "_request_raw", req_mock), \
         patch.object(remnawave_api, "_encrypt_via_happ_su", happ_su_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out is None
    happ_su_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_none_for_empty_input():
    from app.services import remnawave_api
    req_mock = AsyncMock()
    with patch.object(remnawave_api, "_request_raw", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("")
    assert out is None
    req_mock.assert_not_called()


# ── _encrypt_via_happ_su (external fallback) ─────────────────────────

@pytest.mark.asyncio
async def test_happ_su_returns_link_from_json_response(monkeypatch):
    from app.services import remnawave_api

    class _Resp:
        status_code = 200
        text = ""
        def json(self):
            return {"url": "happ://crypto/from-external"}

    class _Client:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_e): return None
        async def post(self, *_a, **_kw): return _Resp()

    monkeypatch.setattr(remnawave_api.httpx, "AsyncClient", _Client)
    out = await remnawave_api._encrypt_via_happ_su("https://rmnw/sub/X")
    assert out == "happ://crypto/from-external"


@pytest.mark.asyncio
async def test_happ_su_returns_link_from_raw_text_response(monkeypatch):
    """Some endpoints return the link as plain text (no JSON wrapper)."""
    from app.services import remnawave_api

    class _Resp:
        status_code = 200
        text = "happ://crypto/plain-text-response"
        def json(self):
            raise ValueError("not json")

    class _Client:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_e): return None
        async def post(self, *_a, **_kw): return _Resp()

    monkeypatch.setattr(remnawave_api.httpx, "AsyncClient", _Client)
    out = await remnawave_api._encrypt_via_happ_su("https://rmnw/sub/X")
    assert out == "happ://crypto/plain-text-response"


@pytest.mark.asyncio
async def test_happ_su_returns_none_on_5xx(monkeypatch):
    from app.services import remnawave_api

    class _Resp:
        status_code = 500
        text = "server error"
        def json(self):
            raise ValueError("not json")

    class _Client:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_e): return None
        async def post(self, *_a, **_kw): return _Resp()

    monkeypatch.setattr(remnawave_api.httpx, "AsyncClient", _Client)
    out = await remnawave_api._encrypt_via_happ_su("https://rmnw/sub/X")
    assert out is None


@pytest.mark.asyncio
async def test_happ_su_returns_none_on_timeout(monkeypatch):
    from app.services import remnawave_api

    class _Client:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_e): return None
        async def post(self, *_a, **_kw):
            raise remnawave_api.httpx.TimeoutException("slow")

    monkeypatch.setattr(remnawave_api.httpx, "AsyncClient", _Client)
    out = await remnawave_api._encrypt_via_happ_su("https://rmnw/sub/X")
    assert out is None


# ── _extract_happ_crypto_link helper ──────────────────────────────────

class TestExtractHelper:
    def test_known_field_encrypted_link(self):
        from app.services.remnawave_api import _extract_happ_crypto_link
        assert _extract_happ_crypto_link({"encryptedLink": "happ://crypto/a"}) == "happ://crypto/a"

    def test_case_insensitive_field_match(self):
        from app.services.remnawave_api import _extract_happ_crypto_link
        # Some panels camelCase, some PascalCase
        assert _extract_happ_crypto_link({"EncryptedLink": "happ://crypto/b"}) == "happ://crypto/b"

    def test_nested_response_wrapper(self):
        from app.services.remnawave_api import _extract_happ_crypto_link
        nested = {"response": {"link": "happ://crypto/nested"}}
        assert _extract_happ_crypto_link(nested) == "happ://crypto/nested"

    def test_raw_string_input(self):
        from app.services.remnawave_api import _extract_happ_crypto_link
        assert _extract_happ_crypto_link("happ://crypto/raw") == "happ://crypto/raw"

    def test_rejects_non_crypto_strings(self):
        from app.services.remnawave_api import _extract_happ_crypto_link
        assert _extract_happ_crypto_link({"link": "https://example.com"}) is None
        assert _extract_happ_crypto_link("https://something") is None

    def test_rejects_none_and_unexpected_types(self):
        from app.services.remnawave_api import _extract_happ_crypto_link
        assert _extract_happ_crypto_link(None) is None
        assert _extract_happ_crypto_link(["happ://crypto/x"]) is None  # lists aren't dicts


# ── get_user_premium_happ_crypto_link ────────────────────────────────

def _patch_links_config(enabled=True):
    from app.services import user_subscription_links
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=enabled)
    return patch.object(user_subscription_links, "config", cfg)


@pytest.mark.asyncio
async def test_premium_happ_crypto_returns_cached_value(monkeypatch):
    from app.services import user_subscription_links
    db = SimpleNamespace(
        get_remnawave_premium_happ_crypto_link=AsyncMock(return_value="happ://crypto/cached"),
        set_remnawave_premium_happ_crypto_link=AsyncMock(),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_links_config():
        out = await user_subscription_links.get_user_premium_happ_crypto_link(42)
    assert out == "happ://crypto/cached"
    db.set_remnawave_premium_happ_crypto_link.assert_not_called()


@pytest.mark.asyncio
async def test_premium_happ_crypto_encrypts_on_cache_miss_and_persists(monkeypatch):
    from app.services import user_subscription_links
    persist_mock = AsyncMock()
    db = SimpleNamespace(
        get_remnawave_premium_happ_crypto_link=AsyncMock(return_value=None),
        set_remnawave_premium_happ_crypto_link=persist_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)

    get_plain_mock = AsyncMock(return_value="https://rmnw/sub/plain")
    encrypt_mock = AsyncMock(return_value="happ://crypto/fresh")
    with _patch_links_config(), \
         patch.object(user_subscription_links, "get_user_premium_url", get_plain_mock), \
         patch("app.services.remnawave_api.encrypt_happ_crypto_link", encrypt_mock):
        out = await user_subscription_links.get_user_premium_happ_crypto_link(42)
    assert out == "happ://crypto/fresh"
    encrypt_mock.assert_awaited_once_with("https://rmnw/sub/plain")
    persist_mock.assert_awaited_once_with(42, "happ://crypto/fresh")


@pytest.mark.asyncio
async def test_premium_happ_crypto_returns_none_when_no_plain_url(monkeypatch):
    from app.services import user_subscription_links
    db = SimpleNamespace(
        get_remnawave_premium_happ_crypto_link=AsyncMock(return_value=None),
        set_remnawave_premium_happ_crypto_link=AsyncMock(),
    )
    monkeypatch.setitem(sys.modules, "database", db)

    with _patch_links_config(), \
         patch.object(user_subscription_links, "get_user_premium_url", AsyncMock(return_value=None)):
        out = await user_subscription_links.get_user_premium_happ_crypto_link(42)
    assert out is None


@pytest.mark.asyncio
async def test_premium_happ_crypto_returns_none_when_encrypt_fails(monkeypatch):
    from app.services import user_subscription_links
    persist_mock = AsyncMock()
    db = SimpleNamespace(
        get_remnawave_premium_happ_crypto_link=AsyncMock(return_value=None),
        set_remnawave_premium_happ_crypto_link=persist_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_links_config(), \
         patch.object(user_subscription_links, "get_user_premium_url",
                      AsyncMock(return_value="https://rmnw/sub/plain")), \
         patch("app.services.remnawave_api.encrypt_happ_crypto_link",
               AsyncMock(return_value=None)):
        out = await user_subscription_links.get_user_premium_happ_crypto_link(42)
    assert out is None
    persist_mock.assert_not_called()


@pytest.mark.asyncio
async def test_premium_happ_crypto_short_circuits_when_remnawave_disabled(monkeypatch):
    from app.services import user_subscription_links
    db = SimpleNamespace(
        get_remnawave_premium_happ_crypto_link=AsyncMock(),
        set_remnawave_premium_happ_crypto_link=AsyncMock(),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_links_config(enabled=False):
        out = await user_subscription_links.get_user_premium_happ_crypto_link(42)
    assert out is None
    db.get_remnawave_premium_happ_crypto_link.assert_not_called()


@pytest.mark.asyncio
async def test_premium_happ_crypto_swallows_encrypt_exception(monkeypatch):
    from app.services import user_subscription_links
    db = SimpleNamespace(
        get_remnawave_premium_happ_crypto_link=AsyncMock(return_value=None),
        set_remnawave_premium_happ_crypto_link=AsyncMock(),
    )
    monkeypatch.setitem(sys.modules, "database", db)

    async def _boom(_):
        raise RuntimeError("network down")

    with _patch_links_config(), \
         patch.object(user_subscription_links, "get_user_premium_url",
                      AsyncMock(return_value="https://rmnw/sub/plain")), \
         patch("app.services.remnawave_api.encrypt_happ_crypto_link", _boom):
        out = await user_subscription_links.get_user_premium_happ_crypto_link(42)
    assert out is None


# ── get_user_premium_displayable_url ─────────────────────────────────

@pytest.mark.asyncio
async def test_displayable_url_prefers_crypto_link_when_available():
    from app.services import user_subscription_links
    with patch.object(user_subscription_links, "get_user_premium_happ_crypto_link",
                      AsyncMock(return_value="happ://crypto/abc")), \
         patch.object(user_subscription_links, "get_user_primary_subscription_url",
                      AsyncMock(return_value="https://rmnw/sub/plain")):
        out = await user_subscription_links.get_user_premium_displayable_url(42)
    assert out == "happ://crypto/abc"


@pytest.mark.asyncio
async def test_displayable_url_falls_back_to_plain_when_crypto_unavailable():
    from app.services import user_subscription_links
    with patch.object(user_subscription_links, "get_user_premium_happ_crypto_link",
                      AsyncMock(return_value=None)), \
         patch.object(user_subscription_links, "get_user_primary_subscription_url",
                      AsyncMock(return_value="https://rmnw/sub/plain")):
        out = await user_subscription_links.get_user_premium_displayable_url(42)
    assert out == "https://rmnw/sub/plain"


# ── migration_broadcast: text uses display URL, button uses plain ────

class TestRenderForUser:
    def test_text_embeds_displayable_url_and_button_uses_plain(self):
        from app.services import migration_broadcast
        cfg = SimpleNamespace(
            PUBLIC_BASE_URL="https://bot.example.com",
            WEBHOOK_URL="",
            SUPPORT_URL="https://t.me/x",
        )
        with patch.object(migration_broadcast, "config", cfg):
            text, kb = migration_broadcast.render_for_user(
                "happ://crypto/abcdef",
                button_subscription_url="https://rmnw/sub/plain123",
            )
        # The displayable (crypto) link is what users see + can copy.
        assert "happ://crypto/abcdef" in text
        # The plain URL never appears in the body — it's only inside
        # the redirect-endpoint query parameter of the keyboard button.
        assert "rmnw/sub/plain123" not in text
        # Keyboard button URL contains the plain URL (url-encoded).
        happ_btn = next(b for b in kb.inline_keyboard[0] if b.text == "🔄 Обновить")
        assert "plain123" in happ_btn.url

    def test_render_falls_back_to_displayable_for_button_when_plain_omitted(self):
        """Older callers that only pass one URL still work — preserves
        the pre-Task-4 contract."""
        from app.services import migration_broadcast
        cfg = SimpleNamespace(
            PUBLIC_BASE_URL="https://bot.example.com",
            WEBHOOK_URL="",
            SUPPORT_URL="https://t.me/x",
        )
        with patch.object(migration_broadcast, "config", cfg):
            text, kb = migration_broadcast.render_for_user("https://rmnw/sub/only")
        assert "https://rmnw/sub/only" in text
        happ_btn = next(b for b in kb.inline_keyboard[0] if b.text == "🔄 Обновить")
        assert "only" in happ_btn.url
