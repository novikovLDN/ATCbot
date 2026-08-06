"""Удаление сироты при откате транзакции выдачи.

Дефект: компенсация вызывала vpn_utils.safe_remove_vless_user_with_retry.
После снятия samopis xray это заглушка — она ничего не удаляла, лог рапортовал
ORPHAN_PREVENTED, а сущность продолжала жить в панели. Пользователь не платил,
но доступ у него оставался.

Класс TestApprovePaymentCompensation удалён вместе с approve_payment_atomic:
та функция была мёртвой веткой ручной модерации платежей без единого
вызывающего. Живой путь — _finalize_purchase_locked — проверяется ниже,
инвариант тот же.
"""
import ast
import inspect
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import database.subscriptions as subs

REPO_ROOT = Path(__file__).resolve().parents[2]

# uuid ПОДКЛЮЧЕНИЯ (subscriptions.uuid, он же vlessUuid) и внутренний uuid
# панели — разные значения. Путать их и значит делать компенсацию мнимой:
# DELETE /api/users/{uuid} по первому отвечает 404.
CONNECTION_UUID = "11111111-1111-4111-8111-111111111111"
PANEL_UUID = "22222222-2222-4222-8222-222222222222"


def _src(fn):
    return inspect.getsource(fn)


class TestFinalizePurchaseCompensation:
    def test_deletes_through_panel_api(self):
        """delete_user зовётся не напрямую, а через orphan_cleanup.

        Прямой вызов здесь был бы мнимой компенсацией второго рода: на руках
        у финализации uuid ПОДКЛЮЧЕНИЯ (vlessUuid), а DELETE /api/users/{uuid}
        ждёт внутренний uuid панели и по первому отвечает 404.
        """
        src = _src(subs._finalize_purchase_locked)
        assert "delete_orphan_premium_entity" in src, (
            "компенсация обязана удалять сущность в панели"
        )

    def test_does_not_rely_on_stub(self):
        """Смотрим только блок компенсации — до raise.

        Ниже по функции заглушка вызывается для удаления СТАРОГО uuid после
        успешного коммита, это другая логика и её здесь проверять не нужно.
        """
        src = _src(subs._finalize_purchase_locked)
        block = src[src.index("except Exception as tx_err"):]
        block = block[:block.index("\n            raise")]
        code = [ln for ln in block.split("\n")
                if "safe_remove_vless_user_with_retry" in ln and not ln.lstrip().startswith("#")]
        assert not code, "заглушка ничего не удаляет — компенсация была мнимой"

    def test_reports_manual_cleanup_on_failure(self):
        """Имя записи говорит о последствии, а не о намерении.

        ORPHAN_PREVENTED утверждало, что сироту предотвратили, — при том что
        результат удаления никто не смотрел, а remnawave_api отдаёт None на
        любой отказ и ничего не бросает.
        """
        src = _src(subs._finalize_purchase_locked)
        assert "ORPHAN_NOT_CLEANED" in src
        assert "вручную" in src, "нужно явно сказать, что делать при сбое удаления"
        # Само имя ORPHAN_PREVENTED в логах проверяется отдельно, по AST:
        # в комментариях оно осталось намеренно — объясняет, чего больше нет.


class TestReissueCompensationUnchanged:
    """Перевыпуск уже удалял правильно — проверяем, что не сломали."""

    def test_still_deletes(self):
        assert "remnawave_api.delete_user" in _src(subs.reissue_vpn_key_atomic)

    def test_checks_what_the_panel_answered(self):
        """delete_user отдаёт None и на 404, и на отказ, и на таймаут, ничего
        не бросая. Прежний код писал «сирота предотвращена», не взглянув на
        ответ, — то есть утверждал удаление и когда панель лежала."""
        src = _src(subs.reissue_vpn_key_atomic)
        assert "removed = await remnawave_api.delete_user(" in src, (
            "результат удаления снова никуда не попадает"
        )
        assert "if removed is not None:" in src


# ─────────────────────────────────────────────────────────────────────
# Настоящее удаление: app/services/orphan_cleanup.py
# ─────────────────────────────────────────────────────────────────────

def _panel(monkeypatch, *, found, deleted):
    """Подменяем панель целиком: helper ходит только в эти две функции."""
    from app.services import remnawave_api

    find = AsyncMock(return_value=found)
    delete = AsyncMock(return_value=deleted)
    monkeypatch.setattr(remnawave_api, "find_user_by_username", find)
    monkeypatch.setattr(remnawave_api, "delete_user", delete)
    return find, delete


class TestPanelDeletionHelper:
    """Единственное место, где сироту действительно удаляют."""

    @pytest.mark.asyncio
    async def test_deletes_by_panel_uuid_not_by_connection_uuid(self, monkeypatch):
        """Раньше в delete_user уезжал uuid подключения — панель отвечала 404,
        и компенсация оставалась мнимой, только уже с сетевым вызовом."""
        from app.services.orphan_cleanup import delete_orphan_premium_entity

        _, delete = _panel(
            monkeypatch,
            found={"uuid": PANEL_UUID, "telegramId": 777},
            deleted={"ok": True},
        )
        ok, entity = await delete_orphan_premium_entity(777, CONNECTION_UUID)

        assert ok is True
        assert entity == PANEL_UUID
        delete.assert_awaited_once_with(PANEL_UUID)

    @pytest.mark.asyncio
    async def test_unconfirmed_delete_is_not_success(self, monkeypatch):
        """None — это и 404, и отказ, и таймаут. Различить нельзя, значит
        считать удалённым нельзя: ложное «удалено» закрывает разбор."""
        from app.services.orphan_cleanup import delete_orphan_premium_entity

        _panel(monkeypatch, found={"uuid": PANEL_UUID, "telegramId": 777}, deleted=None)
        ok, entity = await delete_orphan_premium_entity(777, CONNECTION_UUID)

        assert ok is False
        assert entity == PANEL_UUID, "без идентификатора чистить руками нечего"

    @pytest.mark.asyncio
    async def test_panel_silence_is_not_success(self, monkeypatch):
        """Панель не отдала сущность: её нет либо панель недоступна —
        по ответу не различить, поэтому считаем, что сирота осталась."""
        from app.services.orphan_cleanup import delete_orphan_premium_entity

        _, delete = _panel(monkeypatch, found=None, deleted={"ok": True})
        ok, entity = await delete_orphan_premium_entity(777, CONNECTION_UUID)

        assert ok is False
        assert entity == CONNECTION_UUID
        delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_foreign_entity_is_left_alone(self, monkeypatch):
        """Имя сущности мог занять админ вручную. Удалить её значит отобрать
        доступ у постороннего человека."""
        from app.services.orphan_cleanup import delete_orphan_premium_entity

        _, delete = _panel(
            monkeypatch,
            found={"uuid": PANEL_UUID, "telegramId": 999},
            deleted={"ok": True},
        )
        ok, _entity = await delete_orphan_premium_entity(777, CONNECTION_UUID)

        assert ok is False
        delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch):
        """Компенсация идёт по пути обработки ошибки: исключение отсюда
        подменило бы исходную причину отката."""
        from app.services import remnawave_api
        from app.services.orphan_cleanup import delete_orphan_premium_entity

        monkeypatch.setattr(
            remnawave_api, "find_user_by_username",
            AsyncMock(side_effect=RuntimeError("panel is down")),
        )
        ok, entity = await delete_orphan_premium_entity(777, CONNECTION_UUID)

        assert ok is False
        assert entity == CONNECTION_UUID


# ─────────────────────────────────────────────────────────────────────
# Ни одна компенсация не должна опираться на заглушку xray
# ─────────────────────────────────────────────────────────────────────

# Файл → как в нём зовётся вход в удаление сироты.
_COMPENSATION_SITES = [
    ("database/purchase_finalization.py", "delete_orphan_premium_entity"),
    ("database/balance_purchases.py", "delete_orphan_premium_entity"),
    ("database/admin_access.py", "_cleanup_orphan_after_rollback"),
    ("app/services/activation/service.py", "_cleanup_orphan_entity"),
]


@pytest.mark.parametrize("rel_path,entry_point", _COMPENSATION_SITES)
def test_compensation_does_not_go_through_the_stub(rel_path, entry_point):
    """safe_remove_vless_user_with_retry не удаляет ничего и не бросает.

    Ловим по признаку «заглушке скармливают uuid, созданный фазой 1»: именно
    он и означал, что человек с откатившейся оплатой сохранял рабочий доступ.
    """
    src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    live = [
        ln for ln in src.split("\n")
        if "safe_remove_vless_user_with_retry" in ln
        and "uuid_to_cleanup_on_failure" in ln
        and not ln.lstrip().startswith("#")
    ]
    assert not live, f"{rel_path}: компенсация снова уходит в заглушку: {live}"
    assert entry_point in src, f"{rel_path}: настоящего удаления нет"


# ─────────────────────────────────────────────────────────────────────
# Записи «удалено», под которыми теперь есть (или нет) действие
# ─────────────────────────────────────────────────────────────────────

def _logged(rel_path: str) -> str:
    """Всё, что уходит в logger.*, одной строкой.

    Разбор по AST, а не по тексту файла: имена запрещённых записей
    упоминаются в комментариях и докстрингах — там они объясняют, чего
    больше нет, и мешать этому не должны.
    """
    tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            out.append(ast.unparse(node))
    return "\n".join(out)


# Файл → запись, которой не должно быть в логах → запись вместо неё.
_RENAMED_RECORDS = [
    # Под этими теперь стоит настоящее удаление — имя обязано отражать его
    # результат, а не намерение.
    ("database/purchase_finalization.py", "ORPHAN_PREVENTED", "ORPHAN_DELETED"),
    ("database/subscription_reissue.py", "ORPHAN_PREVENTED", "ORPHAN_DELETED"),
    ("database/admin_access.py", "ORPHAN_PREVENTED", "ORPHAN_DELETED"),
    # А под этими действия по-прежнему нет: заглушка снятого xray.
    ("database/purchase_finalization.py", "OLD_UUID_REMOVED_AFTER_COMMIT",
     "OLD_UUID_REMOVAL_SKIPPED"),
    ("database/balance_purchases.py", "OLD_UUID_REMOVED_AFTER_COMMIT",
     "OLD_UUID_REMOVAL_SKIPPED"),
    ("database/admin_access.py", "OLD_UUID_REMOVED_AFTER_COMMIT",
     "OLD_UUID_REMOVAL_SKIPPED"),
    ("database/admin_access.py", "ADMIN_DELETE_UUID_REMOVED",
     "ADMIN_DELETE_LEGACY_UUID_CLEARED"),
]


@pytest.mark.parametrize("rel_path,forbidden,expected", _RENAMED_RECORDS)
def test_records_say_what_happened(rel_path, forbidden, expected):
    logged = _logged(rel_path)
    assert forbidden not in logged, (
        f"{rel_path}: запись {forbidden} вернулась в лог"
    )
    assert expected in logged, f"{rel_path}: нет записи {expected}"


@pytest.mark.parametrize("rel_path", [
    "database/purchase_finalization.py",
    "database/balance_purchases.py",
    "database/subscription_reissue.py",
    "database/admin_access.py",
    "app/services/activation/service.py",
])
def test_failed_cleanup_names_the_consequence(rel_path):
    """Отказ панели — не «не получилось удалить», а «человек держит
    неоплаченный доступ». По этой записи админ идёт чистить руками."""
    logged = _logged(rel_path)
    assert "NOT_CLEANED" in logged
    assert "вручную" in logged


# ─────────────────────────────────────────────────────────────────────
# Поведение целиком: покупка с баланса, транзакция упала
# ─────────────────────────────────────────────────────────────────────

def _failing_pool():
    """Пул, на котором finalize_balance_purchase падает внутри транзакции.

    fetchrow отдаёт None и на чтение подписки (значит новая выдача, фаза 1
    состоится), и на чтение баланса — вторая None роняет транзакцию.
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=0)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=conn)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    pool = MagicMock()
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = acq
    return pool


async def _run_failing_balance_purchase(monkeypatch):
    import database.balance_purchases as bal
    from app.services import purchase_flow

    monkeypatch.setattr(bal.config, "VPN_ENABLED", True)
    monkeypatch.setattr(bal, "get_pool", AsyncMock(return_value=_failing_pool()))
    monkeypatch.setattr(
        purchase_flow, "provision_subscription",
        AsyncMock(return_value={
            "uuid": CONNECTION_UUID,
            "vless_url": "https://panel/sub/x",
            "vless_url_plus": None,
            "subscription_type": "basic",
        }),
    )
    # Заглушка обязана остаться нетронутой: если компенсация снова уйдёт в
    # неё, это будет видно по вызову.
    stub = AsyncMock()
    monkeypatch.setattr(bal.vpn_utils, "safe_remove_vless_user_with_retry", stub)

    # Ловим именно ту ошибку, которую подстроили: на посторонней тест бы
    # прошёл, не проверив компенсацию.
    with pytest.raises(ValueError, match="not found"):
        await bal.finalize_balance_purchase(
            telegram_id=777, tariff_type="basic", period_days=30, amount_rubles=199.0,
        )
    return stub


@pytest.mark.asyncio
async def test_balance_rollback_deletes_the_entity_in_the_panel(monkeypatch, caplog):
    """Оплата откатилась — платного доступа у человека остаться не должно."""
    _, delete = _panel(
        monkeypatch,
        found={"uuid": PANEL_UUID, "telegramId": 777},
        deleted={"ok": True},
    )
    with caplog.at_level(logging.CRITICAL, logger="database.balance_purchases"):
        stub = await _run_failing_balance_purchase(monkeypatch)

    delete.assert_awaited_once_with(PANEL_UUID)
    stub.assert_not_awaited()
    assert "ORPHAN_DELETED" in caplog.text
    assert "ORPHAN_NOT_CLEANED" not in caplog.text


@pytest.mark.asyncio
async def test_balance_rollback_reports_a_refusal_as_a_refusal(monkeypatch, caplog):
    """Панель не подтвердила удаление — запись обязана сказать именно это."""
    _panel(monkeypatch, found={"uuid": PANEL_UUID, "telegramId": 777}, deleted=None)
    with caplog.at_level(logging.CRITICAL, logger="database.balance_purchases"):
        await _run_failing_balance_purchase(monkeypatch)

    assert "ORPHAN_NOT_CLEANED" in caplog.text
    assert "ORPHAN_DELETED" not in caplog.text
    assert PANEL_UUID[:8] in caplog.text, "нечего дать админу для ручной чистки"


@pytest.mark.asyncio
async def test_activation_rollback_records_the_outcome(monkeypatch, caplog):
    """Тот же инвариант на пути активации отложенной подписки."""
    from app.services.activation import service as act

    _, delete = _panel(
        monkeypatch,
        found={"uuid": PANEL_UUID, "telegramId": 777},
        deleted={"ok": True},
    )
    with caplog.at_level(logging.CRITICAL, logger=act.__name__):
        await act._cleanup_orphan_entity(777, 42, CONNECTION_UUID, reason="state_changed")

    delete.assert_awaited_once_with(PANEL_UUID)
    assert "ACTIVATION_ORPHAN_DELETED" in caplog.text

    _panel(monkeypatch, found={"uuid": PANEL_UUID, "telegramId": 777}, deleted=None)
    caplog.clear()
    with caplog.at_level(logging.CRITICAL, logger=act.__name__):
        await act._cleanup_orphan_entity(777, 42, CONNECTION_UUID, reason="state_changed")

    assert "ACTIVATION_ORPHAN_NOT_CLEANED" in caplog.text
