"""Разбивка раздела трафика на пакет ничего не потеряла.

app/handlers/traffic.py был на 1268 строк и держал четыре разные вещи:
чтение реального расхода из Remnawave, витрины выбора пакета гигабайтов,
выставление счетов за трафик и выставление счетов за обход белых списков.
Правка любой из них шла посреди трёх остальных. Разрезан на пакет
app/handlers/traffic/ — usage / packs / pay_traffic / pay_bypass плюс общий
_shared.

Главный риск такой операции — забытый include_router: кнопка при этом не
даёт ни одной ошибки в логах, она просто перестаёт отвечать. Поэтому здесь
проверяется не «функция объявлена», а «диспетчер её видит».

Второй риск специфичен для этого раздела: две линейки продуктов почти
одинаковы и различаются только префиксом тарифа (traffic_ против bypass_).
Перепутанный при переносе префикс не ломает ничего видимого — просто после
оплаты выдаётся не тот товар. Ниже это тоже проверяется.
"""
import ast
from pathlib import Path

PKG = Path("app/handlers/traffic")

# Взято из файла до разрезания. Каждое имя — живая кнопка в боте.
TRAFFIC_HANDLERS = {
    # экран расхода
    "callback_traffic_info",
    # витрины выбора пакета
    "callback_buy_traffic", "callback_buy_traffic_extended", "callback_buy_traffic_pack",
    "callback_buy_bypass_only", "callback_buy_bypass_extended", "callback_buy_bypass_pack",
    # счета за трафик
    "callback_traffic_pay_balance", "callback_traffic_pay_card",
    "callback_traffic_pay_sbp", "callback_traffic_pay_lava",
    # счета за обход
    "callback_bypass_pay_balance", "callback_bypass_pay_card", "callback_bypass_pay_sbp",
    "callback_bypass_pay_stars", "callback_bypass_pay_crypto", "callback_bypass_pay_lava",
}

MODULES = ["usage.py", "packs.py", "pay_traffic.py", "pay_bypass.py", "_shared.py"]


def _decorated_functions(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.decorator_list
    }


def test_no_traffic_handler_was_lost():
    found = set()
    for name in MODULES:
        found |= _decorated_functions(PKG / name)
    missing = TRAFFIC_HANDLERS - found
    assert not missing, f"обработчики раздела трафика пропали: {sorted(missing)}"


def test_every_traffic_handler_is_registered():
    """Забытый include_router не даёт ошибки — кнопка просто молчит."""
    from app.handlers import router

    registered = set()

    def walk(r):
        for h in list(r.callback_query.handlers) + list(r.message.handlers):
            registered.add(getattr(h.callback, "__name__", ""))
        for sub in r.sub_routers:
            walk(sub)

    walk(router)
    missing = TRAFFIC_HANDLERS - registered
    assert not missing, f"объявлены, но не подключены: {sorted(missing)}"


def test_package_exports_exactly_what_outside_code_uses():
    """Наружу раздел отдаёт два имени: роутер для сборки колбэков и
    show_traffic_info_message для команды /white. Потеря второго не ломает
    ни один импорт при старте — команда просто падает у пользователя."""
    import app.handlers.traffic as traffic

    assert hasattr(traffic, "traffic_router")
    assert hasattr(traffic, "show_traffic_info_message")


def test_old_flat_module_is_gone():
    """Файл заменён пакетом целиком: две копии кода дадут два роутера, и
    один из них останется неподключённым."""
    assert not Path("app/handlers/traffic.py").exists()


def test_product_lines_did_not_get_mixed_up():
    """Линейки трафика и обхода различаются ТОЛЬКО префиксом тарифа.
    Перепутанный префикс ничего не ломает видимо — просто после оплаты
    выдаётся не тот товар."""
    pay_traffic = (PKG / "pay_traffic.py").read_text(encoding="utf-8")
    pay_bypass = (PKG / "pay_bypass.py").read_text(encoding="utf-8")

    assert 'tariff=f"traffic_{gb}gb"' in pay_traffic
    assert 'tariff=f"bypass_{gb}gb"' not in pay_traffic
    assert 'tariff=f"bypass_{gb}gb"' in pay_bypass
    assert 'tariff=f"traffic_{gb}gb"' not in pay_bypass


def test_stars_invoice_stays_in_stars():
    """У Stars в price_kopecks лежит количество звёзд, а не копейки —
    валюта XTR и пустой provider_token. Трактовка поля как копеек ломает
    и счёт, и последующую сверку платежей."""
    src = (PKG / "pay_bypass.py").read_text(encoding="utf-8")
    assert 'currency="XTR"' in src
    assert "price_kopecks=price_stars" in src


def test_shared_helpers_do_not_import_screens():
    """Общий модуль не должен знать про экраны — иначе кольцо импортов и
    он перестаёт быть общим."""
    src = (PKG / "_shared.py").read_text(encoding="utf-8")
    for sibling in ("usage", "packs", "pay_traffic", "pay_bypass"):
        assert f"from .{sibling}" not in src and f"import {sibling}" not in src


def test_subscription_url_is_never_shown_raw():
    """Ссылку на подписку пользователь видит только запечатанной в
    deeplink. Отдать traffic["subscriptionUrl"] напрямую — отдать ключ."""
    src = (PKG / "usage.py").read_text(encoding="utf-8")
    wrapped = src.count('happ_crypto.format_for_user(traffic.get("subscriptionUrl"')
    assert wrapped == 2, (
        f"оба экрана расхода обязаны заворачивать ссылку, обёрнуто {wrapped}"
    )
