"""Усыновлённая сущность в панели не должна остаться со старой датой.

ЧТО ПРОИСХОДИТ

    Человек платит. В базе remnawave_premium_uuid пуст — так бывает
    после прерванной выдачи или у старых записей. Бот идёт создавать
    сущность в панели, панель отвечает «такая уже есть», бот её
    усыновляет и патчит expireAt на оплаченную дату.

ЧТО ЛОМАЛОСЬ

    Если PATCH не проходил (5xx, таймаут), _ensure_premium_entity_state
    писала CRITICAL в лог и возвращала False — а результат никто не
    читал. Наверх уходил успех: база записывала новую дату окончания,
    бот отправлял ключ, человек считал себя оплаченным.

    В панели при этом оставалась СТАРАЯ дата. Доступ отключался на ней —
    через день, неделю, когда угодно, — и связать это с оплатой было
    невозможно ни человеку, ни поддержке.

ЧТО ПРОВЕРЯЕМ

    Признак успешности PATCH едет с результатом (state_synced) и
    provision_subscription на нём останавливается, чтобы сработал
    существующий retry-цикл в grant_access.
"""
from datetime import datetime, timezone

import pytest

from app.services import remnawave_premium as rp


EXPIRE = datetime(2027, 1, 1, tzinfo=timezone.utc)
EXISTING = {
    "uuid": "11111111-2222-3333-4444-555555555555",
    "username": "tg_777_premium",
    "telegramId": 777,
    "subscriptionUrl": "https://panel/sub/abc",
    "shortUuid": "abc",
}


def test_fresh_entity_is_synced_by_default():
    """Свежесозданная сущность рождается с нужной датой — патчить нечего."""
    result = rp.PremiumCreateResult(
        ok=True, panel_uuid="u", forced_uuid_accepted=True,
        subscription_url=None, status=201, error=None,
    )
    assert result.state_synced is True


@pytest.mark.asyncio
@pytest.mark.parametrize("patch_ok", [True, False])
async def test_adopt_reports_whether_patch_landed(monkeypatch, patch_ok):
    """Результат PATCH обязан доехать до вызывающего."""
    async def _find(_username):
        return EXISTING

    async def _update(_uuid, **_fields):
        return {"ok": True} if patch_ok else None

    monkeypatch.setattr(rp.remnawave_api, "find_user_by_username", _find)
    monkeypatch.setattr(rp.remnawave_api, "update_user", _update)
    # Без панели функция выходит на первой же строке — тест проверял бы
    # заглушку вместо ветки усыновления.
    monkeypatch.setattr(rp.config, "REMNAWAVE_ENABLED", True)

    result = await rp.create_premium_user_entity(
        777, requested_uuid=None, expire_at=EXPIRE, description="test",
    )
    assert result.ok is True, "усыновление само по себе удалось"
    assert result.recovered is True
    assert result.state_synced is patch_ok


@pytest.mark.asyncio
async def test_provision_stops_when_expiry_was_not_applied(monkeypatch):
    """Иначе человек платит, база двигает дату, а доступ живёт по старой."""
    from app.services import purchase_flow

    # purchase_flow импортирует database лениво внутри функции, поэтому
    # подменяем атрибут на самом модуле database.
    import database

    async def _no_premium_uuid(_tg):
        return None

    monkeypatch.setattr(database, "get_remnawave_premium_uuid", _no_premium_uuid)
    monkeypatch.setattr(purchase_flow.config, "REMNAWAVE_ENABLED", True)

    async def _create(*_a, **_kw):
        return rp.PremiumCreateResult(
            ok=True, panel_uuid="u", forced_uuid_accepted=False,
            subscription_url="https://panel/sub/abc", status=200, error=None,
            recovered=True, state_synced=False,
        )

    monkeypatch.setattr(
        purchase_flow.remnawave_premium, "create_premium_user_entity", _create,
    )

    with pytest.raises(RuntimeError, match="expireAt not applied"):
        await purchase_flow.provision_subscription(
            777, tariff="basic", subscription_end=EXPIRE, period_days=30,
        )


def test_grant_access_retries_on_this_error():
    """Смысл исключения — попасть в существующий retry, а не уронить всё:
    моргнувшая панель на второй попытке ответит нормально."""
    import inspect
    import database.subscriptions as subs

    src = inspect.getsource(subs.grant_access)
    block = src[src.index("MAX_VPN_RETRIES = 2"):]
    assert "provision_subscription" in block, "провижининг вне retry-цикла"
    assert "except Exception as e:" in block, "исключение не перехватывается циклом"
