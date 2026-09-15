"""Dashboard user card: «Обновить ссылки из панели» / «Перевыпустить подписку»
(prod 2026-09-15: after a manual «перевыпуск» in the panel the bot kept serving
the dead cached links). The routes report per entity, write the audit log and
turn a refused panel call into ok=False."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
from app.api.dashboard.routes import users
from app.services import user_subscription_links as links

ADMIN = {"sub": "777"}


@pytest.fixture
def audit(monkeypatch):
    log = AsyncMock()
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", log, raising=False)
    monkeypatch.setattr(users.bus, "publish", MagicMock())
    return log


async def test_refresh_reports_per_entity_and_audits(monkeypatch, audit):
    result = {"premium": "updated", "bypass": "unchanged"}
    monkeypatch.setattr(links, "refresh_cached_sub_urls", AsyncMock(return_value=result))
    out = await users.user_refresh_sub_links(telegram_id=42, admin=ADMIN)
    assert out == {"ok": True, "result": result}
    action, admin_id = audit.await_args.args
    assert (action, admin_id, audit.await_args.kwargs["target_user"]) == ("admin_sub_links_refresh", 777, 42)
    assert json.loads(audit.await_args.kwargs["details"]) == result


async def test_refresh_with_a_panel_error_is_not_ok(monkeypatch, audit):
    monkeypatch.setattr(links, "refresh_cached_sub_urls",
                        AsyncMock(return_value={"premium": "error", "bypass": "updated"}))
    assert (await users.user_refresh_sub_links(telegram_id=42, admin=ADMIN))["ok"] is False


@pytest.mark.parametrize("result,ok", [
    ({"premium": {"revoke": "revoked", "links": "updated"},
      "bypass": {"revoke": "revoked", "links": "updated"}}, True),
    ({"premium": {"revoke": "revoked", "links": "updated"},             # no bypass entity yet
      "bypass": {"revoke": "no_entity", "links": "no_entity"}}, True),
    ({"premium": {"revoke": "error", "links": "unchanged"},             # the panel refused
      "bypass": {"revoke": "revoked", "links": "updated"}}, False),
])
async def test_reissue_is_ok_only_when_the_panel_accepted(monkeypatch, audit, result, ok):
    monkeypatch.setattr(links, "reissue_sub_urls", AsyncMock(return_value=result))
    out = await users.user_reissue_sub_links(telegram_id=42, admin=ADMIN)
    assert out == {"ok": ok, "result": result}
    assert audit.await_args.args[0] == "admin_sub_links_reissue"
