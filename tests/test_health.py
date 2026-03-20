"""
Tests for health check endpoint.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.api import app
import database.core as db_core


class TestHealthEndpoint:
    """Tests for GET /health."""

    @pytest.mark.asyncio
    async def test_health_returns_503_when_db_not_ready(self):
        original = db_core.DB_READY
        try:
            db_core.DB_READY = False
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "degraded"
            assert data["database"] == "not_ready"
        finally:
            db_core.DB_READY = original

    @pytest.mark.asyncio
    async def test_health_returns_200_when_healthy(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        original = db_core.DB_READY
        try:
            db_core.DB_READY = True
            with patch("database.core.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
                 patch("database.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
                 patch("app.utils.redis_client.is_configured", return_value=False):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/health")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "ok"
                assert data["database"] == "connected"
        finally:
            db_core.DB_READY = original

    @pytest.mark.asyncio
    async def test_health_returns_503_when_pool_is_none(self):
        original = db_core.DB_READY
        try:
            db_core.DB_READY = True
            with patch("database.core.get_pool", new_callable=AsyncMock, return_value=None), \
                 patch("database.get_pool", new_callable=AsyncMock, return_value=None):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/health")
                assert response.status_code == 503
                data = response.json()
                assert data["status"] == "degraded"
        finally:
            db_core.DB_READY = original
