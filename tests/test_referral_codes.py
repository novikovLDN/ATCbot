"""Unit tests for the referral code generator and lookup precedence."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest

from database.users import generate_referral_code


def test_generate_returns_8_chars_alphanumeric():
    code = generate_referral_code(123456)
    assert isinstance(code, str)
    assert len(code) == 8
    assert re.fullmatch(r"[A-Z2-9]+", code)


def test_generate_is_non_deterministic():
    """Same telegram_id must NOT yield the same code (privacy fix)."""
    samples = {generate_referral_code(123456) for _ in range(50)}
    # 50 random 8-char codes — collision odds are negligible.
    assert len(samples) > 1


def test_generate_uses_safe_alphabet():
    """No I, L, O, 0, 1 — Crockford-style for human readability."""
    forbidden = set("ILO01")
    for _ in range(50):
        code = generate_referral_code(0)
        assert not (set(code) & forbidden), f"forbidden char in {code}"


@pytest.mark.asyncio
async def test_legacy_lookup_disabled_by_default(monkeypatch):
    """Pure-numeric ref code must NOT resolve to a user when the legacy
    flag is off (the default)."""
    monkeypatch.delenv("LEGACY_REFERRAL_LOOKUP_ENABLED", raising=False)
    from app.services.referrals import service

    with patch("database.find_user_by_referral_code", AsyncMock(return_value=None)) as op_lookup, \
         patch("database.get_user", AsyncMock(return_value={"telegram_id": 42, "referrer_id": None})), \
         patch.object(service, "_activate_referral_internal", AsyncMock()):
        # Bypass the heavy registration path by exercising only the parser.
        result = await service.process_referral_registration(
            telegram_id=99,
            referral_code="ref_42",
        )
        # Legacy path is gated; opaque lookup is the only one consulted.
        op_lookup.assert_called_once()
        assert result["success"] is False
        assert result["state"].value == "none"


@pytest.mark.asyncio
async def test_legacy_lookup_enabled_via_env(monkeypatch):
    """When the env flag is set the numeric fallback is consulted.

    We don't drive the full registration through; we just confirm the gate
    causes ``database.get_user`` to be called with the numeric id from the
    payload (proof that legacy resolution attempts happen).
    """
    monkeypatch.setenv("LEGACY_REFERRAL_LOOKUP_ENABLED", "true")
    from app.services.referrals import service

    with patch("database.find_user_by_referral_code", AsyncMock(return_value=None)), \
         patch("database.get_user", AsyncMock(return_value=None)) as get_user_mock:
        result = await service.process_referral_registration(
            telegram_id=99,
            referral_code="ref_42",
        )
        get_user_mock.assert_awaited()  # legacy gate consulted database.get_user(42)
        assert result["state"].value == "none"
