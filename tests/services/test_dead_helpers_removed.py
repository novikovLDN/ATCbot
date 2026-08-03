"""Точечно мёртвые функции удалены и не должны вернуться.

У каждой был ноль вызывающих по всему дереву (проверено поиском по .py, .sql
и по дашборду на TypeScript), и каждая при этом выглядела рабочей частью
API своего модуля — то есть новый код построили бы на ни разу не проверенной
ветке.

Что удалено и почему:

• database/reconciliation.py:_bulk_fetch_panel_expires_at — пакетный опрос
  Remnawave по списку кандидатов с семафором. Рядом живёт одиночная
  _fetch_panel_expires_at (её зовут get_reconciliation_detail и
  apply_reconciliation_fix), поэтому пакетная выглядела «оптимизированной
  версией для нового экрана сверки». Вместе с ней ушла константа
  _PANEL_FETCH_CONCURRENCY — её читала только она.

• config.py:get_biz_price_stars — цена бизнес-тарифа в Stars. Соседняя
  get_biz_price живая (payments/callbacks.py:426, :927), эта — нет. Плюс она
  читала TARIFFS_STARS, объявленный НИЖЕ по файлу: не падало только потому,
  что вызова не было ни одного.

• app/services/site_sync.py:check_balance, get_user_status, periodic_sync и
  приватный _get. periodic_sync — дословный дубль тела воркера
  app/workers/site_sync_worker.py (is_enabled + sync_balance +
  sync_referrals); две копии одного цикла разъезжаются, и правку внесли бы в
  ту, что не выполняется. check_balance/get_user_status — читающие обёртки:
  бот сам источник правды по подписке и балансу, состояние с сайта он не
  читает. _get остался без единственных потребителей.

• app/core/pool_monitor.py:get_last_pool_wait_spike_monotonic и глобал
  _last_pool_wait_spike_monotonic — «время последнего всплеска ожидания пула
  для вотчдога». Вотчдог его не читал никогда, переменная только писалась.
  Всплески по-прежнему видны в логах (WARNING > 1 с, CRITICAL > 5 с).

Не трогали (файлы вне этой правки, решение владельца):
app/core/system_state.py:create_default_system_state,
app/services/remnawave_api.py:reset_user_traffic,
app/services/activation/service.py:is_activation_allowed,
app/handlers/callbacks/admin_callbacks.py.
"""
import pytest

REMOVED = [
    ("database.reconciliation", "_bulk_fetch_panel_expires_at"),
    ("database.reconciliation", "_PANEL_FETCH_CONCURRENCY"),
    ("config", "get_biz_price_stars"),
    ("app.services.site_sync", "check_balance"),
    ("app.services.site_sync", "get_user_status"),
    ("app.services.site_sync", "periodic_sync"),
    ("app.services.site_sync", "_get"),
    ("app.core.pool_monitor", "get_last_pool_wait_spike_monotonic"),
    ("app.core.pool_monitor", "_last_pool_wait_spike_monotonic"),
]

STILL_ALIVE = [
    ("database.reconciliation", "_fetch_panel_expires_at"),
    ("config", "get_biz_price"),
    ("app.services.site_sync", "sync_balance"),
    ("app.services.site_sync", "sync_referrals"),
    ("app.services.site_sync", "notify_subscription_extend"),
    ("app.services.site_sync", "_post"),
    ("app.core.pool_monitor", "acquire_connection"),
]


@pytest.mark.parametrize("module_name,attr", REMOVED)
def test_dead_helper_stays_removed(module_name, attr):
    module = __import__(module_name, fromlist=["_"])
    assert not hasattr(module, attr), (
        f"{module_name}.{attr} вернулся — снова мёртвый код, "
        f"который читается как рабочий API"
    )


@pytest.mark.parametrize("module_name,attr", STILL_ALIVE)
def test_live_neighbours_untouched(module_name, attr):
    """Соседи по файлу живые — чистка не должна была их задеть."""
    module = __import__(module_name, fromlist=["_"])
    assert hasattr(module, attr), f"{module_name}.{attr} снесли по ошибке"


def test_site_sync_worker_still_reachable():
    """Периодическую синхронизацию делает воркер, а не удалённая periodic_sync."""
    import app.workers.site_sync_worker as worker

    assert worker.sync_balance is not None
    assert worker.sync_referrals is not None
