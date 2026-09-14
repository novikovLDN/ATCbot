"""
Tests for the «🎁 Получить пробный ключ» broadcast button handler.

Проверяем:
  • первый клик → grant_access(+1 день) + доставка обхода до baseline+1ГБ
    (новичок с 500МБ trial-дефолта получает ровно 1ГБ: delta=0.5ГБ);
  • существующий юзер (+1ГБ сверху);
  • повторный клик в той же рассылке → «Подарок уже получен», без выдачи;
  • сбой выдачи после claim → release_broadcast_trial_key (не жжём попытку).
"""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.handlers.callbacks import broadcast_trial_key as mod

ONE_GB = 1024 ** 3
HALF_GB = ONE_GB // 2


def _make_callback(data="broadcast_trial_key:7", tg=42):
    cb = MagicMock()
    cb.data = data
    cb.from_user = SimpleNamespace(id=tg, username="u", first_name="U")
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.chat = SimpleNamespace(id=tg)
    cb.bot = object()
    return cb


def _enter_common(stack: ExitStack, db, deliver, bytes_seq):
    """Общие патчи + доменные (db, deliver, снапшоты обхода)."""
    stack.enter_context(patch.object(mod, "ensure_db_ready_callback", AsyncMock(return_value=True)))
    stack.enter_context(patch.object(mod, "check_rate_limit", MagicMock(return_value=(True, None))))
    stack.enter_context(patch.object(mod, "resolve_user_language", AsyncMock(return_value="ru")))
    stack.enter_context(patch.object(mod, "_send_device_screen_delayed", AsyncMock(return_value=None)))
    stack.enter_context(patch.object(mod, "database", db))
    stack.enter_context(patch.object(mod, "_deliver_bypass_gb", deliver))
    seq = bytes_seq if isinstance(bytes_seq, list) else None
    if seq is not None:
        stack.enter_context(patch.object(mod, "_current_bypass_bytes", AsyncMock(side_effect=seq)))
    else:
        stack.enter_context(patch.object(mod, "_current_bypass_bytes", AsyncMock(return_value=bytes_seq)))


@pytest.mark.asyncio
async def test_first_click_new_user_delivers_exactly_1gb():
    cb = _make_callback()
    db = MagicMock()
    db.claim_broadcast_trial_key = AsyncMock(return_value=True)
    db.grant_access = AsyncMock(return_value={"subscription_end": "2026-01-01", "action": "new_issuance"})
    db.release_broadcast_trial_key = AsyncMock()
    deliver = AsyncMock(return_value=True)
    # Новичок: до выдачи обхода нет (None), после grant создан trial-bypass 500МБ.
    with ExitStack() as stack:
        _enter_common(stack, db, deliver, [None, HALF_GB])
        await mod.callback_broadcast_trial_key(cb)

    db.claim_broadcast_trial_key.assert_awaited_once_with(7, 42)
    db.grant_access.assert_awaited_once()
    # delta = (0 + 1ГБ) - 0.5ГБ = 0.5ГБ → новичок получает ровно 1ГБ итого.
    deliver.assert_awaited_once_with(42, HALF_GB)
    db.release_broadcast_trial_key.assert_not_awaited()
    cb.message.answer.assert_awaited()  # «Подарок активирован»


@pytest.mark.asyncio
async def test_first_click_existing_user_adds_1gb():
    cb = _make_callback()
    db = MagicMock()
    db.claim_broadcast_trial_key = AsyncMock(return_value=True)
    db.grant_access = AsyncMock(return_value={"subscription_end": "2026-01-01", "action": "renewal"})
    db.release_broadcast_trial_key = AsyncMock()
    deliver = AsyncMock(return_value=True)
    # Существующий: обход 2ГБ до и после (renewal не трогает bypass).
    with ExitStack() as stack:
        _enter_common(stack, db, deliver, [2 * ONE_GB, 2 * ONE_GB])
        await mod.callback_broadcast_trial_key(cb)

    deliver.assert_awaited_once_with(42, ONE_GB)  # ровно +1ГБ сверху


@pytest.mark.asyncio
async def test_repeat_click_same_broadcast_no_grant():
    cb = _make_callback()
    db = MagicMock()
    db.claim_broadcast_trial_key = AsyncMock(return_value=False)  # уже забирал
    db.grant_access = AsyncMock()
    db.release_broadcast_trial_key = AsyncMock()
    deliver = AsyncMock()

    with ExitStack() as stack:
        _enter_common(stack, db, deliver, None)
        await mod.callback_broadcast_trial_key(cb)

    db.grant_access.assert_not_awaited()
    deliver.assert_not_awaited()
    cb.answer.assert_awaited()  # toast «уже получен»


@pytest.mark.asyncio
async def test_grant_failure_releases_claim():
    cb = _make_callback()
    db = MagicMock()
    db.claim_broadcast_trial_key = AsyncMock(return_value=True)
    db.grant_access = AsyncMock(side_effect=RuntimeError("panel down"))
    db.release_broadcast_trial_key = AsyncMock()
    deliver = AsyncMock()

    with ExitStack() as stack:
        _enter_common(stack, db, deliver, None)
        await mod.callback_broadcast_trial_key(cb)

    db.release_broadcast_trial_key.assert_awaited_once_with(7, 42)


@pytest.mark.asyncio
async def test_missing_broadcast_id_uses_zero_bucket():
    cb = _make_callback(data="broadcast_trial_key:")
    db = MagicMock()
    db.claim_broadcast_trial_key = AsyncMock(return_value=True)
    db.grant_access = AsyncMock(return_value={"subscription_end": "x", "action": "renewal"})
    db.release_broadcast_trial_key = AsyncMock()

    with ExitStack() as stack:
        _enter_common(stack, db, AsyncMock(return_value=True), ONE_GB)
        await mod.callback_broadcast_trial_key(cb)

    db.claim_broadcast_trial_key.assert_awaited_once_with(0, 42)
