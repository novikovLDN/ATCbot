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
import inspect

import database.subscriptions as subs


def _src(fn):
    return inspect.getsource(fn)


class TestFinalizePurchaseCompensation:
    def test_deletes_through_panel_api(self):
        src = _src(subs._finalize_purchase_locked)
        assert "remnawave_api.delete_user" in src, (
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
        src = _src(subs._finalize_purchase_locked)
        assert "ORPHAN_PREVENTED_REMOVAL_FAILED" in src
        assert "вручную" in src, "нужно явно сказать, что делать при сбое удаления"


class TestReissueCompensationUnchanged:
    """Перевыпуск уже удалял правильно — проверяем, что не сломали."""

    def test_still_deletes(self):
        assert "remnawave_api.delete_user" in _src(subs.reissue_vpn_key_atomic)
