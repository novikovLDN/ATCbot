"""Событие payment:approved и доставка уведомлений админу.

Два связанных дефекта:

1. bus.publish({'type': 'payment:approved'}) стоял единственный раз — внутри
   approve_payment_atomic, у которой нет ни одного вызывающего. Реальные
   оплаты идут через finalize_purchase и в шину не писали ничего: живая
   лента оплат в дашборде была пустой всегда, milestone-push не приходил
   никогда.

2. admin_notifier._send отправлял только браузерный web-push, хотя секция
   настроек называлась «Telegram DM · Что присылать в личку». Push молча
   не доставляется, когда подписок ноль или когда админ на iOS без
   установки на «экран Домой» — админ не узнавал ни об ошибке платежа, ни
   о завершении рассылки.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestPaymentApprovedEvent:
    def test_publish_helper_emits_expected_payload(self, monkeypatch):
        from database import subscriptions as subs
        from app import events

        published = []
        monkeypatch.setattr(events.bus, "publish", published.append)

        subs._publish_payment_approved(
            {
                "success": True, "payment_id": 11, "telegram_id": 777,
                "tariff_type": "plus", "is_renewal": True, "expires_at": None,
            },
            purchase_id="p-1", amount_rubles=499.0, payment_provider="platega",
        )

        assert len(published) == 1
        e = published[0]
        assert e["type"] == "payment:approved"
        assert e["telegram_id"] == 777
        assert e["amount_rubles"] == 499.0
        assert e["tariff"] == "plus"
        assert e["is_renewal"] is True
        assert e["provider"] == "platega"

    def test_failed_purchase_is_not_published(self, monkeypatch):
        from database import subscriptions as subs
        from app import events

        published = []
        monkeypatch.setattr(events.bus, "publish", published.append)

        subs._publish_payment_approved(
            {"success": False}, purchase_id="p-2",
            amount_rubles=1.0, payment_provider="lava",
        )
        subs._publish_payment_approved(
            None, purchase_id="p-3", amount_rubles=1.0, payment_provider="lava",
        )
        assert published == []

    def test_bus_failure_does_not_raise(self, monkeypatch):
        """Деньги уже приняты и записаны — падение шины не должно всплыть."""
        from database import subscriptions as subs
        from app import events

        def boom(_e):
            raise RuntimeError("bus is down")

        monkeypatch.setattr(events.bus, "publish", boom)
        subs._publish_payment_approved(
            {"success": True, "payment_id": 1, "telegram_id": 2},
            purchase_id="p-4", amount_rubles=1.0, payment_provider="lava",
        )

    def test_finalize_publishes_after_lock_released(self):
        """Публикация обязана стоять в обёртке finalize_purchase, а не в теле
        под локом: там транзакция ещё не закоммичена."""
        from pathlib import Path

        # finalize_purchase и её тело под локом переехали в
        # database/purchase_finalization.py; в subscriptions.py остался фасад.
        src = Path("database/purchase_finalization.py").read_text(encoding="utf-8")
        wrapper = src[src.index("async def finalize_purchase("):
                     src.index("async def _finalize_purchase_locked(")]
        assert "_publish_payment_approved(" in wrapper


class TestAdminNotifierFallback:
    @pytest.mark.asyncio
    async def test_telegram_dm_when_push_reaches_nobody(self, monkeypatch):
        from app.services import admin_notifier

        monkeypatch.setattr(
            "app.services.push_notifications.send_to_all",
            AsyncMock(return_value={"sent": 0, "failed": 0, "total": 0}),
            raising=False,
        )
        monkeypatch.setattr(admin_notifier.config, "ADMIN_TELEGRAM_ID", 42, raising=False)

        bot = MagicMock()
        bot.send_message = AsyncMock()

        await admin_notifier._send(
            bot, title="⚠️ Ошибка платежа", body="Platega · webhook",
            tag="payment_error:1", url="https://x/dashboard/payments",
        )

        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.args[0] == 42
        assert "Ошибка платежа" in bot.send_message.await_args.args[1]

    @pytest.mark.asyncio
    async def test_no_duplicate_dm_when_push_delivered(self, monkeypatch):
        from app.services import admin_notifier

        monkeypatch.setattr(
            "app.services.push_notifications.send_to_all",
            AsyncMock(return_value={"sent": 2, "failed": 0, "total": 2}),
            raising=False,
        )
        monkeypatch.setattr(admin_notifier.config, "ADMIN_TELEGRAM_ID", 42, raising=False)

        bot = MagicMock()
        bot.send_message = AsyncMock()

        await admin_notifier._send(
            bot, title="t", body="b", tag="tag", url="u",
        )

        assert not bot.send_message.called, "дубль: push дошёл, а DM всё равно ушёл"

    @pytest.mark.asyncio
    async def test_push_exception_falls_back_to_dm(self, monkeypatch):
        from app.services import admin_notifier

        monkeypatch.setattr(
            "app.services.push_notifications.send_to_all",
            AsyncMock(side_effect=RuntimeError("vapid keys missing")),
            raising=False,
        )
        monkeypatch.setattr(admin_notifier.config, "ADMIN_TELEGRAM_ID", 42, raising=False)

        bot = MagicMock()
        bot.send_message = AsyncMock()

        await admin_notifier._send(bot, title="t", body="b", tag="tag", url="u")
        bot.send_message.assert_awaited_once()
