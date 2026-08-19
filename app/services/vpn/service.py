"""No-op shim после смерти samopis-мастера (cutover 2026-08).

Сохраняет публичное API для legacy call-sites — `fast_expiry_cleanup`
и `logging_helpers` — которые ещё импортят из этого модуля. Реальная
работа с VPN entities перенесена в `remnawave_api` / `remnawave_bypass`
/ `remnawave_premium`. Все методы здесь — no-op:
  - `remove_uuid_if_needed` → False (caller продолжит DB-cleanup)
  - `is_vpn_api_available` → False (сообщает вызывающему что API OFF)
  - `VPNRemovalError` → generic Exception для backward-compat
"""


class VPNServiceError(Exception):
    """Legacy exception сохранена для обратной совместимости."""


class VPNRemovalError(VPNServiceError):
    """Legacy — реальные VPN-операции ушли в remnawave_*."""


def is_vpn_api_available() -> bool:
    """Legacy samopis Xray-API мёртв — всегда False."""
    return False


async def remove_uuid_if_needed(
    uuid: str,
    subscription_status: str = "active",
    subscription_expired: bool = True,
) -> bool:
    """No-op. Реальная очистка entities — через remnawave_api.delete_user
    в admin_delete_user_complete / reissue_vpn_key_atomic. Cleanup БД
    выполняется в caller ветке 'DB_UPDATE_SUCCESS' независимо от нашего
    возврата.
    """
    return False
