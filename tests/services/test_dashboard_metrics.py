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


@pytest.mark.parametrize("label, sub", [
    ("ARPU", "на всю базу"),
    ("LTV", "на платящего"),
])
def test_dashboard_labels_say_what_is_measured(label, sub):
    """Подпись «на юзера» у ARPU была неверной и маскировала совпадение."""
    src = Path("dashboard/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
    idx = src.index(f'label="{label}"')
    window = src[idx: idx + 260]
    assert f'sub="{sub}"' in window, f"карточка {label}: подпись не объясняет знаменатель"
