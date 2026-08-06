"""Записи, которые утверждали то, чего не произошло.

ЗАЧЕМ ЭТИ ТЕСТЫ

    Разбор обращения идёт по логам, и запись, поставленная по НАМЕРЕНИЮ,
    хуже отсутствующей: она уводит разбор в сторону и закрывает вопрос.
    Здесь закреплены пять мест, где так и было:

    • «ключ отправлен» писалось после провала обеих попыток отправки;
    • «скидка применена» уходило человеку и в лог при пустом результате
      create_user_discount, которая глотает исключение внутри;
    • `old_purchases_cancelled=True` при отсутствующей отмене;
    • «отключено» / «продлено» в панели без взгляда на результат, а
      remnawave_api отдаёт None на отказ и не бросает;
    • «удалено» поверх заглушек снятого с эксплуатации xray — эти записи
      ложны не иногда, а всегда: под ними нет действия.

    Обычные тесты поведения ни одного из пяти не ловят: код работает
    ровно так же, ломается только возможность потом разобраться.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_FULL_UUID = "11111111-1111-4111-8111-111111111111"


# ─────────────────────────────────────────────────────────────────────
# Д1. VPN_KEY_SENT и аудит-строка vpn_key_sent=True
# ─────────────────────────────────────────────────────────────────────

def _payment_objects(*, upgrade: bool = False):
    """Минимальные env/ctx/fin для announce_success."""
    from app.handlers.payments.payment_preflight import PaymentEnvelope, PurchaseContext
    from app.handlers.payments.subscription_finalize import FinalizedSubscription
    from datetime import datetime, timezone

    env = PaymentEnvelope(
        telegram_id=777,
        language="ru",
        payment=MagicMock(),
        payload="sub_basic_30_777",
        is_stars_payment=False,
        degradation_notice=False,
    )
    ctx = PurchaseContext(
        purchase_id="p-1",
        pending_purchase={},
        tariff_type="basic",
        period_days=30,
        promo_code_used=None,
        payment_amount_rubles=199.0,
    )
    result = MagicMock()
    result.is_basic_to_plus_upgrade = upgrade
    result.is_combo = False
    result.referral_reward = None
    fin = FinalizedSubscription(
        result=result,
        payment_id="pay-1",
        expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        vpn_key="vk",
        is_renewal=False,
        subscription_type="basic",
        vpn_key_plus=None,
    )
    return env, ctx, fin


@pytest.mark.asyncio
async def test_vpn_key_sent_is_not_written_when_both_sends_fail(monkeypatch, caplog):
    """Обе попытки отправки экрана падают — значит ключа человек не видел.

    Раньше сразу за ERROR-ом «Failed to send payment success message»
    стояла безусловная VPN_KEY_SENT: при разборе «оплатил, ключ не пришёл»
    админ видел два противоречащих утверждения и верил тому, что говорит
    «отправлено».
    """
    import app.handlers.payments.subscription_success as ss

    monkeypatch.setattr(ss.database, "is_payment_notification_sent", AsyncMock(return_value=False))
    monkeypatch.setattr(ss.database, "mark_payment_notification_sent", AsyncMock(return_value=True))
    monkeypatch.setattr(ss, "notify_referral_cashback", AsyncMock(return_value=None))
    monkeypatch.setattr(ss, "log_handler_exit", MagicMock())

    message = MagicMock()
    message.answer = AsyncMock(side_effect=RuntimeError("bot blocked"))
    message.bot = MagicMock()

    env, ctx, fin = _payment_objects()
    with caplog.at_level(logging.INFO, logger=ss.__name__):
        proceed, delivered = await ss.announce_success(message, env, ctx, fin, 0.0)

    assert proceed is True, "сценарий должен идти дальше: подписка выдана"
    assert delivered is False, "ни одна попытка отправки не прошла"

    text = caplog.text
    assert "VPN_KEY_SENT" not in text, "запись о выдаче ключа поставлена по намерению"
    assert "PAYMENT_CONFIRMATION_UNDELIVERED" in text
    assert "777" in text and "p-1" in text, "в записи нет идентификаторов"
    assert "vpn_key_sent=False" in text, "PAYMENT_COMPLETE снова утверждает отправку"


@pytest.mark.asyncio
async def test_vpn_key_sent_is_written_when_the_screen_goes_out(monkeypatch, caplog):
    """Обратная сторона: успешная отправка обязана оставлять запись."""
    import app.handlers.payments.subscription_success as ss

    monkeypatch.setattr(ss.database, "is_payment_notification_sent", AsyncMock(return_value=False))
    monkeypatch.setattr(ss.database, "mark_payment_notification_sent", AsyncMock(return_value=True))
    monkeypatch.setattr(ss, "notify_referral_cashback", AsyncMock(return_value=None))
    monkeypatch.setattr(ss, "log_handler_exit", MagicMock())

    message = MagicMock()
    message.answer = AsyncMock(return_value=None)
    message.bot = MagicMock()

    env, ctx, fin = _payment_objects()
    with caplog.at_level(logging.INFO, logger=ss.__name__):
        proceed, delivered = await ss.announce_success(message, env, ctx, fin, 0.0)

    assert (proceed, delivered) == (True, True)
    assert "VPN_KEY_SENT" in caplog.text
    assert "vpn_key_sent=True" in caplog.text


def test_payment_audit_row_reports_real_delivery():
    """Аудит уходит в журнал дашборда — его админ открывает первым.

    Там стоял строковый литерал vpn_key_sent=True, который не вычислялся
    никогда: заказ выглядел выданным независимо от исхода отправки.
    """
    import app.handlers.payments.subscription_success as ss

    src = inspect.getsource(ss.finish_payment)
    assert "vpn_key_sent=True" not in src, "в аудите снова захардкожена выдача ключа"
    assert "vpn_key_sent={delivered}" in src
    # Тот же платёж закрывает span: недоставленный экран — не полный успех.
    assert 'outcome="success" if delivered else "degraded"' in src, (
        "outcome снова захардкожен: по метрике недоставленный платёж "
        "неотличим от доставленного"
    )


def test_delivery_flag_reaches_finish_payment():
    """Фасад обязан протащить факт доставки до уборки — иначе аудиту
    неоткуда взять правду."""
    src = (REPO_ROOT / "app/handlers/payments/payments_messages.py").read_text(encoding="utf-8")
    assert "delivered = await announce_success(" in src
    assert "await finish_payment(state, env, ctx, fin, start_time, delivered)" in src


# ─────────────────────────────────────────────────────────────────────
# Д7. «Скидка применена» до того, как скидка создана
# ─────────────────────────────────────────────────────────────────────

def _callback(user_id: int = 555, data: str = "broadcast_promo_buy:9"):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = user_id
    cb.answer = AsyncMock(return_value=None)
    cb.message = MagicMock()
    cb.message.answer = AsyncMock(return_value=None)
    cb.message.chat.id = user_id
    cb.bot = MagicMock()
    cb.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    cb.bot.delete_message = AsyncMock(return_value=None)
    return cb


@pytest.mark.asyncio
async def test_broadcast_discount_failure_is_reported(monkeypatch, caplog):
    """create_user_discount отдаёт False и при неготовой базе, и при любом
    исключении внутри. Результат не читался: человек получал «скидка
    применена», в базе не было ничего, в логе — ни строки."""
    import app.handlers.callbacks.broadcast_offers.promo_discounts as pd

    monkeypatch.setattr(
        pd.database, "get_broadcast_discount",
        AsyncMock(return_value={"discount_percent": 30, "discount_hours": 48,
                                "discount_label": "48 часов"}),
    )
    monkeypatch.setattr(pd.database, "create_user_discount", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.handlers.common.screens.show_tariffs_main_screen",
        AsyncMock(return_value=None),
    )

    cb = _callback()
    with caplog.at_level(logging.INFO, logger=pd.__name__):
        await pd.callback_broadcast_promo_buy(cb, MagicMock())

    assert "BROADCAST_DISCOUNT_NOT_CREATED" in caplog.text
    assert "555" in caplog.text, "в записи нет telegram_id"
    said = " ".join(str(c) for c in cb.message.answer.call_args_list)
    assert "автоматически применена" not in said, (
        "человеку обещана скидка, которой в базе нет"
    )


@pytest.mark.asyncio
async def test_broadcast_discount_success_is_reported(monkeypatch, caplog):
    import app.handlers.callbacks.broadcast_offers.promo_discounts as pd

    monkeypatch.setattr(
        pd.database, "get_broadcast_discount",
        AsyncMock(return_value={"discount_percent": 30, "discount_hours": 48,
                                "discount_label": "48 часов"}),
    )
    monkeypatch.setattr(pd.database, "create_user_discount", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.handlers.common.screens.show_tariffs_main_screen",
        AsyncMock(return_value=None),
    )

    cb = _callback()
    with caplog.at_level(logging.INFO, logger=pd.__name__):
        await pd.callback_broadcast_promo_buy(cb, MagicMock())

    assert "BROADCAST_DISCOUNT_APPLIED" in caplog.text
    said = " ".join(str(c) for c in cb.message.answer.call_args_list)
    assert "автоматически применена" in said


@pytest.mark.asyncio
async def test_traffic_discount_failure_does_not_draw_discounted_prices(monkeypatch, caplog):
    """Экран пакетов рисовал зачёркнутые цены и «скидка применена» по
    проценту из рассылки, а не по тому, легла ли скидка в базу: человек
    выбирал пакет по одной цене, платил по другой."""
    import app.handlers.callbacks.broadcast_offers.promo_discounts as pd

    monkeypatch.setattr(
        pd.database, "get_broadcast_discount",
        AsyncMock(return_value={"discount_percent": 40}),
    )
    monkeypatch.setattr(pd.database, "create_user_traffic_discount", AsyncMock(return_value=False))
    monkeypatch.setattr(pd.database, "get_subscription", AsyncMock(return_value={"status": "active"}))
    monkeypatch.setattr(pd, "resolve_user_language", AsyncMock(return_value="ru"))

    cb = _callback(data="broadcast_promo_traffic:9")
    with caplog.at_level(logging.INFO, logger=pd.__name__):
        await pd.callback_broadcast_promo_traffic(cb)

    assert "BROADCAST_TRAFFIC_DISCOUNT_NOT_CREATED" in caplog.text
    assert "555" in caplog.text
    said = " ".join(str(c) for c in cb.message.answer.call_args_list)
    assert "на трафик применена" not in said


@pytest.mark.asyncio
async def test_gift_reveal_promises_nothing_until_the_discount_exists(monkeypatch, caplog):
    """Сообщение «для тебя подарок N%» отправлялось ПЕРЕД созданием скидки
    и независимо от него."""
    import app.handlers.callbacks.broadcast_offers.gift_reveal as gr

    monkeypatch.setattr(
        gr.database, "get_broadcast_discount",
        AsyncMock(return_value={"gift_reveal_percent": 35}),
    )
    monkeypatch.setattr(gr.database, "create_user_discount", AsyncMock(return_value=False))
    monkeypatch.setattr(gr.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.handlers.common.screens.show_tariffs_main_screen",
        AsyncMock(return_value=None),
    )

    cb = _callback(data="broadcast_gift_reveal:9")
    state = MagicMock()
    state.update_data = AsyncMock(return_value=None)
    with caplog.at_level(logging.INFO, logger=gr.__name__):
        await gr.callback_broadcast_gift_reveal(cb, state)

    assert "GIFT_REVEAL_DISCOUNT_NOT_CREATED" in caplog.text
    assert "555" in caplog.text
    sent = " ".join(str(c) for c in cb.bot.send_message.call_args_list)
    assert "Для тебя подарок" not in sent, (
        "подарок обещан раньше, чем создан — и при пустом результате"
    )


@pytest.mark.asyncio
async def test_share_discount_claim_says_whether_the_discount_exists(monkeypatch, caplog):
    """REFDC_CLAIMED писала pct=30 константой. Claim одноразовый на всю
    жизнь аккаунта: при провале create_user_discount человек терял и
    скидку, и возможность получить её тем же путём, а лог подтверждал
    выдачу."""
    import app.handlers.user.start.share_discount as sd

    monkeypatch.setattr(sd, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(
        sd.database, "find_user_by_referral_code",
        AsyncMock(return_value={"telegram_id": 999}),
    )
    monkeypatch.setattr(sd.database, "has_claimed_referral_share_discount", AsyncMock(return_value=False))
    monkeypatch.setattr(sd.database, "get_user_discount", AsyncMock(return_value=None))
    monkeypatch.setattr(sd.database, "create_user_discount", AsyncMock(return_value=False))
    monkeypatch.setattr(sd.database, "record_referral_share_discount_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.handlers.common.screens.show_tariffs_main_screen",
        AsyncMock(return_value=None),
    )

    message = MagicMock()
    message.answer = AsyncMock(return_value=None)

    with caplog.at_level(logging.INFO, logger=sd.__name__):
        handled = await sd._handle_share_discount_start(
            message, MagicMock(), 555, "abc123", is_new_user=False,
        )

    assert handled is True
    assert "REFDC_DISCOUNT_NOT_CREATED" in caplog.text
    assert "discount_created=False" in caplog.text, (
        "REFDC_CLAIMED снова утверждает выдачу константой"
    )
    assert "555" in caplog.text


# ─────────────────────────────────────────────────────────────────────
# Д13. old_purchases_cancelled=True
# ─────────────────────────────────────────────────────────────────────

def test_promo_applied_does_not_claim_a_cancellation_that_never_happens():
    """Рядом стоит комментарий «КРИТИЧНО: НЕ отменяем pending покупки», а
    запись сообщала об отмене. Разбор «применил промокод, старый счёт
    списал полную цену» уходил искать, почему отмена не сработала."""
    src = (REPO_ROOT / "app/handlers/payments/promo_fsm.py").read_text(encoding="utf-8")
    live = [
        ln for ln in src.split("\n")
        if "old_purchases_cancelled=True" in ln and not ln.lstrip().startswith("#")
    ]
    assert not live, f"запись снова сообщает об отмене: {live}"
    assert "pending_purchases_left_intact=True" in src


# ─────────────────────────────────────────────────────────────────────
# Д3. Панель: результат update_user / delete_user не проверялся
# ─────────────────────────────────────────────────────────────────────

def _panel_ready(monkeypatch, rs, *, user_data: dict):
    monkeypatch.setattr(rs.config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(rs.database, "get_remnawave_uuid", AsyncMock(return_value=_FULL_UUID))
    monkeypatch.setattr(rs.database, "clear_remnawave_uuid", AsyncMock(return_value=None))
    monkeypatch.setattr(rs.database, "reset_traffic_notification_flags", AsyncMock(return_value=None))
    monkeypatch.setattr(rs.remnawave_api, "get_user", AsyncMock(return_value=user_data))


@pytest.mark.asyncio
async def test_disable_reports_panel_refusal(monkeypatch, caplog):
    """update_user отдаёт None на любой отказ панели и не бросает. Запись
    REMNAWAVE_DISABLED стояла безусловно: «отозвали, а VPN работает»
    разбиралось как «человек путает»."""
    import app.services.remnawave_service as rs

    _panel_ready(monkeypatch, rs, user_data={
        "uuid": _FULL_UUID, "trafficLimitBytes": 0, "usedTrafficBytes": 0, "status": "ACTIVE",
    })
    monkeypatch.setattr(rs.remnawave_api, "update_user", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.disable_remnawave_user(4242)

    assert "REMNAWAVE_DISABLE_REJECTED" in caplog.text
    assert "REMNAWAVE_DISABLED:" not in caplog.text
    assert "4242" in caplog.text


@pytest.mark.asyncio
async def test_disable_still_reports_success(monkeypatch, caplog):
    import app.services.remnawave_service as rs

    _panel_ready(monkeypatch, rs, user_data={
        "uuid": _FULL_UUID, "trafficLimitBytes": 0, "usedTrafficBytes": 0, "status": "ACTIVE",
    })
    monkeypatch.setattr(rs.remnawave_api, "update_user", AsyncMock(return_value={"uuid": _FULL_UUID}))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.disable_remnawave_user(4242)

    assert "REMNAWAVE_DISABLED:" in caplog.text
    assert "REJECTED" not in caplog.text


@pytest.mark.asyncio
async def test_renew_reports_panel_refusal(monkeypatch, caplog):
    """«Продлил, а через месяц отключилось»: REMNAWAVE_RENEWED в логе есть,
    значит причину ищут в базе, где всё правильно."""
    import app.services.remnawave_service as rs
    from datetime import datetime, timezone, timedelta

    _panel_ready(monkeypatch, rs, user_data={
        "uuid": _FULL_UUID, "trafficLimitBytes": 1024, "status": "ACTIVE",
        "activeInternalSquads": ["s"],
    })
    monkeypatch.setattr(rs.remnawave_api, "update_user", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.renew_remnawave_user(
            4242, "basic", datetime.now(timezone.utc) + timedelta(days=30), period_days=30,
        )

    assert "REMNAWAVE_RENEW_REJECTED" in caplog.text
    assert "REMNAWAVE_RENEWED" not in caplog.text
    assert "4242" in caplog.text


@pytest.mark.asyncio
async def test_bypass_extend_reports_panel_refusal(monkeypatch, caplog):
    import app.services.remnawave_service as rs

    _panel_ready(monkeypatch, rs, user_data={"uuid": _FULL_UUID})
    monkeypatch.setattr(rs.remnawave_api, "update_user", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.extend_remnawave_for_bypass(4242)

    assert "REMNAWAVE_BYPASS_EXTEND_REJECTED" in caplog.text
    assert "REMNAWAVE_BYPASS_EXTENDED" not in caplog.text


@pytest.mark.asyncio
async def test_keep_active_reports_panel_refusal(monkeypatch, caplog):
    """У человека остались оплаченные гигабайты обхода — если панель не
    приняла продление срока, обход погаснет по старой дате."""
    import app.services.remnawave_service as rs

    _panel_ready(monkeypatch, rs, user_data={
        "uuid": _FULL_UUID, "trafficLimitBytes": 1024, "usedTrafficBytes": 1, "status": "ACTIVE",
    })
    monkeypatch.setattr(rs.remnawave_api, "update_user", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.disable_remnawave_user(4242)

    assert "REMNAWAVE_KEEP_ACTIVE_REJECTED" in caplog.text
    assert "REMNAWAVE_KEPT_ACTIVE" not in caplog.text


@pytest.mark.asyncio
async def test_delete_reports_panel_refusal(monkeypatch, caplog):
    """Ссылку из базы стираем в любом случае — значит после отказа панели
    сущность находится только по этой записи."""
    import app.services.remnawave_service as rs

    _panel_ready(monkeypatch, rs, user_data={"uuid": _FULL_UUID})
    monkeypatch.setattr(rs.remnawave_api, "delete_user", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.delete_remnawave_user(4242)

    assert "REMNAWAVE_DELETE_REJECTED" in caplog.text
    assert "REMNAWAVE_DELETED:" not in caplog.text
    assert _FULL_UUID[:8] in caplog.text


@pytest.mark.asyncio
async def test_tariff_update_reports_panel_refusal(monkeypatch, caplog):
    import app.services.remnawave_service as rs

    _panel_ready(monkeypatch, rs, user_data={"uuid": _FULL_UUID})
    monkeypatch.setattr(rs.remnawave_api, "update_user", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger=rs.__name__):
        await rs.update_tariff(4242, "plus", period_days=30)

    assert "REMNAWAVE_TARIFF_UPDATE_REJECTED" in caplog.text
    assert "REMNAWAVE_TARIFF_UPDATED" not in caplog.text


# ─────────────────────────────────────────────────────────────────────
# Д3 (вторая половина). Записи «удалено» поверх заглушек xray
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stub_retry_does_not_report_a_deletion(caplog):
    """safe_remove_vless_user_with_retry ведёт в заглушку: HTTP-вызова нет,
    исключений нет, retry не срабатывает. ORPHAN_CLEANUP_SUCCESS не бывал
    ложным «иногда» — он был ложным всегда."""
    import vpn_utils

    with caplog.at_level(logging.INFO, logger="vpn_utils"):
        await vpn_utils.safe_remove_vless_user_with_retry(_FULL_UUID)

    assert "ORPHAN_CLEANUP_SUCCESS" not in caplog.text
    assert "ORPHAN_CLEANUP_NOOP" in caplog.text
    assert _FULL_UUID[:8] in caplog.text


# Места, где под записью «удалено» стоит заглушка снятого с эксплуатации
# xray: файл → запрещённая запись → запись, которая обязана быть вместо неё.
_STUB_SITES = [
    ("database/subscription_state.py", "EXPIRY_REMOVE_SUCCESS", "EXPIRY_LEGACY_UUID_CLEARED"),
    ("database/admin_access.py", "ADMIN_REVOKE_UUID_REMOVED", "ADMIN_REVOKE_LEGACY_UUID_CLEARED"),
    ("database/balance_purchases.py", 'ORPHAN_PREVENTED uuid={uuid_preview} reason=finalize_balance',
     "ORPHAN_NOT_CLEANED"),
    ("app/services/activation/service.py", "ACTIVATION_ORPHAN_PREVENTED", "ACTIVATION_ORPHAN_NOT_CLEANED"),
]


@pytest.mark.parametrize("rel_path,forbidden,expected", _STUB_SITES)
def test_stub_sites_do_not_claim_a_deletion(rel_path, forbidden, expected):
    """«Отозвали доступ, а VPN работает» разбиралось как «человек путает»:
    в логах стояли и REMNAWAVE_DISABLED, и ADMIN_REVOKE_UUID_REMOVED."""
    src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    live = [
        ln for ln in src.split("\n")
        if forbidden in ln and not ln.lstrip().startswith("#")
    ]
    assert not live, f"{rel_path}: запись поверх заглушки вернулась: {live}"
    assert expected in src, f"{rel_path}: нет честной записи {expected}"


def test_orphan_not_cleaned_records_name_the_entity():
    """Без идентификатора запись бесполезна: вычистить сущность руками
    не по чему."""
    act = (REPO_ROOT / "app/services/activation/service.py").read_text(encoding="utf-8")
    idx = act.index("ACTIVATION_ORPHAN_NOT_CLEANED")
    window = act[idx:idx + 600]
    assert "subscription_id" in window and "uuid" in window
    assert "вручную" in window

    bal = (REPO_ROOT / "database/balance_purchases.py").read_text(encoding="utf-8")
    idx = bal.index("ORPHAN_NOT_CLEANED")
    window = bal[idx:idx + 400]
    assert "user={telegram_id}" in window
    assert "вручную" in window


def test_expiry_audit_row_no_longer_claims_a_removal():
    """Аудит уходит в audit_log, то есть в журнал дашборда. Он говорил
    result=success сразу за заглушкой — читалось как «доступ снят».

    Проверка не привязана к месту вызова: запись переехала из фазы 2 в
    фазу 3, за UPDATE, и текст переехал с ней. Порядок закреплён отдельно —
    tests/integration/test_vpn_entitlement.py::TestRealtimeExpiryAuditOrder.
    """
    import database.subscription_state as st

    src = inspect.getsource(st.check_and_disable_expired_subscription)
    assert "no-op stub, nothing removed" in src, (
        "аудит снова не говорит, что удалять было нечем"
    )
    assert "disable_remnawave_user_bg" in src, (
        "в аудите нет указания, кто снимает доступ на самом деле"
    )
