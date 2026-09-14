"""
Regression: create_user не должен падать на коллизии referral_code.

Прод-инцидент 2026-09: UniqueViolationError idx_users_referral_code при /start
роняла регистрацию, т.к. ON CONFLICT (telegram_id) не гасит конфликт по
referral_code. Теперь код регенерируется и INSERT повторяется.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from database import users as db_users


class _Conn:
    def __init__(self, fetchval):
        self.fetchval = fetchval
        self.execute = AsyncMock(return_value="UPDATE 0")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


def _uv(msg):
    return asyncpg.exceptions.UniqueViolationError(msg)


@pytest.mark.asyncio
async def test_create_user_retries_on_refcode_collision():
    # 1-й INSERT → коллизия referral_code, 2-й → успех (вернул telegram_id).
    fetchval = AsyncMock(side_effect=[
        _uv('duplicate key value violates unique constraint "idx_users_referral_code"'),
        42,
    ])
    conn = _Conn(fetchval)
    with patch.object(db_users, "get_pool", AsyncMock(return_value=_Pool(conn))):
        await db_users.create_user(42, "user", "ru")  # не должно кинуть
    assert fetchval.await_count == 2
    # UPDATE-бэкфилл вызвался с НЕ-None кодом
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_create_user_reraises_non_refcode_unique_violation():
    # Коллизия по ДРУГОМУ constraint — не глотаем, пробрасываем.
    fetchval = AsyncMock(side_effect=_uv(
        'duplicate key value violates unique constraint "users_pkey"'))
    conn = _Conn(fetchval)
    with patch.object(db_users, "get_pool", AsyncMock(return_value=_Pool(conn))):
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await db_users.create_user(7, "u", "ru")


@pytest.mark.asyncio
async def test_create_user_exhausts_then_inserts_without_code():
    # 6 коллизий подряд → 7-й INSERT без referral_code (успех).
    seq = [_uv('unique constraint "idx_users_referral_code"') for _ in range(6)]
    seq.append(99)  # финальный INSERT без кода
    fetchval = AsyncMock(side_effect=seq)
    conn = _Conn(fetchval)
    with patch.object(db_users, "get_pool", AsyncMock(return_value=_Pool(conn))):
        await db_users.create_user(99, "u", "ru")
    assert fetchval.await_count == 7
    # UPDATE-бэкфилл НЕ вызывается (referral_code=None)
    conn.execute.assert_not_awaited()
