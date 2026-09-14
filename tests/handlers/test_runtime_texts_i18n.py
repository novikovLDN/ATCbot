"""R2 / R3 (docs/audit/11_telegram_runtime.md §5): two user-facing texts were
hardcoded, bypassing get_text — the error-boundary toast (Russian for everyone)
and the pre_checkout rejection (English for everyone). Both now come from i18n
in the user's language.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import database
from app.core.telegram_error_middleware import TelegramErrorBoundaryMiddleware
from app.handlers.payments.payments_messages import process_pre_checkout_query
from app.i18n import get_text


def _user_lang(monkeypatch, lang):
    async def get_user(telegram_id):
        return {"telegram_id": telegram_id, "language": lang}
    monkeypatch.setattr(database, "get_user", get_user)


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_error_boundary_answers_in_the_users_language(monkeypatch, lang):
    _user_lang(monkeypatch, lang)
    cq = SimpleNamespace(id="q1", from_user=SimpleNamespace(id=42), answer=AsyncMock())
    update = SimpleNamespace(update_id=7, callback_query=cq, message=None)

    async def handler(event, data):
        raise RuntimeError("boom")

    assert await TelegramErrorBoundaryMiddleware()(handler, update, {}) is None
    cq.answer.assert_awaited_once()
    assert cq.answer.await_args.args[0] == get_text(lang, "errors.try_later")


async def test_error_boundary_still_answers_when_the_language_lookup_fails(monkeypatch):
    async def get_user(telegram_id):
        raise ConnectionError("db down")
    monkeypatch.setattr(database, "get_user", get_user)
    cq = SimpleNamespace(id="q1", from_user=SimpleNamespace(id=42), answer=AsyncMock())
    update = SimpleNamespace(update_id=7, callback_query=cq, message=None)

    async def handler(event, data):
        raise RuntimeError("boom")

    await TelegramErrorBoundaryMiddleware()(handler, update, {})
    assert cq.answer.await_args.args[0] == get_text("ru", "errors.try_later")


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_pre_checkout_rejection_is_in_the_users_language(monkeypatch, lang):
    _user_lang(monkeypatch, lang)

    async def get_pending_purchase(purchase_id, telegram_id, check_expiry=True):
        return None
    monkeypatch.setattr(database, "get_pending_purchase", get_pending_purchase)
    q = SimpleNamespace(
        invoice_payload="purchase:abc", from_user=SimpleNamespace(id=42),
        currency="RUB", total_amount=19900, answer=AsyncMock(),
    )

    await process_pre_checkout_query(q)

    q.answer.assert_awaited_once()
    assert q.answer.await_args.kwargs["ok"] is False
    assert q.answer.await_args.kwargs["error_message"] == get_text(lang, "payment.expired")
