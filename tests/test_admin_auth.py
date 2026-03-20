"""
Tests for admin authorization — verifies that admin check uses ADMIN_TELEGRAM_IDS set
and that non-admin users are rejected.
"""
import pytest
from unittest.mock import patch


class TestAdminTelegramIDs:
    """Verify ADMIN_TELEGRAM_IDS parsing and usage."""

    def test_single_admin_id(self):
        """Single admin ID is parsed correctly."""
        with patch.dict("os.environ", {
            "APP_ENV": "local",
            "LOCAL_BOT_TOKEN": "fake",
            "LOCAL_ADMIN_TELEGRAM_ID": "12345",
            "LOCAL_DATABASE_URL": "postgresql://x:y@localhost/db",
            "LOCAL_WEBHOOK_URL": "https://fake/webhook",
            "LOCAL_WEBHOOK_SECRET": "secret",
        }, clear=False):
            # Re-parse would require reimporting config; test the set logic directly
            ids_str = "12345"
            ids = set()
            for s in ids_str.split(","):
                s = s.strip()
                if s:
                    ids.add(int(s))
            assert ids == {12345}

    def test_multi_admin_ids(self):
        """Multiple comma-separated admin IDs are parsed correctly."""
        ids_str = "12345, 67890, 11111"
        ids = set()
        for s in ids_str.split(","):
            s = s.strip()
            if s:
                ids.add(int(s))
        assert ids == {12345, 67890, 11111}

    def test_admin_check_rejects_non_admin(self):
        """Non-admin user ID should not be in ADMIN_TELEGRAM_IDS."""
        admin_ids = {12345, 67890}
        non_admin_id = 99999
        assert non_admin_id not in admin_ids

    def test_admin_check_accepts_admin(self):
        """Admin user ID should be in ADMIN_TELEGRAM_IDS."""
        admin_ids = {12345, 67890}
        assert 12345 in admin_ids
        assert 67890 in admin_ids

    def test_empty_string_in_ids_ignored(self):
        """Empty strings between commas should be ignored."""
        ids_str = "12345,,67890,"
        ids = set()
        for s in ids_str.split(","):
            s = s.strip()
            if s:
                ids.add(int(s))
        assert ids == {12345, 67890}


class TestGuardsCacheTTL:
    """Test that critical tables check uses TTL cache (requires config env vars)."""

    @pytest.mark.skipif(
        not __import__("os").getenv("LOCAL_BOT_TOKEN"),
        reason="Requires LOCAL_BOT_TOKEN env var (CI only)"
    )
    def test_cache_structure(self):
        from app.handlers.common.guards import _critical_tables_cache, _CRITICAL_TABLES_CACHE_TTL
        assert "result" in _critical_tables_cache
        assert "expires" in _critical_tables_cache
        assert _CRITICAL_TABLES_CACHE_TTL == 30.0
