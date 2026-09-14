"""GET /payments/recent ↔ the «Последние покупки» feed (dashboard/src/components/PaymentsFeed.tsx).

The feed read `price_rubles`, which the endpoint never returns (it passes
through pending_purchases.price_kopecks), so every purchase showed «0 ₽».
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import database
from app.api.dashboard.routes import payments

FEED_TSX = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "components" / "PaymentsFeed.tsx"


async def test_recent_feed_returns_price_in_kopecks(monkeypatch):
    row = {
        "id": 1, "purchase_id": "pp_1", "telegram_id": 42, "tariff": "basic",
        "purchase_type": "subscription", "period_days": 30, "price_kopecks": 19900,
        "status": "paid", "created_at": datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc),
        "promo_code": None, "is_combo": False, "payment_provider": "platega", "username": "u",
    }

    async def feed(**_kw):
        return [row]

    monkeypatch.setattr(database, "get_recent_payments_feed", feed, raising=False)
    body = await payments.payments_recent(limit=20, hours=None, status=None)
    assert body[0]["price_kopecks"] == 19900
    assert "price_rubles" not in body[0]
    assert body[0]["created_at"].startswith("2026-09-14")


def test_feed_component_reads_the_field_the_endpoint_returns():
    src = FEED_TSX.read_text(encoding="utf-8")
    assert "price_kopecks" in src
