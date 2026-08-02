"""Админский перевыпуск ключа подписки.

Дефект: функция вызывала vpn_utils.reissue_vpn_access, который внутри
обращается к add_vless_user. После снятия samopis xray эта функция стала
заглушкой и возвращает пустой vless_url, а следом стоит проверка «пустая
ссылка — ошибка». Перевыпуск падал гарантированно, при любом состоянии
системы.
"""
import inspect

import database.subscriptions as subs


def test_reissue_goes_through_remnawave():
    src = inspect.getsource(subs.reissue_subscription_key)
    assert "reissue_premium_user_entity" in src, (
        "перевыпуск обязан идти через панель, а не через заглушку vpn_utils"
    )


def test_reissue_does_not_call_stub():
    src = inspect.getsource(subs.reissue_subscription_key)
    code = [ln for ln in src.split("\n")
            if "vpn_utils.reissue_vpn_access" in ln and not ln.lstrip().startswith("#")]
    assert not code, "вызов заглушки гарантированно бросает ошибку"


def test_empty_subscription_url_is_an_error():
    """Пустая ссылка означает, что панель ничего не выдала — это отказ."""
    src = inspect.getsource(subs.reissue_subscription_key)
    assert "subscription_url" in src
    assert "RuntimeError" in src


def test_panel_uuid_wins_over_generated():
    """Идентификатор сущности берётся из панели: она источник истины."""
    src = inspect.getsource(subs.reissue_subscription_key)
    assert "result.panel_uuid" in src


def test_stub_reissue_still_reports_failure_clearly():
    src = inspect.getsource(subs.reissue_subscription_key)
    assert "REMNAWAVE_REISSUE_FAILED" in src
