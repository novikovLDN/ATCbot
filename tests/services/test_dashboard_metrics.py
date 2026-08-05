"""Метрики дашборда: разные карточки должны показывать разные числа.

Дефекты:

1. ARPU и LTV считались одинаково — выручка / платящие. Алгебраически
   SUM(всё)/COUNT(платящих) и AVG(SUM по каждому платящему) это одно и то
   же, поэтому две карточки на дашборде ВСЕГДА показывали одно число. Две
   одинаковые цифры под разными названиями хуже одной: по ним принимают
   решения, считая, что видят разные срезы.

2. Ключа active_subscriptions не существовало: get_extended_bot_stats
   возвращает active_subs. Фронт читает active_subscriptions в трёх местах,
   поэтому карточка «Активных с триалами» всегда пустовала, а fallback в
   stats.py превращался в None.

3. WebSocket пускал по JWT, проверив только подпись и role=='admin', — без
   разбора sub и без admin_auth.is_admin. Токен живёт 30 дней и переживает
   logout: тот отзывает куку, а ссылку с токеном — нет.
"""
import inspect
from pathlib import Path

import pytest


def test_arpu_and_ltv_use_different_denominators():
    """Иначе это одна метрика под двумя названиями."""
    from database import analytics

    arpu = inspect.getsource(analytics.get_arpu)
    ltv = inspect.getsource(analytics.get_average_ltv)

    assert "COUNT(*) FROM users" in arpu, (
        "ARPU снова делится не на всю базу — совпадёт с LTV"
    )
    assert "COUNT(*) FROM users" not in ltv, (
        "LTV должен считаться только по платящим"
    )
    assert "GROUP BY telegram_id" in ltv


def test_arpu_docstring_explains_the_difference():
    """Определение обязано лежать рядом с кодом: следующий, кто будет
    править метрику, должен увидеть, почему знаменатели разные."""
    from database import analytics

    src = inspect.getsource(analytics.get_arpu)
    assert "get_average_ltv" in src


def test_extended_stats_exposes_the_key_the_dashboard_reads():
    """Фронт читает active_subscriptions; отдавать только active_subs
    значит оставить карточку пустой."""
    from database import analytics

    # Берём исходник самой функции, а не поиск по файлу: строку
    # "total_users" возвращают и другие сводки.
    src = inspect.getsource(analytics.get_extended_bot_stats)
    assert '"active_subscriptions"' in src
    assert '"active_subs"' in src, "старое имя нужно существующим потребителям"


def test_ws_checks_that_token_owner_is_still_admin():
    """Проверка обязана совпадать с deps.require_admin."""
    src = Path("app/api/dashboard/ws.py").read_text(encoding="utf-8")
    token_branch = src[src.index("if not authorized and token:"):]
    token_branch = token_branch[: token_branch.index("if not authorized:")]
    assert "admin_auth.is_admin(" in token_branch, (
        "WS пускает по токену без проверки, что владелец — действующий админ"
    )
    assert 'int(payload["sub"])' in token_branch, "sub не разбирается"


@pytest.mark.parametrize("label, hint", [
    ("ARPU", "на всю базу"),
    ("LTV", "на платящего"),
])
def test_analytics_labels_say_what_is_measured(label, hint):
    """Подпись «на юзера» у ARPU была неверной и маскировала совпадение.

    Карточки переехали со сводки на «Аналитику» (research §9.3: ARPU и LTV
    — метрики уровня 2, из них не следует действие в ближайший час). При
    переезде их к тому же расклеили: на главной они жили одной карточкой
    «ARPU / средний LTV» с двумя числами через слэш, где понять, где чьё,
    было нельзя вовсе.
    """
    src = Path("dashboard/src/pages/Analytics.tsx").read_text(encoding="utf-8")
    idx = src.index(f'label="{label}"')
    window = src[idx: idx + 260]
    assert f'hint="{hint}"' in window, (
        f"карточка {label}: подпись не объясняет знаменатель"
    )


class TestPaidSubscriptionCountIncludesCombo:
    """Комбо считается платной подпиской — во всех счётчиках сразу.

    ЧТО БЫЛО

        Список тарифов был выписан руками в двух местах, и в одном из них
        (database/analytics_stats.py) не было combo_basic и combo_plus.
        Комбо — отдельные продукты со своей ценой, а не «plus с добавкой»,
        и в колонке subscription_type у них два представления:
        историческое 'plus' + is_combo=TRUE и явное 'combo_*'. Первое
        попадало в счёт через 'plus', второе не попадало никуда.

        Заметить это по числу нельзя: счётчик не ломается, он просто
        показывает меньше, чем есть. А когда счётчиков стало два — на
        сводке и на аналитике, — они начали расходиться, и понять, какой
        прав, можно было только чтением обоих запросов.
    """

    def test_config_defines_one_list_and_combo_is_in_it(self):
        import config

        assert set(config.COMBO_TARIFF_TYPES) <= set(config.PAID_SUBSCRIPTION_TYPES), (
            "комбо выпало из списка платных тарифов — комбо-подписчиков "
            "перестанут считать, и число просто станет другим"
        )
        assert set(config.VALID_SUBSCRIPTION_TYPES) <= set(config.PAID_SUBSCRIPTION_TYPES)

    def test_both_counters_read_the_same_list(self):
        """Разные списки = два разных ответа на один вопрос."""
        import config
        import database.dashboard_summary as summary

        assert summary._PAID_SUBSCRIPTION_TYPES is config.PAID_SUBSCRIPTION_TYPES, (
            "сводка снова держит свою копию списка тарифов"
        )

    def test_the_query_does_not_hardcode_tariffs(self):
        """Список внутри SQL — это третья копия, которая разъедется следом."""
        import inspect

        import database.analytics_stats as stats

        src = inspect.getsource(stats.get_active_paid_subscriptions_count)
        assert "'basic'" not in src and '"basic"' not in src, (
            "тарифы снова перечислены прямо в запросе"
        )
        assert "PAID_SUBSCRIPTION_TYPES" in src

    def test_a_failed_count_is_not_reported_as_zero(self):
        """Ноль неотличим от «никто не платит».

        Вызывающий (/stats/overview) умеет обработать отказ — он
        подставляет active_subscriptions. Свой перехват отбирал у него эту
        возможность: до запасного варианта дело не доходило.
        """
        import inspect

        import database.analytics_stats as stats

        src = inspect.getsource(stats.get_active_paid_subscriptions_count)
        assert "return 0" not in src.split('"""')[-1], (
            "вернулся перехват, превращающий отказ запроса в ноль на экране"
        )
