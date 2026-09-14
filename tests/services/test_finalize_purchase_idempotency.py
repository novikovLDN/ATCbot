"""
Duplicate-webhook idempotency for database.finalize_purchase.

Moved 1:1 from tests/integration/test_vpn_entitlement.py (the rest of that
file tested the removed samopis vpn_utils / app.services.vpn shims).
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestDuplicateWebhookIdempotency:
    """Test 2: Duplicate webhook must not create duplicate subscription."""

    @pytest.mark.asyncio
    async def test_duplicate_webhook_raises_already_processed(self):
        """Same purchase_id, status already 'paid' → ValueError."""
        # finalize_purchase lives in database.subscriptions and binds get_pool
        # there (from database.core import get_pool) — patching the package
        # re-export `database.get_pool` left the real, uninitialised pool.
        with patch("database.subscriptions.get_pool") as mock_pool:
            conn = MagicMock()
            conn.fetchrow = AsyncMock(return_value={
                "purchase_id": "p1", "telegram_id": 123, "status": "paid",  # already paid
                "tariff": "basic", "period_days": 30, "price_kopecks": 10000,
                "purchase_type": "subscription"
            })
            pool = MagicMock()
            acq = MagicMock()
            acq.__aenter__ = AsyncMock(return_value=conn)
            acq.__aexit__ = AsyncMock(return_value=None)
            pool.acquire.return_value = acq
            mock_pool.return_value = pool

            import database
            with pytest.raises(ValueError, match="already processed"):
                await database.finalize_purchase(
                    purchase_id="p1",
                    payment_provider="cryptobot",
                    amount_rubles=100.0,
                )
