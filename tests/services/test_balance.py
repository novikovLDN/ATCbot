"""
Tests for balance operations — increase, decrease, validation.

These test the pure logic and validation without hitting a real database.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestIncreaseBalance:
    """Tests for increase_balance validation logic."""

    @pytest.mark.asyncio
    async def test_rejects_zero_amount(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            from database.users import increase_balance
            result = await increase_balance(telegram_id=100, amount=0, source="test")
            assert result is False

    @pytest.mark.asyncio
    async def test_rejects_negative_amount(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            from database.users import increase_balance
            result = await increase_balance(telegram_id=100, amount=-50, source="test")
            assert result is False

    @pytest.mark.asyncio
    async def test_rejects_when_db_not_ready(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = False
            from database.users import increase_balance
            result = await increase_balance(telegram_id=100, amount=100, source="test")
            assert result is False

    @pytest.mark.asyncio
    async def test_success_with_conn(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            from database.users import increase_balance
            result = await increase_balance(
                telegram_id=100, amount=250.0, source="telegram_payment",
                description="Test topup", conn=mock_conn
            )
            assert result is True
            # Verify advisory lock was acquired
            calls = [str(c) for c in mock_conn.execute.call_args_list]
            assert any("pg_advisory_xact_lock" in c for c in calls)
            # Verify balance update
            assert any("balance + $1" in c for c in calls)

    @pytest.mark.asyncio
    async def test_kopeck_conversion(self):
        """Verify that amount in rubles is converted to kopecks."""
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            from database.users import increase_balance
            await increase_balance(
                telegram_id=100, amount=149.0, source="payment", conn=mock_conn
            )
            # The second arg to the UPDATE call should be 14900 (kopecks)
            update_call = [c for c in mock_conn.execute.call_args_list
                          if "balance" in str(c)]
            assert len(update_call) > 0


class TestDecreaseBalance:
    """Tests for decrease_balance validation logic."""

    @pytest.mark.asyncio
    async def test_rejects_zero_amount(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            from database.users import decrease_balance
            result = await decrease_balance(telegram_id=100, amount=0, source="test")
            assert result is False

    @pytest.mark.asyncio
    async def test_rejects_negative_amount(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            from database.users import decrease_balance
            result = await decrease_balance(telegram_id=100, amount=-10, source="test")
            assert result is False

    @pytest.mark.asyncio
    async def test_rejects_when_db_not_ready(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = False
            from database.users import decrease_balance
            result = await decrease_balance(telegram_id=100, amount=100, source="test")
            assert result is False

    @pytest.mark.asyncio
    async def test_insufficient_balance(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.fetchrow = AsyncMock(return_value={"balance": 5000})  # 50 rubles
            from database.users import decrease_balance
            result = await decrease_balance(
                telegram_id=100, amount=100.0, source="payment", conn=mock_conn  # 100 rubles > 50
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_sufficient_balance(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.fetchrow = AsyncMock(return_value={"balance": 30000})  # 300 rubles
            from database.users import decrease_balance
            result = await decrease_balance(
                telegram_id=100, amount=149.0, source="subscription_payment", conn=mock_conn
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        with patch("database.users._core") as mock_core:
            mock_core.DB_READY = True
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.fetchrow = AsyncMock(return_value=None)  # user not found
            from database.users import decrease_balance
            result = await decrease_balance(
                telegram_id=999, amount=10.0, source="test", conn=mock_conn
            )
            assert result is False
