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

@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_link_on_success():
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value={"encryptedLink": "happ://crypto/abc123"})
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out == "happ://crypto/abc123"
    req_mock.assert_awaited_once()
    args, kwargs = req_mock.call_args
    assert args[0] == "POST"
    assert "encrypt-happ-crypto-link" in args[1]
    assert kwargs["json"] == {"data": "https://rmnw/sub/X"}


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_none_when_panel_errors():
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value=None)
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out is None


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_rejects_malformed_payload():
    """Defensive: panel must return happ://crypto/ — anything else is logged + None."""
    from app.services import remnawave_api
    req_mock = AsyncMock(return_value={"encryptedLink": "https://something-else"})
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("https://rmnw/sub/X")
    assert out is None


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_returns_none_for_empty_input():
    from app.services import remnawave_api
    req_mock = AsyncMock()
    with patch.object(remnawave_api, "_request", req_mock):
        out = await remnawave_api.encrypt_happ_crypto_link("")
    assert out is None
    req_mock.assert_not_called()


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
