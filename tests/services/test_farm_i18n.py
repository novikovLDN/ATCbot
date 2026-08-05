"""Ферма и шторм должны говорить на языке пользователя.

Дефект: в отличие от боулинга и кубиков, вся ферма была собрана из русских
строковых литералов — баннер шторма, названия грядок, кнопки, диалог
выкапывания, экраны оплаты плёнки, все алерты и названия культур. Язык
пользователя в обработчиках вычислялся, но никуда не передавался. Человек с
языком en/de/ar/kk/tj/uz открывал «🌾 Ферма» и получал полностью русский
экран; для арабского это ещё и ломает направление текста.

Тест сторожит два условия: в коде фермы не осталось русских литералов,
уходящих на экран, и каждый запрошенный ключ существует во всех семи
словарях (иначе фолбэк вернёт русский текст — то, с чего начали).
"""
import ast
import re
from pathlib import Path

import pytest

LANGS = ["ru", "en", "de", "ar", "kk", "tj", "uz"]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# Ферма разрезана на пакет: правила, экран, грядки, шторм. Тексты живут в
# трёх из четырёх модулей, поэтому сканируем весь пакет целиком — иначе
# проверка молча перестанет видеть половину экранов.
FARM_SRCS = sorted(p for p in Path("app/handlers/farm").glob("*.py"))

# Ключи собираются конкатенацией: get_text(lang, "farm.plant_" + key).
_KEY_LITERAL = re.compile(r'i18n_get_text\(\s*\w+,\s*[\'"]([\w.]+)[\'"]')


def _module_trees():
    assert FARM_SRCS, "пакет фермы не найден — проверка ничего не сканирует"
    return [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in FARM_SRCS]


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                ids.add(id(node.body[0].value))
    return ids


def _log_and_ledger_ids(tree):
    """Строки, которые пользователь не видит: логи и запись в реестр операций.

    Логи читает разработчик — переводить их вредно. description уходит в
    balance_transactions, это внутренняя бухгалтерия, а не интерфейс.
    """
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_log = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "logger"
        )
        targets = []
        if is_log:
            targets = list(node.args)
        for kw in node.keywords:
            if kw.arg in ("description", "comment"):
                targets.append(kw.value)
        for t in targets:
            for sub in ast.walk(t):
                ids.add(id(sub))
    return ids


def test_no_russian_literals_left_on_screen():
    offenders = []
    for path, tree in _module_trees():
        skip = _docstring_ids(tree) | _log_and_ledger_ids(tree)
        offenders += [
            (f"{path}:{node.lineno}", node.value[:60])
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and CYRILLIC.search(node.value)
        ]
    assert not offenders, f"русский текст в коде фермы: {offenders}"


def _used_keys():
    keys = set()
    for path in FARM_SRCS:
        keys |= set(_KEY_LITERAL.findall(path.read_text(encoding="utf-8")))
    return keys


def test_farm_asks_for_a_meaningful_number_of_keys():
    """Страховка от «регулярка перестала находить вызовы»."""
    assert len(_used_keys()) >= 30


@pytest.mark.parametrize("lang", LANGS)
def test_every_key_exists_in_every_language(lang):
    from app.i18n import LANGUAGES

    keys = LANGUAGES[lang]
    missing = sorted(
        k for k in _used_keys()
        # «farm.plant_» — префикс собираемого ключа, а не ключ: достаточно,
        # чтобы в словаре нашлась хотя бы одна культура с таким началом.
        if k not in keys and not (k.endswith("_") and any(x.startswith(k) for x in keys))
    )
    assert not missing, f"{lang}: нет ключей {missing}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_plant_has_a_name(lang):
    """Названия культур в PLANT_TYPES русские — это справочник механики.
    На экран они попадают только через farm.plant_<ключ>."""
    from app.handlers.game import PLANT_TYPES
    from app.i18n import LANGUAGES

    missing = [k for k in PLANT_TYPES if "farm.plant_" + k not in LANGUAGES[lang]]
    assert not missing, f"{lang}: культуры без названия {missing}"


def test_plant_name_helper_uses_the_dictionary():
    from app.handlers.farm import _plant_name

    assert _plant_name("ru", "oak") == "Дуб"
    assert _plant_name("en", "oak") == "Oak"
    assert _plant_name("ru", "no_such_plant") == ""


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_match_russian(lang):
    """Разошедшийся набор плейсхолдеров — это потерянная сумма или номер
    грядки в тексте: format отработает, а число до человека не дойдёт."""
    from app.i18n import LANGUAGES

    ph = re.compile(r"\{(\w+)")
    broken = []
    for key in _used_keys():
        ru_text = LANGUAGES["ru"].get(key)
        other = LANGUAGES[lang].get(key)
        if ru_text is None or other is None:
            continue
        if set(ph.findall(ru_text)) != set(ph.findall(other)):
            broken.append(key)
    assert not broken, f"{lang}: плейсхолдеры разошлись с русским в {broken}"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", LANGS)
async def test_farm_screen_renders_for_every_language(lang, monkeypatch):
    """Сквозная проверка экрана: ни сырых ключей, ни незаполненных {скобок}.

    Проверять только наличие ключей мало: подстановка живёт в вызывающем
    коде, и перепутанное имя плейсхолдера обнаружится лишь на живом экране.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock

    # Подменять зависимости надо в том модуле, где живёт _render_farm:
    # ферма разрезана на пакет, и патч на app.handlers.farm обработчик
    # экрана уже не увидит — он читает свои собственные globals.
    import app.handlers.farm.screen as farm

    now = datetime.now(timezone.utc)
    plots = [
        {"plot_id": 0, "status": "empty"},
        {"plot_id": 1, "status": "growing", "plant_type": "oak",
         "planted_at": now.isoformat(),
         "ready_at": (now + timedelta(days=30)).isoformat(),
         "dead_at": (now + timedelta(days=31)).isoformat(),
         "storm_shielded": True},
        {"plot_id": 2, "status": "ready", "plant_type": "tomato",
         "ready_at": now.isoformat(), "dead_at": (now + timedelta(hours=20)).isoformat()},
        {"plot_id": 3, "status": "dead", "plant_type": "greens"},
    ]
    monkeypatch.setattr(farm, "resolve_user_language", AsyncMock(return_value=lang))
    monkeypatch.setattr(farm, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(farm, "_get_imminent_storm", AsyncMock(return_value={
        "id": 1, "scheduled_at": now + timedelta(hours=5),
        "announced_at": now, "executed_at": None,
    }))
    monkeypatch.setattr(farm.database, "save_farm_plots", AsyncMock())

    callback = MagicMock()
    callback.from_user = MagicMock()
    callback.from_user.id = 555
    callback.answer = AsyncMock()
    callback.message = MagicMock()

    await farm._render_farm(callback, MagicMock(), plots, plot_count=4, balance=12345)

    text = farm.safe_edit_text.await_args.args[1]
    keyboard = farm.safe_edit_text.await_args.kwargs["reply_markup"]
    screen = text + "\n" + "\n".join(
        b.text for row in keyboard.inline_keyboard for b in row
    )
    assert "farm." not in screen, f"{lang}: на экран ушёл сырой ключ:\n{screen}"
    assert "{" not in screen, f"{lang}: плейсхолдер не подставился:\n{screen}"
    assert "123.45" in screen, f"{lang}: баланс не отформатирован:\n{screen}"


def test_storm_and_payment_screens_are_translated():
    """Самые дорогие для поддержки экраны: шторм и оплата плёнки."""
    used = _used_keys()
    for key in (
        "farm.storm_banner",
        "farm.storm_planting_blocked",
        "farm.shield_payment_title",
        "farm.shield_lava_invoice",
        "farm.shield_sbp_invoice",
        "farm.early_success",
    ):
        assert key in used, f"{key} не запрашивается — экран остался жёстко зашитым"
