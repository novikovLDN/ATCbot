"""Интеграционные проверки выдачи VPN-доступа.

ВАЖНО ПРО ЦЕЛИ ПАТЧЕЙ
    Патчить нужно модуль, где функция ОПРЕДЕЛЕНА, — здесь это
    database.purchase_finalization. Ни database.<имя>, ни
    database.subscriptions.<имя> не годятся: и пакет, и файл подписок
    только реэкспортируют, а исполняется код внутри своего модуля и берёт
    зависимости из его пространства имён. Патч мимо места молча не
    срабатывает — тест проходит, не проверив ничего.

Что проверяется:

1. Сбой базы после создания UUID → UUID удаляется, сироты не остаётся.
2. Повторный вебхук не создаёт вторую подписку.
3. Истёкшая подписка удаляется.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock


class TestOrphanPreventionOnDBFailure:
    """Сбой базы после создания сущности в панели.

    ИСТОРИЯ ЭТОГО ТЕСТА
        Раньше он проверял, что при откате транзакции вызывается
        vpn_utils.remove_vless_user — то есть сущность удаляется из samopis
        xray. После перехода на Remnawave такой очистки нет by design:
        дёргать снятый с эксплуатации сервис бессмысленно, он ответит 404.

        Актуальное поведение: откат не пытается ничего удалять, но пишет
        запись с идентификатором сущности — по ней админ вычищает её через
        панель. Тест закрепляет именно это, чтобы никто не «починил»
        логирование обратно в вызов несуществующего сервиса.

    ОСТОРОЖНО С НАЗВАНИЕМ ЗАПИСИ
        Здесь стояло имя PURCHASE_FLOW_ORPHAN_NOT_CLEANED, которого нет ни
        в одном файле репозитория. Докстринг, называющий несуществующую
        запись, — та же ложь, что и запись о несделанном действии: по нему
        ищут в логах и не находят, и разбор упирается в «поиск сломан».

        Фактические имена: ORPHAN_NOT_CLEANED (balance_purchases) и
        ORPHAN_PREVENTED (purchase_finalization, subscription_reissue,
        admin_access). Второе — само по себе неправда, «prevented» под
        заглушкой ничего не предотвращает; оно записано в реестр как
        отдельный пункт. Тест намеренно НЕ сверяет имя: он проверяет
        поведение (что удаления не было), а не текст записи.
    """

    @pytest.mark.asyncio
    async def test_rollback_reports_orphan_instead_of_calling_samopis(self, caplog):
        import logging

        import database.purchase_finalization as subs

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "purchase_id": "p1", "telegram_id": 123, "status": "pending",
            "tariff": "basic", "period_days": 30, "price_kopecks": 10000,
            "purchase_type": "subscription",
        })
        conn.fetchval = AsyncMock(return_value=1)
        conn.execute = AsyncMock(return_value="UPDATE 1")

        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=conn)
        tx_ctx.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=tx_ctx)

        pool = MagicMock()
        acq = MagicMock()
        acq.__aenter__ = AsyncMock(return_value=conn)
        acq.__aexit__ = AsyncMock(return_value=None)
        pool.acquire.return_value = acq

        removed = []

        async def fake_remove(uuid):
            removed.append(uuid)

        with patch.object(subs, "get_pool", AsyncMock(return_value=pool)), \
             patch.object(subs.vpn_utils, "safe_remove_vless_user_with_retry",
                          AsyncMock(side_effect=fake_remove)), \
             patch.object(subs, "grant_access",
                          AsyncMock(side_effect=Exception("Simulated DB failure"))):
            with caplog.at_level(logging.WARNING, logger="database.purchase_finalization"):
                # grant_access поднимает именно это исключение — ловим его,
                # а не любое, иначе тест пройдёт и на посторонней ошибке.
                with pytest.raises(Exception, match="Simulated DB failure"):
                    await subs.finalize_purchase(
                        purchase_id="p1",
                        payment_provider="cryptobot",
                        amount_rubles=100.0,
                        invoice_id="inv1",
                    )

        assert removed == [], (
            "откат не должен дёргать снятый с эксплуатации samopis xray"
        )


class TestDuplicateWebhookIdempotency:
    """Test 2: Duplicate webhook must not create duplicate subscription."""

    @pytest.mark.asyncio
    async def test_duplicate_webhook_raises_already_processed(self):
        """Same purchase_id, status already 'paid' → ValueError."""
        with patch("database.purchase_finalization.get_pool") as mock_pool:
            conn = MagicMock()
            conn.fetchrow = AsyncMock(return_value={
                "purchase_id": "p1", "telegram_id": 123, "status": "paid",  # already paid
                "tariff": "basic", "period_days": 30, "price_kopecks": 10000,
                "purchase_type": "subscription"
            })
            # finalize_purchase берёт advisory-лок на покупку перед работой:
            # успешный захват возвращает True.
            conn.fetchval = AsyncMock(return_value=True)
            conn.execute = AsyncMock(return_value="SELECT 1")
            pool = MagicMock()
            acq = MagicMock()
            acq.__aenter__ = AsyncMock(return_value=conn)
            acq.__aexit__ = AsyncMock(return_value=None)
            pool.acquire.return_value = acq
            mock_pool.return_value = pool

            import database
            with pytest.raises(ValueError, match="already processed"):
                await database.finalize_purchase(
                    purchase_id="p1",
                    payment_provider="cryptobot",
                    amount_rubles=100.0,
                )


class TestExpiredSubscriptionRemoved:
    """Истечение подписки должно гасить premium-сущность в панели.

    ЧТО ЛОМАЛОСЬ

        Воркер звал vpn_service.remove_uuid_if_needed — тот делегировал в
        no-op заглушку и возвращал True, ничего не отключив. По этому
        True тут же писался аудит vpn_expire с result=success, а строка в
        базе обнулялась. В панели сущность оставалась активной и гасла
        сама по своему expireAt — то есть человек с подрезанной админом
        датой или после перехода в bypass-only (там expires_at ставится
        на десять лет вперёд) продолжал пользоваться платным VPN. Лог при
        этом утверждал обратное.

        Второй дефект того же места: аудит «доступ закрыт» писался ДО
        проверки, что подписку и правда закрывают. Строкой ниже мог
        сработать SKIP_RENEWED — человек успел продлиться, а запись уже
        соврала.

    ПОРЯДОК, КОТОРЫЙ ПРОВЕРЯЕМ

        UPDATE в базе → и только затем отключение в панели, вне открытой
        транзакции: HTTP внутри неё держал бы соединение пула и
        блокировки строк всё время запроса.
    """

    def _source(self):
        import inspect
        import fast_expiry_cleanup
        return inspect.getsource(fast_expiry_cleanup)

    def test_panel_entity_is_actually_disabled(self):
        src = self._source()
        assert "disable_premium_user" in src, (
            "истечение снова никак не отражается в панели"
        )
        assert "remove_uuid_if_needed" not in src.replace(
            "# Здесь стоял вызов vpn_service.remove_uuid_if_needed", "",
        ), "вернулся вызов заглушки, которая ничего не отключает"

    def test_panel_is_disabled_only_after_the_row_is_closed(self):
        """Иначе гасим доступ человеку, который успел продлиться."""
        src = self._source()
        flag_set = src.index("disable_premium_after_commit = True")
        renewed_check = src.index("SKIP_RENEWED")
        call = src.index("await disable_premium_user(telegram_id)")

        assert renewed_check < flag_set, "флаг ставится до проверки продления"
        assert flag_set < call, "панель гасится раньше, чем закрыта строка в БД"

    def test_panel_call_is_outside_the_transaction(self):
        """Сетевой вызов внутри транзакции держит соединение и блокировки.

        Проверяем по отступу: ветка `if disable_premium_after_commit:`
        обязана стоять на том же уровне, что и сам `async with
        acquire_connection`, — то есть за пределами блока соединения.
        """
        src = self._source()

        def _indent_of(needle: str) -> int:
            pos = src.index(needle)
            line_start = src.rindex("\n", 0, pos) + 1
            return pos - line_start

        tx_indent = _indent_of('async with acquire_connection(pool, "fast_expiry_update")')
        guard_indent = _indent_of("if disable_premium_after_commit:")

        assert guard_indent == tx_indent, (
            f"вызов панели оказался вложен в блок соединения "
            f"(отступ {guard_indent} против {tx_indent})"
        )

    def test_success_audit_is_written_once_and_after_the_update(self):
        """Раньше vpn_expire/success писался дважды: до проверки и после."""
        src = self._source()
        writes = src.count("_log_vpn_lifecycle_audit_async(")
        assert writes == 1, (
            f"записей в журнал жизненного цикла стало {writes} — "
            f"вернулся преждевременный аудит"
        )
        assert 'action="vpn_expire"' not in src, (
            "действие снова захардкожено — оно должно зависеть от ветки"
        )
        assert "action=audit_action" in src

    def test_bypass_transition_is_not_logged_as_an_expiry(self):
        """Переход в bypass-only — не истечение.

        Строка остаётся активной, expires_at уезжает на десять лет вперёд,
        человек продолжает пользоваться обходом. Запись «подписка истекла и
        UUID удалён» вела разбор инцидента не туда: искали истёкшую
        подписку, а она активна.
        """
        src = self._source()
        block = src[src.index('if update_result == "UPDATE 1":'):]
        block = block[: block.index("_log_vpn_lifecycle_audit_async")]

        assert "if has_remnawave:" in block, "ветки аудита не разведены"
        assert "vpn_bypass_transition" in block, "у перехода в обход нет своего действия"
        assert "uuid_bypass_transition" in block, "у перехода в обход нет своего события"


    def test_premium_is_disabled_in_both_branches(self):
        """Платный доступ снят и там, и там — premium гасим в обоих случаях."""
        src = self._source()
        block = src[src.index('if update_result == "UPDATE 1":'):]
        flag = block.index("disable_premium_after_commit = True")
        branch = block.index("if has_remnawave:")
        assert flag < branch, (
            "флаг отключения premium попал внутрь одной из веток — "
            "во второй доступ останется"
        )


class TestRealtimeExpiryAuditOrder:
    """Проверка истечения по запросу: журнал не должен обгонять базу.

    ЧТО ЛОМАЛОСЬ

        database.check_and_disable_expired_subscription разнесена на три
        фазы: чтение → снятие доступа вне транзакции → UPDATE. Запись
        vpn_expire с result=success ставилась во ФАЗЕ 2, сразу за вызовом
        xray-заглушки, — то есть до того, как подписка закрыта.

        Закрывает её UPDATE фазы 3, и он может не сработать: пока шла
        фаза 2, человек успел продлиться, условие `expires_at <= now`
        перестало выполняться, UPDATE трогает ноль строк и ветка уходит в
        EXPIRY_SKIPPED_RENEWED. Подписка при этом активна, а в журнале
        дашборда уже стоит «истекла» — разбор обращения «списали день»
        упирался в запись, которой верить нельзя.

        То же самое было вылечено в fast_expiry_cleanup переносом записи
        за UPDATE; здесь повторён тот же приём.
    """

    def _source(self):
        import inspect
        import database.subscription_state as st
        return inspect.getsource(st.check_and_disable_expired_subscription)

    def test_audit_is_written_once_and_after_the_update(self):
        src = self._source()
        writes = src.count("_log_vpn_lifecycle_audit_async(")
        assert writes == 1, (
            f"записей в журнал жизненного цикла стало {writes} — "
            f"вернулся преждевременный аудит"
        )
        update = src.index("SET status = 'expired'")
        renewed_check = src.index("if rows == 0:")
        audit = src.index("_log_vpn_lifecycle_audit_async(")
        assert update < renewed_check < audit, (
            "аудит снова пишется раньше, чем закрыта строка в базе"
        )

    def test_action_depends_on_the_branch(self):
        """Переход в bypass-only — не истечение: строка остаётся активной,
        expires_at уезжает на десять лет вперёд, обход продолжает работать."""
        src = self._source()
        assert 'action="vpn_expire"' not in src, (
            "действие снова захардкожено — оно должно зависеть от ветки"
        )
        assert "action=audit_action" in src
        assert "vpn_bypass_transition" in src

    @pytest.mark.asyncio
    async def test_nothing_is_logged_when_the_user_renewed(self, monkeypatch):
        """Гонка с продлением: UPDATE не трогает ни одной строки."""
        import database.core as core
        import database.subscription_state as st

        telegram_id = 555
        uuid = "11111111-1111-4111-8111-111111111111"
        row = {
            "id": 7,
            "telegram_id": telegram_id,
            "uuid": uuid,
            "status": "active",
            "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "source": "payment",
        }

        conn = MagicMock()
        # 1) чтение истёкшей строки, 2) перепроверка перед фазой 2
        conn.fetchrow = AsyncMock(side_effect=[row, {"ok": 1}])
        conn.fetchval = AsyncMock(return_value=None)  # bypass-сущности нет
        conn.execute = AsyncMock(return_value="UPDATE 0")  # продлился первым
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=conn)
        tx.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=tx)
        pool = MagicMock()
        acq = MagicMock()
        acq.__aenter__ = AsyncMock(return_value=conn)
        acq.__aexit__ = AsyncMock(return_value=None)
        pool.acquire.return_value = acq

        audit = AsyncMock()
        monkeypatch.setattr(core, "DB_READY", True)
        monkeypatch.setattr(st, "get_pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(st, "_log_vpn_lifecycle_audit_async", audit)
        monkeypatch.setattr(
            st.vpn_utils, "safe_remove_vless_user_with_retry", AsyncMock(),
        )

        assert await st.check_and_disable_expired_subscription(telegram_id) is False
        assert audit.await_count == 0, (
            "подписка осталась активной, а в журнале появилось «истекла»"
        )

    @pytest.mark.asyncio
    async def test_audit_follows_the_update_that_closed_the_row(self, monkeypatch):
        """Обратная сторона: строку закрыли — запись обязана появиться, и
        именно после UPDATE-а."""
        import database.core as core
        import database.subscription_state as st

        telegram_id = 556
        uuid = "11111111-1111-4111-8111-111111111111"
        row = {
            "id": 8,
            "telegram_id": telegram_id,
            "uuid": uuid,
            "status": "active",
            "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "source": "trial",
        }
        order = []

        async def _execute(*_args, **_kwargs):
            order.append("update")
            return "UPDATE 1"

        async def _audit(**kwargs):
            order.append(f"audit:{kwargs.get('action')}")

        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=[row, {"ok": 1}])
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock(side_effect=_execute)
        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=conn)
        tx.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=tx)
        pool = MagicMock()
        acq = MagicMock()
        acq.__aenter__ = AsyncMock(return_value=conn)
        acq.__aexit__ = AsyncMock(return_value=None)
        pool.acquire.return_value = acq

        import app.services.remnawave_service as rs
        monkeypatch.setattr(rs, "disable_remnawave_user_bg", MagicMock())
        monkeypatch.setattr(core, "DB_READY", True)
        monkeypatch.setattr(st, "get_pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(st, "_log_vpn_lifecycle_audit_async", _audit)
        monkeypatch.setattr(
            st.vpn_utils, "safe_remove_vless_user_with_retry", AsyncMock(),
        )

        assert await st.check_and_disable_expired_subscription(telegram_id) is True
        assert order == ["update", "audit:vpn_expire"], order


# Reconciliation worker removed: DB is source of truth; no background Xray state diffing.
