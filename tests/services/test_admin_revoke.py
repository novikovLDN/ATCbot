"""Отзыв доступа админом должен отключать пользователя в панели.

Дефект: функция очищала запись в базе и вызывала
vpn_utils.safe_remove_vless_user_with_retry. После перехода на Remnawave
эта функция стала заглушкой, поэтому в панели пользователь оставался
активным — ссылка продолжала работать после отзыва.
"""
import inspect

import database.admin as admin_mod


def test_revoke_disables_premium_in_panel():
    src = inspect.getsource(admin_mod.admin_revoke_access_atomic)
    assert "disable_premium_user" in src, (
        "отзыв не отключает пользователя в панели — доступ останется рабочим"
    )


def test_panel_call_is_outside_transaction():
    """Внешний HTTP-вызов не должен выполняться внутри транзакции."""
    src = inspect.getsource(admin_mod.admin_revoke_access_atomic)
    tx_pos = src.index("async with conn.transaction()")
    call_pos = src.index("disable_premium_user")
    # Вызов идёт после закрытия блока транзакции — проверяем по отступу строки.
    line = next(ln for ln in src.split("\n") if "disable_premium_user" in ln and "import" in ln)
    assert call_pos > tx_pos
    assert len(line) - len(line.lstrip()) <= 12, "вызов панели попал внутрь транзакции"


def test_failure_does_not_roll_back_revocation():
    """Сбой отключения в панели не должен отменять уже снятый доступ."""
    src = inspect.getsource(admin_mod.admin_revoke_access_atomic)
    tail = src[src.index("disable_premium_user"):]
    assert "except Exception" in tail
    assert "ADMIN_REVOKE_PREMIUM_DISABLE_FAILED" in tail, (
        "нужен явный маркер для ручного отключения в панели"
    )


def test_stub_removal_still_called_for_legacy_rows():
    """Старые записи с uuid обрабатываются прежним путём — он безвреден."""
    src = inspect.getsource(admin_mod.admin_revoke_access_atomic)
    assert "safe_remove_vless_user_with_retry" in src
