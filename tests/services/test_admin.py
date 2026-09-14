"""
Unit tests for admin service layer.

Tests focus on business logic:
- User overview data aggregation
- Admin action decisions
- Edge cases

VIP was removed (owner decision 2026-09-14): no VIP fields / actions.
"""
import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock
from app.services.admin.service import (
    get_admin_user_overview,
    get_admin_user_actions,
    AdminUserOverview,
    AdminActions,
)
from app.services.admin.exceptions import UserNotFoundError
from app.services.subscriptions.service import SubscriptionStatus


def _inactive_status():
    return SubscriptionStatus(
        is_active=False,
        has_subscription=False,
        expires_at=None,
        activation_status=None,
        is_expired=False,
    )


def _overview(**kw):
    base = dict(
        user={"telegram_id": 12345},
        subscription=None,
        subscription_status=_inactive_status(),
        stats={},
        user_discount=None,
        trial_available=False,
    )
    base.update(kw)
    return AdminUserOverview(**base)


class TestGetAdminUserOverview:
    """Tests for get_admin_user_overview function"""

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        """Should raise UserNotFoundError when user doesn't exist"""
        with patch('app.services.admin.service.database') as mock_db:
            mock_db.get_user = AsyncMock(return_value=None)

            with pytest.raises(UserNotFoundError):
                await get_admin_user_overview(12345)

    @pytest.mark.asyncio
    async def test_user_with_active_subscription(self):
        """Should return overview with active subscription"""
        user = {"telegram_id": 12345, "username": "test_user"}
        subscription = {
            "telegram_id": 12345,
            "status": "active",
            "expires_at": datetime(2024, 2, 15, 12, 0, 0),
            "uuid": "test-uuid",
        }
        stats = {"renewals_count": 2, "reissues_count": 1}

        with patch('app.services.admin.service.database') as mock_db, \
             patch('app.services.admin.service.get_subscription_status') as mock_status, \
             patch('app.services.admin.service.trial_service') as mock_trial:

            mock_db.get_user = AsyncMock(return_value=user)
            mock_db.get_subscription = AsyncMock(return_value=subscription)
            mock_db.get_user_extended_stats = AsyncMock(return_value=stats)
            mock_db.get_user_discount = AsyncMock(return_value=None)
            # Added in 7fb295ee (per-user discount on bypass GB purchases).
            mock_db.get_user_traffic_discount = AsyncMock(return_value=None)
            mock_trial.is_trial_available = AsyncMock(return_value=False)

            status = SubscriptionStatus(
                is_active=True,
                has_subscription=True,
                expires_at=datetime(2024, 2, 15, 12, 0, 0),
                activation_status="active",
                is_expired=False,
            )
            mock_status.return_value = status

            overview = await get_admin_user_overview(12345)

            assert overview.user == user
            assert overview.subscription == subscription
            assert overview.subscription_status is status
            assert overview.stats == stats
            assert overview.trial_available is False
            assert overview.user_traffic_discount is None
            assert not hasattr(overview, "is_vip")
            mock_status.assert_called_once_with(subscription)
            mock_db.get_user_traffic_discount.assert_awaited_once_with(12345)

    @pytest.mark.asyncio
    async def test_user_with_discount(self):
        """Should return overview with the personal and traffic discounts"""
        user = {"telegram_id": 12345, "username": "test_user"}
        discount = {"discount_percent": 10, "expires_at": None}

        with patch('app.services.admin.service.database') as mock_db, \
             patch('app.services.admin.service.get_subscription_status') as mock_status, \
             patch('app.services.admin.service.trial_service') as mock_trial:

            mock_db.get_user = AsyncMock(return_value=user)
            mock_db.get_subscription = AsyncMock(return_value=None)
            mock_db.get_user_extended_stats = AsyncMock(return_value={})
            mock_db.get_user_discount = AsyncMock(return_value=discount)
            traffic_discount = {"discount_percent": 20, "expires_at": None}
            mock_db.get_user_traffic_discount = AsyncMock(return_value=traffic_discount)
            mock_trial.is_trial_available = AsyncMock(return_value=False)
            mock_status.return_value = _inactive_status()

            overview = await get_admin_user_overview(12345)

            assert overview.user_discount == discount
            assert overview.user_traffic_discount == traffic_discount


class TestGetAdminUserActions:
    """Tests for get_admin_user_actions function"""

    def test_actions_for_active_subscription(self):
        """Active subscription should allow key reissue"""
        overview = _overview(
            subscription={"status": "active"},
            subscription_status=SubscriptionStatus(
                is_active=True,
                has_subscription=True,
                expires_at=datetime(2024, 2, 15, 12, 0, 0),
                activation_status="active",
                is_expired=False,
            ),
        )

        actions = get_admin_user_actions(overview)

        assert actions.can_reissue_key is True
        assert actions.can_revoke_access is True
        assert actions.can_grant_access is True

    def test_actions_for_inactive_subscription(self):
        """Inactive subscription should not allow key reissue"""
        actions = get_admin_user_actions(_overview())

        assert actions.can_reissue_key is False
        assert actions.can_revoke_access is False
        assert actions.can_grant_access is True

    def test_no_vip_actions(self):
        """VIP was removed: no VIP actions at all"""
        actions = get_admin_user_actions(_overview())
        assert isinstance(actions, AdminActions)
        assert not hasattr(actions, "can_grant_vip")
        assert not hasattr(actions, "can_revoke_vip")

    def test_actions_for_user_with_discount(self):
        """User with discount should allow revoke, not grant"""
        actions = get_admin_user_actions(_overview(user_discount={"discount_percent": 10}))

        assert actions.can_grant_discount is False
        assert actions.can_revoke_discount is True

    def test_actions_for_user_without_discount(self):
        """User without discount should allow grant, not revoke"""
        actions = get_admin_user_actions(_overview())

        assert actions.can_grant_discount is True
        assert actions.can_revoke_discount is False

    def test_view_history_always_available(self):
        """View history should always be available"""
        actions = get_admin_user_actions(_overview())

        assert actions.can_view_history is True
