"""Hermetic aiogram dispatch helper for handler-routing tests.

Feeds an Update through the REAL root router (app.handlers.router, same
include order as production) with a fake Bot that records API calls instead
of hitting Telegram.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update

USER_ID = 42


class FakeBot(Bot):
    def __init__(self):
        super().__init__(token="42:TEST")
        self.calls: list = []

    async def __call__(self, method, request_timeout=None):  # no network
        self.calls.append(method)
        return None


def make_dispatcher() -> Dispatcher:
    from app.handlers import router as root_router
    root_router._parent_router = None      # allow re-attaching across tests
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(root_router)
    return dp


def detach() -> None:
    from app.handlers import router as root_router
    root_router._parent_router = None


def message_update(**content) -> Update:
    msg = {
        "message_id": 1,
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": USER_ID, "type": "private"},
        "from": {"id": USER_ID, "is_bot": False, "first_name": "U"},
    }
    msg.update(content)
    return Update.model_validate({"update_id": 1, "message": msg})


def find_handler(router, callback_name: str):
    """HandlerObject of the message handler named callback_name (searched recursively)."""
    for h in router.message.handlers:
        if getattr(h.callback, "__name__", "") == callback_name:
            return h
    for sub in router.sub_routers:
        found = find_handler(sub, callback_name)
        if found is not None:
            return found
    return None


PAID = {"currency": "RUB", "total_amount": 19900, "invoice_payload": "purchase:p1",
        "telegram_payment_charge_id": "c1", "provider_payment_charge_id": "pc1"}
REFUNDED = {"currency": "XTR", "total_amount": 100, "invoice_payload": "purchase:p1",
            "telegram_payment_charge_id": "c1"}
