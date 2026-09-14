"""main.py registered the Telegram webhook with drop_pending_updates=True on EVERY
start: updates queued while the process was restarting / deploying — including
successful_payment (money already taken) — were thrown away: nothing granted,
no admin alert, and Telegram never resends a dropped update.

Telegram keeps undelivered updates for 24 h and delivers them once the webhook is
set again; the handlers are idempotent (payment: purchase_id / charge id).
"""
from __future__ import annotations

import ast
from pathlib import Path

import tests.conftest  # noqa: F401  (env before config)
import main


def test_set_webhook_keeps_pending_updates():
    src = Path(main.__file__).read_text(encoding="utf-8")
    calls = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "set_webhook"
    ]
    assert calls, "main.py must register the webhook"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        value = kw.get("drop_pending_updates")
        assert isinstance(value, ast.Constant) and value.value is False, (
            "set_webhook must keep the updates queued during a restart (drop_pending_updates=False)"
        )
