"""vpn_utils — no-op shim после смерти samopis-мастера (cutover 2026-08).

Все реальные VPN-операции перенесены в:
  - app.services.remnawave_api       (низкоуровневый HTTP клиент)
  - app.services.remnawave_bypass    (bypass tier)
  - app.services.remnawave_premium   (premium tier)
  - app.services.remnawave_service   (высокоуровневые helpers)
  - app.services.purchase_flow       (create/renew оркестрация)

Модуль оставлен только чтобы существующие импорты в 9 файлах не
сломались. Все функции no-op, публичное API сохранено для BC:
  - build_sub_url / generate_sub_token — единственные РЕАЛЬНЫЕ:
    используются в user_subscription_links / handlers для генерации
    fallback subscription URL (legacy `/api/sub/{token}?id=...`).
  - Остальное (add_vless_user, remove_vless_user, reissue_vpn_access,
    check_xray_health, ensure_user_in_xray, ...) — no-op stubs.

Callers должны мигрировать на remnawave_* и удалить импорт этого
модуля. Планово — следующая волна cleanup.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import config

logger = logging.getLogger(__name__)


# ── Exceptions (сохранены для callers, что ловят конкретные типы) ────

class VPNAPIError(Exception):
    """Legacy — реальные ошибки провижена приходят из remnawave_api."""


class VPNTimeoutError(VPNAPIError):
    pass


class AuthError(VPNAPIError):
    pass


class InvalidResponseError(VPNAPIError):
    pass


class CriticalUUIDMismatchError(VPNAPIError):
    pass


# ── No-op stubs (samopis мёртв, всё в remnawave_*) ───────────────────

async def check_xray_health() -> bool:
    """Legacy samopis /health — всегда False."""
    return False


async def add_vless_user(
    telegram_id: int,
    subscription_end: datetime,
    tariff: str = "basic",
    period_days: int = 30,
    force_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """No-op. Real provisioning: purchase_flow.provision_subscription."""
    import uuid as _u
    stub_uuid = force_uuid or str(_u.uuid4())
    logger.debug(
        "vpn_utils.add_vless_user NO-OP: tg=%s (use purchase_flow.provision_subscription)",
        telegram_id,
    )
    return {"uuid": stub_uuid, "vless_link": "", "vless_link_plus": None, "tariff": tariff}


async def upgrade_vless_user(uuid: str) -> Dict[str, str]:
    """No-op. Real upgrade: remnawave_premium.update_user."""
    return {"uuid": uuid, "vless_link_plus": ""}


async def remove_plus_inbound(uuid: str) -> bool:
    """No-op. Real remove: remnawave_api.update_user."""
    return False


async def ensure_user_in_xray(
    telegram_id: int,
    uuid: Optional[str],
    subscription_end: datetime,
    tariff: str = "basic",
) -> Optional[str]:
    """No-op. Real sync: purchase_flow.sync_renewal_to_remnawave."""
    return None


async def update_vless_user(uuid: str, subscription_end: datetime) -> None:
    """No-op. Real update: remnawave_api.update_user(id, expireAt=...)."""
    return None


async def remove_vless_user(uuid: str) -> None:
    """No-op. Real delete: remnawave_api.delete_user(id)."""
    return None


async def safe_remove_vless_user_with_retry(
    uuid: str,
    *,
    max_retries: int = 3,
) -> None:
    """No-op orphan-prevention wrapper. Реальный orphan-cleanup
    делается в admin_delete_user_complete через remnawave_api.delete_user."""
    return None


async def reissue_vpn_access(
    old_uuid: str,
    telegram_id: int,
    subscription_end: datetime,
) -> Tuple[str, str]:
    """No-op. Real reissue: reissue_vpn_key_atomic →
    remnawave_premium.reissue_premium_user_entity."""
    raise VPNAPIError(
        "vpn_utils.reissue_vpn_access is deprecated; "
        "use database.subscriptions.reissue_vpn_key_atomic (Remnawave path)."
    )


# ── REAL: sub-token / sub-URL для fallback subscription page ────────

def generate_sub_token(bot_token: str, telegram_id: int) -> str:
    """HMAC-SHA256(bot_token, str(telegram_id)) → base64url → 32 chars.
    Identical to Node.js mini-app impl. Реально используется —
    fallback URL для юзеров без Remnawave-sub_url в кеше."""
    import hmac
    import hashlib
    import base64
    signature = hmac.new(
        bot_token.encode(),
        str(telegram_id).encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode()[:32]


def build_sub_url(telegram_id: int) -> str:
    """https://{SUB_BASE_URL}/api/sub/{token}?id={telegram_id}."""
    token = generate_sub_token(config.BOT_TOKEN, telegram_id)
    return f"{config.SUB_BASE_URL}/api/sub/{token}?id={telegram_id}"


__all__ = [
    "VPNAPIError", "VPNTimeoutError", "AuthError",
    "InvalidResponseError", "CriticalUUIDMismatchError",
    "check_xray_health", "add_vless_user", "upgrade_vless_user",
    "remove_plus_inbound", "ensure_user_in_xray", "update_vless_user",
    "remove_vless_user", "safe_remove_vless_user_with_retry",
    "reissue_vpn_access", "generate_sub_token", "build_sub_url",
]
