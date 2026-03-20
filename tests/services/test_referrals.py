"""
Tests for referral service — registration, activation, and safety checks.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.referrals.service import (
    process_referral_registration,
    activate_referral,
    get_referral_state,
    ReferralState,
)


@pytest.fixture
def mock_db():
    """Mock database module for referral tests."""
    with patch("app.services.referrals.service.database") as mock:
        mock.get_user = AsyncMock()
        mock.find_user_by_referral_code = AsyncMock()
        mock.register_referral = AsyncMock()
        mock.get_pool = AsyncMock()
        yield mock


class TestProcessReferralRegistration:
    """Referral registration tests."""

    @pytest.mark.asyncio
    async def test_no_referral_code(self, mock_db):
        result = await process_referral_registration(telegram_id=100, referral_code=None)
        assert result["success"] is False
        assert result["state"] == ReferralState.NONE
        assert result["reason"] == "no_referral_code"

    @pytest.mark.asyncio
    async def test_invalid_format_no_prefix(self, mock_db):
        result = await process_referral_registration(telegram_id=100, referral_code="abc123")
        assert result["success"] is False
        assert result["reason"] == "invalid_referral_code_format"

    @pytest.mark.asyncio
    async def test_self_referral_blocked(self, mock_db):
        mock_db.get_user.return_value = {"telegram_id": 100, "referrer_id": None}
        result = await process_referral_registration(telegram_id=100, referral_code="ref_100")
        assert result["success"] is False
        assert result["reason"] == "self_referral"

    @pytest.mark.asyncio
    async def test_referrer_not_found(self, mock_db):
        mock_db.get_user.side_effect = [None]  # Legacy lookup fails
        mock_db.find_user_by_referral_code.return_value = None
        result = await process_referral_registration(telegram_id=100, referral_code="ref_nonexistent")
        assert result["success"] is False
        assert result["reason"] == "invalid_referral_code_value"

    @pytest.mark.asyncio
    async def test_immutable_referrer_id(self, mock_db):
        # First call: legacy lookup finds referrer (user 200)
        mock_db.get_user.side_effect = [
            {"telegram_id": 200, "referrer_id": None},  # Legacy lookup for referrer
            {"telegram_id": 100, "referrer_id": 300, "referred_by": 300},  # User already has referrer
        ]
        result = await process_referral_registration(telegram_id=100, referral_code="ref_200")
        assert result["success"] is False
        assert result["reason"] == "referrer_id_already_set"
        assert result["referrer_id"] == 300

    @pytest.mark.asyncio
    async def test_referral_loop_blocked(self, mock_db):
        # User 100 tries ref_200, but user 200 was referred by user 100
        mock_db.get_user.side_effect = [
            {"telegram_id": 200, "referrer_id": 100, "referred_by": 100},  # Referrer user (has loop)
            {"telegram_id": 100, "referrer_id": None, "referred_by": None},  # Current user
            {"telegram_id": 200, "referrer_id": 100, "referred_by": 100},  # Verify referrer exists
        ]
        result = await process_referral_registration(telegram_id=100, referral_code="ref_200")
        assert result["success"] is False
        assert result["reason"] == "referral_loop"

    @pytest.mark.asyncio
    async def test_successful_registration_legacy_code(self, mock_db):
        mock_db.get_user.side_effect = [
            {"telegram_id": 200, "referrer_id": None, "referred_by": None},  # Legacy referrer lookup
            {"telegram_id": 100, "referrer_id": None, "referred_by": None},  # Current user
            {"telegram_id": 200, "referrer_id": None, "referred_by": None},  # Verify referrer
        ]
        mock_db.register_referral.return_value = True

        result = await process_referral_registration(telegram_id=100, referral_code="ref_200")
        assert result["success"] is True
        assert result["state"] == ReferralState.REGISTERED
        assert result["referrer_id"] == 200

    @pytest.mark.asyncio
    async def test_successful_registration_opaque_code(self, mock_db):
        mock_db.get_user.side_effect = [
            None,  # Legacy lookup fails (non-numeric code)
            {"telegram_id": 100, "referrer_id": None, "referred_by": None},  # Current user
            {"telegram_id": 200, "referrer_id": None, "referred_by": None},  # Verify referrer
        ]
        mock_db.find_user_by_referral_code.return_value = {"telegram_id": 200}
        mock_db.register_referral.return_value = True

        result = await process_referral_registration(telegram_id=100, referral_code="ref_abc123")
        assert result["success"] is True
        assert result["referrer_id"] == 200


class TestActivateReferral:
    """Referral activation tests."""

    @pytest.mark.asyncio
    async def test_no_referrer(self, mock_db):
        mock_db.get_user.return_value = {"telegram_id": 100, "referrer_id": None}
        result = await activate_referral(telegram_id=100)
        assert result["success"] is False
        assert result["state"] == ReferralState.NONE
        assert result["was_activated"] is False

    @pytest.mark.asyncio
    async def test_activation_with_conn(self, mock_db):
        """Test activation when conn is provided."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock()
        # First call: get user referrer_id
        mock_conn.fetchrow.side_effect = [
            {"referrer_id": 200},  # User lookup
            None,  # Referral row doesn't exist yet
        ]
        mock_conn.execute = AsyncMock()

        result = await activate_referral(telegram_id=100, activation_type="payment", conn=mock_conn)
        assert result["success"] is True
        assert result["state"] == ReferralState.ACTIVATED
        assert result["was_activated"] is True

    @pytest.mark.asyncio
    async def test_already_activated(self, mock_db):
        """Already activated referral should return was_activated=False."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"referrer_id": 200},  # User lookup
            {"first_paid_at": "2024-01-01"},  # Already activated
        ]

        result = await activate_referral(telegram_id=100, conn=mock_conn)
        assert result["success"] is True
        assert result["was_activated"] is False
