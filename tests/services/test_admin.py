"""
Unit tests for admin service layer.

Tests focus on business logic:
- User overview data aggregation
- Admin action decisions
- Edge cases
"""
import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock
from app.services.admin.service import (
    get_admin_user_overview,
    get_admin_user_actions,
    AdminUserOverview,
    AdminActions,
)
from app.services.admin.exceptions import UserNotFoundError
from app.services.subscriptions.service import SubscriptionStatus


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
            mock_db.is_vip_user = AsyncMock(return_value=False)
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
            assert overview.stats == stats
            assert overview.is_vip is False
            assert overview.trial_available is False
    
    @pytest.mark.asyncio
    async def test_user_with_vip_and_discount(self):
        """Should return overview with VIP status and discount"""
        user = {"telegram_id": 12345, "username": "test_user"}
        discount = {"discount_percent": 10, "expires_at": None}
        
        with patch('app.services.admin.service.database') as mock_db, \
             patch('app.services.admin.service.get_subscription_status') as mock_status, \
             patch('app.services.admin.service.trial_service') as mock_trial:
            
            mock_db.get_user = AsyncMock(return_value=user)
            mock_db.get_subscription = AsyncMock(return_value=None)
            mock_db.get_user_extended_stats = AsyncMock(return_value={})
            mock_db.get_user_discount = AsyncMock(return_value=discount)
            mock_db.is_vip_user = AsyncMock(return_value=True)
            mock_trial.is_trial_available = AsyncMock(return_value=False)
            
            status = SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            )
            mock_status.return_value = status
            
            overview = await get_admin_user_overview(12345)
            
            assert overview.user_discount == discount
            assert overview.is_vip is True


class TestGetAdminUserActions:
    """Tests for get_admin_user_actions function"""
    
    def test_actions_for_active_subscription(self):
        """Active subscription should allow key reissue"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription={"status": "active"},
            subscription_status=SubscriptionStatus(
                is_active=True,
                has_subscription=True,
                expires_at=datetime(2024, 2, 15, 12, 0, 0),
                activation_status="active",
                is_expired=False,
            ),
            stats={},
            user_discount=None,
            is_vip=False,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_reissue_key is True
        assert actions.can_revoke_access is True
        assert actions.can_grant_access is True
    
    def test_actions_for_inactive_subscription(self):
        """Inactive subscription should not allow key reissue"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription=None,
            subscription_status=SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            ),
            stats={},
            user_discount=None,
            is_vip=False,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_reissue_key is False
        assert actions.can_revoke_access is False
        assert actions.can_grant_access is True
    
    def test_actions_for_vip_user(self):
        """VIP user should allow revoke VIP, not grant"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription=None,
            subscription_status=SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            ),
            stats={},
            user_discount=None,
            is_vip=True,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_grant_vip is False
        assert actions.can_revoke_vip is True
    
    def test_actions_for_non_vip_user(self):
        """Non-VIP user should allow grant VIP, not revoke"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription=None,
            subscription_status=SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            ),
            stats={},
            user_discount=None,
            is_vip=False,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_grant_vip is True
        assert actions.can_revoke_vip is False
    
    def test_actions_for_user_with_discount(self):
        """User with discount should allow revoke, not grant"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription=None,
            subscription_status=SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            ),
            stats={},
            user_discount={"discount_percent": 10},
            is_vip=False,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_grant_discount is False
        assert actions.can_revoke_discount is True
    
    def test_actions_for_user_without_discount(self):
        """User without discount should allow grant, not revoke"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription=None,
            subscription_status=SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            ),
            stats={},
            user_discount=None,
            is_vip=False,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_grant_discount is True
        assert actions.can_revoke_discount is False
    
    def test_view_history_always_available(self):
        """View history should always be available"""
        overview = AdminUserOverview(
            user={"telegram_id": 12345},
            subscription=None,
            subscription_status=SubscriptionStatus(
                is_active=False,
                has_subscription=False,
                expires_at=None,
                activation_status=None,
                is_expired=False,
            ),
            stats={},
            user_discount=None,
            is_vip=False,
            trial_available=False,
        )
        
        actions = get_admin_user_actions(overview)
        
        assert actions.can_view_history is True
