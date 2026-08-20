"""Верификация выдачи после платежа: реально ли применились GB в панели.

После combo/traffic-pack покупок делает GET user из Remnawave и сверяет
`trafficLimitBytes` c ожидаемым. При mismatch — DM админу с деталями,
чтобы можно было расследовать.

Fire-and-forget: verify запускается create_task после basic-flow, ни в
коем случае не блокирует основной путь платежа.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _fmt_gb(byte_val: int) -> str:
    """Bytes → человекочитаемое GB (гибибайт)."""
    return f"{byte_val / (1024 ** 3):.2f} GB"


async def _send_admin_alert(title: str, body: str, tag: str = "delivery") -> None:
    """DM админу. Fail-safe, не бросает.

    tag определяет severity emoji: warning/error → ⚠️, info → ℹ️.
    """
    try:
        from aiogram import Bot
        from app.api import telegram_webhook
        bot: Optional[Bot] = getattr(telegram_webhook, "_bot", None)
        if bot is None or not getattr(config, "ADMIN_TELEGRAM_ID", 0):
            return
        # Info-теги (не ошибка, а информационное уведомление).
        _info_tags = {"topup-unlimited"}
        emoji = "ℹ️" if tag in _info_tags else "⚠️"
        text = f"{emoji} <b>{title}</b>\n{body}"
        await bot.send_message(
            chat_id=config.ADMIN_TELEGRAM_ID,
            text=text[:4000],
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("admin alert send failed: %s", e)


async def verify_bypass_delivery(
    *,
    telegram_id: int,
    provider: str,
    kind: str,               # "combo" | "traffic_pack"
    expected_added_bytes: int,
    baseline_bytes: Optional[int] = None,
    purchase_id: str = "",
    tariff: str = "",
    period_days: Optional[int] = None,
    delay_sec: float = 3.0,
) -> None:
    """Проверить что bypass entity в панели действительно получил GB.

    Args:
      expected_added_bytes: сколько ГБ должно было добавиться (в bytes).
      baseline_bytes: если известен current до операции — проверяем
        что реальный = baseline + expected. Иначе — что реальный
        >= expected (для новых entities).
    """
    import asyncio as _aio
    # Дадим панели время применить PATCH.
    await _aio.sleep(max(0.1, delay_sec))
    try:
        import database
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            await _send_admin_alert(
                "Bypass verify FAIL: no remnawave_uuid",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"Kind: <b>{kind}</b> · Provider: <b>{provider}</b>\n"
                    f"Tariff: <b>{tariff}</b>{f' · {period_days}d' if period_days else ''}\n"
                    f"Purchase: <code>{purchase_id}</code>\n"
                    f"Expected +{_fmt_gb(expected_added_bytes)} bypass\n"
                    "В subscriptions.remnawave_uuid пусто — bypass "
                    "entity не создался. Проверить логи PURCHASE_FLOW."
                ),
            )
            return
        traffic = await remnawave_api.get_user_traffic(rmn_uuid)
        if not traffic:
            await _send_admin_alert(
                "Bypass verify FAIL: entity not in panel",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"Kind: <b>{kind}</b> · Provider: <b>{provider}</b>\n"
                    f"UUID: <code>{str(rmn_uuid)[:16]}</code>\n"
                    "GET /api/users вернул пусто — entity удалён "
                    "или UUID/id stale."
                ),
            )
            return
        actual_bytes = int(traffic.get("trafficLimitBytes") or 0)
        # Ok-условия:
        if baseline_bytes is not None:
            # trafficLimitBytes=0 в панели = БЕЗЛИМИТ (semantic infinity).
            # Если entity был безлимитным ДО операции → top-up не имеет
            # смысла (∞ + N = ∞). Отдельный INFO-alert админу: юзер
            # заплатил, но functionally получил то же самое (безлимит),
            # admin решает refund/notify. Не ERROR — bot всё сделал верно.
            if int(baseline_bytes) == 0:
                if actual_bytes == 0:
                    await _send_admin_alert(
                        "Bypass top-up: entity уже был безлимит",
                        (
                            f"User: <code>tg:{telegram_id}</code>\n"
                            f"Kind: <b>{kind}</b> · Provider: <b>{provider}</b>\n"
                            f"Tariff: <b>{tariff}</b>{f' · {period_days}d' if period_days else ''}\n"
                            f"Purchase: <code>{purchase_id}</code>\n"
                            f"\n"
                            f"Пакет: <b>+{_fmt_gb(expected_added_bytes)}</b>\n"
                            f"Baseline: <b>∞</b> (безлимит)\n"
                            f"В панели: <b>∞</b> (без изменений — top-up "
                            f"безлимитного entity — no-op)\n"
                            f"\n"
                            f"Юзер уже имеет ∞ трафика — оплата фактически "
                            f"не изменила состояние. Проверьте: refund / "
                            f"признание как gift / игнор."
                        ),
                        tag="topup-unlimited",
                    )
                    logger.info(
                        "BYPASS_VERIFY_UNLIMITED_TOPUP: tg=%s kind=%s "
                        "extra=%s (already unlimited)",
                        telegram_id, kind, expected_added_bytes,
                    )
                    return
                # Baseline=0, но panel != 0 → кто-то PATCH-нул лимит.
                # Пусть остальной flow отработает как обычный mismatch.
                expected_total = int(expected_added_bytes)
                diff = actual_bytes - expected_total
                ok = False
            else:
                # Точное совпадение с допуском 100 MB (округления).
                expected_total = int(baseline_bytes) + int(expected_added_bytes)
                diff = actual_bytes - expected_total
                ok = abs(diff) < 100 * 1024 * 1024
        else:
            # Только-что созданный entity — просто >= expected.
            ok = actual_bytes >= int(expected_added_bytes) or actual_bytes == 0
            expected_total = int(expected_added_bytes)
            diff = actual_bytes - expected_total

        if ok:
            logger.info(
                "BYPASS_VERIFY_OK: tg=%s kind=%s actual=%s expected=%s",
                telegram_id, kind, actual_bytes, expected_total,
            )
            return

        await _send_admin_alert(
            "Bypass verify MISMATCH",
            (
                f"User: <code>tg:{telegram_id}</code>\n"
                f"Kind: <b>{kind}</b> · Provider: <b>{provider}</b>\n"
                f"Tariff: <b>{tariff}</b>{f' · {period_days}d' if period_days else ''}\n"
                f"Purchase: <code>{purchase_id}</code>\n"
                f"\n"
                f"Ожидалось: <b>{_fmt_gb(expected_total)}</b>"
                + (f" (был {_fmt_gb(baseline_bytes)} + {_fmt_gb(expected_added_bytes)})"
                   if baseline_bytes is not None else f" (пакет {_fmt_gb(expected_added_bytes)})") +
                f"\nВ панели: <b>{_fmt_gb(actual_bytes)}</b>\n"
                f"Diff: <b>{_fmt_gb(abs(diff))}</b> "
                f"({'нехватка' if diff < 0 else 'излишек'})\n"
                f"\n"
                f"Резолв через дашборд Юзеры → карточка."
            ),
        )
    except Exception as e:
        logger.exception("verify_bypass_delivery failed tg=%s: %s", telegram_id, e)


async def verify_premium_delivery(
    *,
    telegram_id: int,
    provider: str,
    expected_expire_at: datetime,
    purchase_id: str = "",
    tariff: str = "",
    period_days: Optional[int] = None,
    delay_sec: float = 3.0,
) -> None:
    """Проверить что premium entity в панели имеет корректный expireAt.

    Допуск: 5 минут (округления в панели / временной drift).
    """
    import asyncio as _aio
    await _aio.sleep(max(0.1, delay_sec))
    try:
        import database
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_premium_uuid(telegram_id)
        if not rmn_uuid:
            await _send_admin_alert(
                "Premium verify FAIL: no premium_uuid",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"Provider: <b>{provider}</b> · Tariff: <b>{tariff}</b>"
                    f"{f' · {period_days}d' if period_days else ''}\n"
                    f"Purchase: <code>{purchase_id}</code>\n"
                    "В subscriptions.remnawave_premium_uuid пусто — "
                    "premium entity не создался. Проверить логи PURCHASE_FLOW."
                ),
            )
            return
        entity = await remnawave_api.get_user(rmn_uuid)
        if not entity:
            await _send_admin_alert(
                "Premium verify FAIL: entity not in panel",
                (
                    f"User: <code>tg:{telegram_id}</code>\n"
                    f"UUID: <code>{str(rmn_uuid)[:16]}</code>\n"
                    "GET вернул пусто — id/uuid stale."
                ),
            )
            return
        panel_expire_raw = entity.get("expireAt")
        if not panel_expire_raw:
            await _send_admin_alert(
                "Premium verify: expireAt пусто в панели",
                f"tg={telegram_id}, ожидалось {expected_expire_at.isoformat()}",
            )
            return
        try:
            panel_expire = datetime.fromisoformat(str(panel_expire_raw).replace("Z", "+00:00"))
            if panel_expire.tzinfo is None:
                panel_expire = panel_expire.replace(tzinfo=timezone.utc)
        except Exception:
            panel_expire = None
        if panel_expire is None:
            await _send_admin_alert(
                "Premium verify: не удалось распарсить expireAt",
                f"tg={telegram_id}, raw={panel_expire_raw!r}",
            )
            return
        # Допуск 5 минут.
        diff_sec = abs((panel_expire - expected_expire_at).total_seconds())
        if diff_sec <= 300:
            logger.info(
                "PREMIUM_VERIFY_OK: tg=%s panel=%s expected=%s",
                telegram_id, panel_expire.isoformat(), expected_expire_at.isoformat(),
            )
            return
        await _send_admin_alert(
            "Premium verify MISMATCH: expireAt",
            (
                f"User: <code>tg:{telegram_id}</code>\n"
                f"Provider: <b>{provider}</b> · Tariff: <b>{tariff}</b>"
                f"{f' · {period_days}d' if period_days else ''}\n"
                f"Purchase: <code>{purchase_id}</code>\n"
                f"\n"
                f"Ожидалось: <code>{expected_expire_at.isoformat()}</code>\n"
                f"В панели:  <code>{panel_expire.isoformat()}</code>\n"
                f"Diff: <b>{int(diff_sec)}s</b>"
            ),
        )
    except Exception as e:
        logger.exception("verify_premium_delivery failed tg=%s: %s", telegram_id, e)
