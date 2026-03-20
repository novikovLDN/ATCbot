"""
Tests for referral code generation and referral link building.
"""
import pytest
from unittest.mock import AsyncMock, patch

from database.users import generate_referral_code


class TestGenerateReferralCode:
    """Tests for deterministic referral code generation."""

    def test_returns_string(self):
        code = generate_referral_code(12345)
        assert isinstance(code, str)

    def test_length_6(self):
        code = generate_referral_code(12345)
        assert len(code) == 6

    def test_uppercase_alphanumeric(self):
        code = generate_referral_code(12345)
        assert code == code.upper()
        assert code.isalnum()

    def test_deterministic(self):
        """Same telegram_id always produces the same code."""
        code1 = generate_referral_code(12345)
        code2 = generate_referral_code(12345)
        assert code1 == code2

    def test_different_users_different_codes(self):
        """Different telegram_ids produce different codes."""
        code1 = generate_referral_code(12345)
        code2 = generate_referral_code(67890)
        assert code1 != code2

    def test_large_telegram_id(self):
        code = generate_referral_code(9999999999)
        assert len(code) == 6
        assert code.isalnum()

    def test_small_telegram_id(self):
        code = generate_referral_code(1)
        assert len(code) == 6
        assert code.isalnum()


class TestBuildReferralLink:
    """Tests for referral link building."""

    @pytest.mark.asyncio
    async def test_builds_link_with_opaque_code(self):
        with patch("app.utils.referral_link.database") as mock_db:
            mock_db.get_user_referral_code = AsyncMock(return_value="ABC123")
            from app.utils.referral_link import build_referral_link

            link = await build_referral_link(12345, "testbot")
            assert link == "https://t.me/testbot?start=ref_ABC123"

    @pytest.mark.asyncio
    async def test_fallback_to_legacy_format(self):
        with patch("app.utils.referral_link.database") as mock_db:
            mock_db.get_user_referral_code = AsyncMock(return_value=None)
            from app.utils.referral_link import build_referral_link

            link = await build_referral_link(12345, "testbot")
            assert link == "https://t.me/testbot?start=ref_12345"

    @pytest.mark.asyncio
    async def test_link_contains_bot_username(self):
        with patch("app.utils.referral_link.database") as mock_db:
            mock_db.get_user_referral_code = AsyncMock(return_value="XYZ789")
            from app.utils.referral_link import build_referral_link

            link = await build_referral_link(99999, "my_vpn_bot")
            assert "my_vpn_bot" in link
            assert link.startswith("https://t.me/my_vpn_bot?start=ref_")
