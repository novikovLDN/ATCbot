"""#22 (docs/notifications/matrix.md): the dashboard lists only notifications
the bot sends, and no reminder window the bot does not apply."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.automated_notifications import helper
from app.services.automated_notifications.registry import (
    FIXED_WINDOW_KEYS, REGISTRY, RETIRED_KEYS, WINDOW_FIELDS,
)


def test_never_sent_keys_are_not_registered():
    assert RETIRED_KEYS == {"trial.reminder_6h", "subscription.reminder_24h"}
    assert not RETIRED_KEYS & set(REGISTRY)


def test_paid_reminders_offer_no_window_the_code_does_not_apply():
    for key in FIXED_WINDOW_KEYS:
        assert key in REGISTRY
        assert not set(REGISTRY[key].default_trigger) & set(WINDOW_FIELDS), key
    # the trial windows ARE applied (trial_notifications reads them) — kept
    assert REGISTRY["trial.reminder_24h"].default_trigger == {"before_expiry_hours": 24, "tolerance_hours": 1}


class _Conn:
    def __init__(self):
        self.executed = []

    async def fetchrow(self, sql, *args):
        return {"trigger_config": {}}

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))
        return "UPDATE 1"


async def test_sync_removes_retired_rows_and_stale_windows(monkeypatch):
    conn = _Conn()

    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(self_inner):
                    return conn

                async def __aexit__(self_inner, *exc):
                    return False
            return _A()

    monkeypatch.setattr(helper, "get_pool", AsyncMock(return_value=_Pool()))
    await helper.sync_registry_to_db()
    deletes = [a for s, a in conn.executed if s.startswith("DELETE FROM automated_notifications")]
    assert deletes and set(deletes[0][0]) == set(RETIRED_KEYS)
    strips = [a for s, a in conn.executed if "trigger_config = COALESCE(trigger_config" in s]
    assert strips and set(strips[0][0]) == set(FIXED_WINDOW_KEYS) and list(strips[0][1]) == list(WINDOW_FIELDS)


@pytest.mark.parametrize("key,kept", [
    ("subscription.reminder_7d", {"segment_filter": "paid"}),
    ("trial.reminder_24h", {"segment_filter": "paid", "before_expiry_hours": 20, "tolerance_hours": 1}),
])
async def test_dashboard_patch_drops_windows_of_paid_reminders(monkeypatch, key, kept):
    from app.api.dashboard.routes import automated_notifications as route
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(route, "update_notification", update)
    payload = route.UpdatePayload(trigger_config={"segment_filter": "paid", "before_expiry_hours": 20,
                                                  "tolerance_hours": 1})
    try:
        await route.patch_notification(payload, key=key, admin={"sub": "1"})
    except Exception:
        pass           # the response part after the update is not under test
    assert update.await_args.kwargs["trigger_config"] == kept
