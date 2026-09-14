"""#21 (docs/notifications/matrix.md): every date in a user text is Moscow time.
A subscription ending at 21:30 UTC ends at 00:30 MSK the next day — the UTC
date told the user a day too early."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.payments import success_message
from app.utils.date_utils import format_date_msk

LATE_UTC = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("dt,text", [
    (LATE_UTC, "21.10.2026"),
    (datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc), "20.10.2026"),
    (datetime(2026, 10, 20, 21, 30), "21.10.2026"),          # naive = UTC (DB contract)
])
def test_format_date_msk(dt, text):
    assert format_date_msk(dt) == text


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("is_renewal", [False, True])
async def test_purchase_success_date_is_moscow_time(monkeypatch, lang, is_renewal):
    monkeypatch.setattr(success_message, "_admin_override", _none)
    text, _kb = await success_message.build_purchase_success(
        lang, subscription_type="basic", is_combo=False, period_days=30,
        expires_at=LATE_UTC, is_renewal=is_renewal)
    assert "21.10.2026" in text and "20.10.2026" not in text


async def _none(*_a, **_k):
    return None


def test_auto_renewal_message_date_is_moscow_time():
    import inspect

    import auto_renewal
    src = inspect.getsource(auto_renewal)
    assert 'strftime("%d.%m.%Y")' not in src, "auto-renewal «до ДД.ММ.ГГГГ» must be MSK"
    assert auto_renewal.format_date_msk(LATE_UTC) == "21.10.2026"
