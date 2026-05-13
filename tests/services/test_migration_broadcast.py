"""
Unit tests for app.services.migration_broadcast.

Covers the four pieces of the Task-3 broadcast:
  1. Body rendering — HTML structure, key wrapped in
     <blockquote><code>, no "Premium" in visible text, cutoff date
     embedded, individual URL preserved.
  2. Happ deeplink construction — uses PUBLIC_BASE_URL + /open/happ,
     falls back to WEBHOOK_URL origin, returns None gracefully.
  3. Keyboard layout — 🔄 Обновить + 💬 Поддержка row, Happ button
     dropped when no public origin is available.
  4. run_migration_broadcast — iterates candidates via the database
     stub, marks success rows, swallows failures, returns stats dict.

Telegram-side side-effects are mocked; no aiogram bot is required.
"""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _cfg(**overrides):
    cfg = SimpleNamespace(
        PUBLIC_BASE_URL="https://atcbot-production-2f93.up.railway.app",
        WEBHOOK_URL="https://atcbot-production-2f93.up.railway.app/telegram/webhook",
        SUPPORT_URL="https://t.me/atlassecure_support",
        BROADCAST_RATE_PER_SEC=20,
        MIGRATION_BROADCAST_CONCURRENCY=5,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── Body rendering ────────────────────────────────────────────────────

class TestRenderMigrationText:
    def test_contains_user_url_inside_blockquote_code(self):
        from app.services import migration_broadcast
        url = "https://rmnw.atlassecure.ru/api/sub/AbCdEf12345"
        with patch.object(migration_broadcast, "config", _cfg()):
            text = migration_broadcast.render_migration_text(url)
        assert "<blockquote>" in text
        assert "<code>" in text
        assert url in text
        # The blockquote-wrapped section must contain the code-wrapped url.
        bq_open = text.index("<blockquote>")
        bq_close = text.index("</blockquote>")
        chunk = text[bq_open:bq_close]
        assert "<code>" in chunk and url in chunk

    def test_does_not_use_word_premium_in_visible_text(self):
        """Customer requirement: say 'основные' / 'безлимитные', NOT 'Premium'."""
        from app.services import migration_broadcast
        text = migration_broadcast.render_migration_text("https://rmnw/sub/x")
        assert "premium" not in text.lower(), (
            "Word 'Premium' leaked into the visible broadcast body — "
            "customer requirement is 'основные' / 'безлимитные'."
        )

    def test_mentions_cutoff_date(self):
        from app.services import migration_broadcast
        text = migration_broadcast.render_migration_text("https://rmnw/sub/x")
        assert migration_broadcast.MIGRATION_CUTOFF_DATE_STR in text
        assert "18.05.2026" in text  # explicit pin

    def test_mentions_tap_to_copy_hint(self):
        """New copy: 'Нажми на свой ключ — скопируется сам'."""
        from app.services import migration_broadcast
        text = migration_broadcast.render_migration_text("https://rmnw/sub/x")
        lowered = text.lower()
        assert "нажми на свой ключ" in lowered or "скопируется сам" in lowered

    def test_mentions_lte_bypass_unchanged(self):
        from app.services import migration_broadcast
        text = migration_broadcast.render_migration_text("https://rmnw/sub/x")
        # Body must reassure users that LTE-bypass links are untouched.
        assert "LTE" in text
        assert "обход" in text.lower()

    def test_html_special_chars_in_url_are_escaped(self):
        from app.services import migration_broadcast
        url = "https://example.com/sub/x?a=1&b=2"
        text = migration_broadcast.render_migration_text(url)
        # & must be escaped to &amp; in the rendered body
        assert "&amp;" in text
        assert "?a=1&amp;b=2" in text

    def test_custom_emoji_markers_present_for_safe_send_to_convert(self):
        """Body must include Telegram-Ads emoji markers so
        safe_send_message's convert_tg_emoji rewrites them into
        <tg-emoji> tags on delivery.  We don't pre-convert here — that
        happens at send time, identical to the rest of the bot."""
        from app.services import migration_broadcast
        text = migration_broadcast.render_migration_text("https://rmnw/sub/x")
        assert "(tg://emoji?id=" in text
        # The rocket marker is the headline icon and should always be present.
        assert "5188481279963715781" in text

    def test_emoji_markers_survive_convert_tg_emoji(self):
        """End-to-end: render the body, then run convert_tg_emoji (the
        same function safe_send_message applies before delivery) and
        verify the result contains real <tg-emoji> tags AND no leftover
        Markdown-style markers."""
        from app.services import migration_broadcast
        from app.utils.telegram_safe import convert_tg_emoji
        text = migration_broadcast.render_migration_text("https://rmnw/sub/x")
        rendered = convert_tg_emoji(text)
        assert '<tg-emoji emoji-id="5188481279963715781">' in rendered
        # No leftover Markdown-style emoji markers
        assert "(tg://emoji?id=" not in rendered


# ── Happ deeplink + keyboard ──────────────────────────────────────────

class TestHappDeeplink:
    def test_uses_public_base_url_when_set(self):
        from app.services import migration_broadcast
        with patch.object(migration_broadcast, "config", _cfg()):
            url = migration_broadcast.build_happ_deeplink("https://rmnw/sub/AbCd")
        assert url is not None
        assert url.startswith("https://atcbot-production-2f93.up.railway.app/open/happ?url=")
        # The subscription URL should be percent-encoded
        assert "rmnw%2Fsub%2FAbCd" in url or "rmnw/sub/AbCd" not in url[len("https://atcbot-production-2f93.up.railway.app/open/happ?url="):]

    def test_falls_back_to_webhook_origin_when_public_base_missing(self):
        from app.services import migration_broadcast
        cfg = _cfg(PUBLIC_BASE_URL="")
        with patch.object(migration_broadcast, "config", cfg):
            url = migration_broadcast.build_happ_deeplink("https://rmnw/sub/x")
        assert url is not None
        assert url.startswith("https://atcbot-production-2f93.up.railway.app/open/happ")

    def test_returns_none_when_origin_unavailable(self):
        from app.services import migration_broadcast
        cfg = _cfg(PUBLIC_BASE_URL="", WEBHOOK_URL="")
        with patch.object(migration_broadcast, "config", cfg):
            url = migration_broadcast.build_happ_deeplink("https://rmnw/sub/x")
        assert url is None

    def test_returns_none_when_subscription_url_empty(self):
        from app.services import migration_broadcast
        with patch.object(migration_broadcast, "config", _cfg()):
            assert migration_broadcast.build_happ_deeplink("") is None


class TestKeyboard:
    def test_has_obnovit_and_support_buttons(self):
        from app.services import migration_broadcast
        with patch.object(migration_broadcast, "config", _cfg()):
            kb = migration_broadcast.build_migration_keyboard("https://rmnw/sub/X")
        rows = kb.inline_keyboard
        assert len(rows) == 1
        texts = [btn.text for btn in rows[0]]
        assert "🔄 Обновить" in texts
        assert "💬 Поддержка" in texts

    def test_support_button_uses_configured_url(self):
        from app.services import migration_broadcast
        with patch.object(migration_broadcast, "SUPPORT_URL", "https://t.me/Atlas_SupportSecurity"):
            with patch.object(migration_broadcast, "config", _cfg()):
                kb = migration_broadcast.build_migration_keyboard("https://rmnw/sub/X")
        support = next(b for b in kb.inline_keyboard[0] if b.text == "💬 Поддержка")
        assert support.url == "https://t.me/Atlas_SupportSecurity"

    def test_obnovit_button_skipped_when_no_public_origin(self):
        from app.services import migration_broadcast
        cfg = _cfg(PUBLIC_BASE_URL="", WEBHOOK_URL="")
        with patch.object(migration_broadcast, "config", cfg):
            kb = migration_broadcast.build_migration_keyboard("https://rmnw/sub/X")
        texts = [btn.text for btn in kb.inline_keyboard[0]]
        assert "🔄 Обновить" not in texts
        assert "💬 Поддержка" in texts


# ── send_migration_notice ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_migration_notice_returns_true_on_delivery():
    from app.services import migration_broadcast
    sent = AsyncMock(return_value=SimpleNamespace(message_id=42))
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", sent):
        ok = await migration_broadcast.send_migration_notice(
            bot=SimpleNamespace(), telegram_id=100, premium_subscription_url="https://rmnw/sub/x",
        )
    assert ok is True
    args, kwargs = sent.call_args
    assert args[1] == 100
    assert kwargs["parse_mode"] == "HTML"
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_send_migration_notice_returns_false_when_blocked():
    from app.services import migration_broadcast
    sent = AsyncMock(return_value=None)  # safe_send returns None on Forbidden
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", sent):
        ok = await migration_broadcast.send_migration_notice(
            bot=SimpleNamespace(), telegram_id=100, premium_subscription_url="https://rmnw/sub/x",
        )
    assert ok is False


# ── send_test_notice_to_admin ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_test_notice_uses_admin_url_when_present(monkeypatch):
    from app.services import migration_broadcast

    class _Conn:
        async def fetchval(self, *_a, **_kw):
            return "https://rmnw/sub/admin-cached"
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return None

    class _Pool:
        def acquire(self): return _Conn()

    db = SimpleNamespace(get_pool=AsyncMock(return_value=_Pool()))
    monkeypatch.setitem(sys.modules, "database", db)

    sent = AsyncMock(return_value=SimpleNamespace(message_id=1))
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", sent):
        ok = await migration_broadcast.send_test_notice_to_admin(
            bot=SimpleNamespace(), admin_telegram_id=42,
        )
    assert ok is True
    # The rendered text must contain the admin's own URL.
    body = sent.call_args.args[2]
    assert "https://rmnw/sub/admin-cached" in body


@pytest.mark.asyncio
async def test_test_notice_falls_back_to_placeholder(monkeypatch):
    from app.services import migration_broadcast

    class _Conn:
        async def fetchval(self, *_a, **_kw):
            return None
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return None

    class _Pool:
        def acquire(self): return _Conn()

    db = SimpleNamespace(get_pool=AsyncMock(return_value=_Pool()))
    monkeypatch.setitem(sys.modules, "database", db)

    sent = AsyncMock(return_value=SimpleNamespace(message_id=1))
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", sent):
        await migration_broadcast.send_test_notice_to_admin(
            bot=SimpleNamespace(), admin_telegram_id=42,
        )
    body = sent.call_args.args[2]
    assert "TEST_PLACEHOLDER" in body


# ── run_migration_broadcast ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_skips_users_without_url_and_marks_delivered(monkeypatch):
    from app.services import migration_broadcast

    candidates = [
        {"telegram_id": 1, "premium_url": "https://rmnw/sub/u1"},
        {"telegram_id": 2, "premium_url": ""},                       # skipped
        {"telegram_id": 3, "premium_url": "https://rmnw/sub/u3"},
    ]
    mark_mock = AsyncMock()
    notify_mock = AsyncMock()
    db = SimpleNamespace(
        DB_READY=True,
        list_migration_broadcast_candidates=AsyncMock(return_value=candidates),
        mark_migration_notice_sent=mark_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)

    bot = SimpleNamespace(send_message=notify_mock)
    sent_mock = AsyncMock(return_value=SimpleNamespace(message_id=99))
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", sent_mock):
        result = await migration_broadcast.run_migration_broadcast(
            bot, admin_telegram_id=42, notify_admin_on_complete=True,
        )

    assert result["total"] == 3
    assert result["success"] == 2
    assert result["skipped"] == 1
    assert result["failed"] == 0
    # mark_migration_notice_sent called only for delivered rows
    awaited_ids = {call.args[0] for call in mark_mock.await_args_list}
    assert awaited_ids == {1, 3}
    # Admin completion message sent
    notify_mock.assert_awaited()


@pytest.mark.asyncio
async def test_broadcast_marks_failure_when_safe_send_returns_none(monkeypatch):
    from app.services import migration_broadcast

    candidates = [{"telegram_id": 1, "premium_url": "https://rmnw/sub/u1"}]
    mark_mock = AsyncMock()
    db = SimpleNamespace(
        DB_READY=True,
        list_migration_broadcast_candidates=AsyncMock(return_value=candidates),
        mark_migration_notice_sent=mark_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)

    # safe_send returns None — Forbidden / chat-not-found / etc.
    sent_mock = AsyncMock(return_value=None)
    bot = SimpleNamespace(send_message=AsyncMock())
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", sent_mock):
        result = await migration_broadcast.run_migration_broadcast(
            bot, admin_telegram_id=42, notify_admin_on_complete=False,
        )

    assert result["failed"] == 1
    assert result["success"] == 0
    mark_mock.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_returns_zero_when_db_not_ready(monkeypatch):
    from app.services import migration_broadcast
    db = SimpleNamespace(DB_READY=False)
    monkeypatch.setitem(sys.modules, "database", db)
    result = await migration_broadcast.run_migration_broadcast(
        bot=SimpleNamespace(), admin_telegram_id=42, notify_admin_on_complete=False,
    )
    assert result == {
        "success": 0, "failed": 0, "skipped": 0, "total": 0, "duration_seconds": 0.0,
    }


@pytest.mark.asyncio
async def test_broadcast_swallows_send_exceptions_and_counts_failed(monkeypatch):
    from app.services import migration_broadcast

    candidates = [{"telegram_id": 1, "premium_url": "https://rmnw/sub/u1"}]
    mark_mock = AsyncMock()
    db = SimpleNamespace(
        DB_READY=True,
        list_migration_broadcast_candidates=AsyncMock(return_value=candidates),
        mark_migration_notice_sent=mark_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)

    async def _explode(*_a, **_kw):
        raise RuntimeError("unexpected")

    bot = SimpleNamespace(send_message=AsyncMock())
    with patch.object(migration_broadcast, "config", _cfg()), \
         patch.object(migration_broadcast, "safe_send_message", _explode):
        result = await migration_broadcast.run_migration_broadcast(
            bot, admin_telegram_id=42, notify_admin_on_complete=False,
        )

    assert result["failed"] == 1
    mark_mock.assert_not_called()
