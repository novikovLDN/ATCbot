"""Экраны «Сервис», «Аналитика», «Статистика» и примитивы под ними.

Что здесь закреплено и почему именно тестом, а не «посмотрели глазами»:

1. ССЫЛКА СО СВОДКИ. Блок «Требует внимания» и плитка «Расхождений с панелью»
   ведут на /service?focus=reconciliation, а строка про конкретную подписку —
   ещё и с &tg=<telegram_id>. Параметр tg раньше молча игнорировался: человек
   приходил разбираться с одним пользователем и должен был снова искать его
   глазами в списке кандидатов. Тест держит оба параметра разобранными.

2. ОТКАЗ НЕ ПОКАЗЫВАЕТСЯ КАК ПУСТОТА. Главный дефект старого дашборда:
   упавший запрос рисовался как «записей нет». На экранах «Сервис» и
   «Аналитика» ветка isError обязана стоять ДО ветки пустого списка — иначе
   недоступный бэкенд читается как «всё хорошо, очередь пуста».

3. ГРАФИКОВ RECHARTS В КОДЕ НЕТ. Требование research §7.3 отключить анимацию
   серий (isAnimationActive={false}) выполнено тем, что библиотека из src/
   ушла совсем. Если её вернут — тест напомнит, что вместе с ней возвращается
   и требование про анимацию.

4. КРУГОВЫХ ДИАГРАММ НЕТ. research §7.1: пирог допустим максимум на трёх
   сегментах, а у нас все разрезы (продукты, провайдеры, тарифы) — от восьми.
"""
import re
from pathlib import Path

import pytest

SRC = Path("dashboard/src")
SERVICE = SRC / "pages/Service.tsx"
RECONCILIATION = SRC / "components/ReconciliationSection.tsx"
STAT_CARD = SRC / "components/ui/StatCard.tsx"
EMPTY_STATE = SRC / "components/ui/EmptyState.tsx"
UI_INDEX = SRC / "components/ui/index.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """Только код, без комментариев.

    Комментарии в этих файлах намеренно цитируют убранный дефект — «раньше
    стояло transition-[height]», «был bg-gradient». Искать признаки дефекта
    надо в коде, иначе тест ловит собственное объяснение (тот же приём, что
    в test_revenue_delta_label.py).
    """
    text = _read(path)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def _body(path: Path) -> str:
    """Код без комментариев и без блока импортов.

    Импорты отсортированы по алфавиту, поэтому по ним нельзя судить о
    порядке веток в разметке: EmptyAllClear там всегда раньше EmptyFailure.
    """
    code = _code(path)
    marker = code.find("export function")
    return code[marker:] if marker != -1 else code


# ── 1. Ссылка ?focus=reconciliation[&tg=] ────────────────────────────


def test_service_reads_focus_param():
    """Без разбора focus ссылка со сводки приводит на верх страницы."""
    src = _read(SERVICE)
    assert 'params.get("focus")' in src
    assert '"reconciliation"' in src
    assert 'id="reconciliation"' in src, "якорь, к которому ведёт ссылка"


def test_service_reads_tg_param_and_passes_it_down():
    """tg=<id> обязан доехать до блока сверки, а не потеряться в адресе."""
    src = _read(SERVICE)
    assert 'params.get("tg")' in src, "параметр tg снова игнорируется"
    assert "focusTelegramId" in src
    assert "<ReconciliationSection focusTelegramId=" in src, (
        "блок сверки не получает пользователя, за которым пришли"
    )


def test_reconciliation_expands_the_focused_candidate():
    """Карточка нужного пользователя раскрывается сама и подсвечивается."""
    src = _read(RECONCILIATION)
    assert "focusTelegramId" in src
    assert "focused={focusTelegramId === c.telegram_id}" in src
    # Раскрыта с самого монтирования, а не после отдельного клика.
    assert "useState(focused)" in src
    assert "scrollIntoView" in src


def test_block_scroll_and_row_scroll_do_not_fight():
    """Две прокрутки одновременно утаскивают страницу туда-сюда.

    Когда в адресе есть tg=, до глаз доводит карточка кандидата; прокрутка к
    блоку целиком в этом случае должна быть выключена.
    """
    src = _read(SERVICE)
    assert "focusTelegramId === null" in src, (
        "прокрутка к блоку не отключена при заданном tg — две прокрутки "
        "будут перебивать друг друга"
    )


# ── 2. Отказ рисуется отказом ────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        SRC / "pages/Service.tsx",
        SRC / "pages/Analytics.tsx",
        SRC / "pages/Statistics.tsx",
        SRC / "components/analytics/HourlyActivity.tsx",
        SRC / "components/analytics/ConversionFunnel.tsx",
    ],
)
def test_screens_render_failure_not_emptiness(path: Path):
    """У каждого экрана есть явная ветка отказа с возможностью повторить."""
    src = _read(path)
    assert "isError" in src, f"{path.name}: отказ запроса нигде не разобран"
    assert "EmptyFailure" in src, (
        f"{path.name}: отказ показывается не как отказ — это и есть главный "
        f"дефект старого дашборда"
    )


@pytest.mark.parametrize(
    "path",
    [
        SRC / "pages/Service.tsx",
        SRC / "pages/Analytics.tsx",
        SRC / "pages/Statistics.tsx",
    ],
)
def test_failure_branch_comes_before_empty_branch(path: Path):
    """Порядок веток: сначала отказ, потом пусто.

    Если поменять местами, недоступный сервер покажется как «всё в порядке,
    записей нет» — то есть ровно наоборот.
    """
    body = _body(path)
    assert "EmptyAllClear" in body, f"{path.name}: нет состояния «всё в порядке»"
    assert body.index("EmptyFailure") < body.index("EmptyAllClear"), (
        f"{path.name}: ветка пустоты стоит перед веткой отказа"
    )


def test_settings_does_not_invent_toggle_state_on_error():
    """Три тумблера уведомлений при отказе показывались включёнными."""
    src = _read(SRC / "pages/Settings.tsx")
    assert "flags.isError" in src
    assert "EmptyFailure" in src


# ── 3. Пустые состояния: полный набор и разные смыслы ────────────────


def test_six_distinct_empty_states_exist():
    """Четыре из research §6.4 плюс «нет прав» и «не настроено»."""
    src = _read(EMPTY_STATE)
    for name in (
        "EmptyFirstRun",
        "EmptyFilter",
        "EmptyAllClear",
        "EmptyNotConfigured",
        "EmptyNoAccess",
        "EmptyFailure",
    ):
        assert f"export function {name}" in src, f"нет состояния {name}"
        assert name in _read(UI_INDEX), f"{name} не экспортирован из ui/"


def test_empty_state_docstring_says_when_to_use_which():
    """Набор без правила выбора снова схлопнется в один шаблон."""
    src = _read(EMPTY_STATE)
    head = src[: src.index("function Shell")]
    # В шапке должен быть порядок проверки, а не просто перечень имён.
    assert "EmptyFailure" in head and "EmptyAllClear" in head
    assert "порядок" in head.lower() or "первый подошедший" in head.lower(), (
        "докстринг не объясняет, какой случай когда брать"
    )


def test_empty_filter_has_no_create_button():
    """Классическая ошибка: человек искал существующее, ему предлагают
    завести новое."""
    src = _code(EMPTY_STATE)
    block = src[src.index("export function EmptyFilter"):]
    block = block[: block.index("export function EmptyAllClear")]
    assert "onReset" in block
    assert "actionLabel" not in block, "на пустом фильтре появилась «создать»"


# ── 4. StatCard: без градиента, без иконки, с общим скелетоном ───────


def test_statcard_has_no_gradient_under_the_number():
    """Градиент в области данных меняет контраст числа по диагонали."""
    src = _code(STAT_CARD)
    assert "bg-gradient" not in src, "градиент вернулся под число"
    assert "from-accent/" not in src and "to-bg-card" not in src


def test_statcard_does_not_draw_a_decorative_icon():
    """Иконка «кошелёк» рядом с подписью «Доход» повторяет подпись."""
    src = _code(STAT_CARD)
    # Пропс принимается ради совместимости со старыми вызовами …
    assert "icon?: LucideIcon" in src
    # … но не рисуется: <Icon /> в разметке быть не должно.
    assert "<Icon" not in src, "иконка снова рисуется в каждой карточке"


def test_statcard_uses_the_shared_skeleton():
    """Свой серый прямоугольник не отличим от пустого блока."""
    src = _code(STAT_CARD)
    assert 'from "./Skeleton"' in src
    assert "<Skeleton" in src
    assert 'bg-bg-elevated" />' not in src, "вернулась статичная заглушка"


def test_statcard_stays_importable_from_the_old_path():
    """На @/components/StatCard ссылаются страницы вне этой переделки."""
    src = _read(SRC / "components/StatCard.tsx")
    assert "export { StatCard" in src
    assert "./ui/StatCard" in src


# ── 5. Графики: ни recharts, ни пирогов, ни анимации серий ──────────


def _tsx_sources() -> list[Path]:
    return [p for p in SRC.rglob("*.ts*") if p.is_file()]


def test_no_recharts_imports_left():
    """research §7.3: анимация серий отключается вместе с библиотекой."""
    offenders = [
        p for p in _tsx_sources() if 'from "recharts"' in _code(p)
    ]
    assert not offenders, (
        f"recharts вернулся в {offenders} — вместе с ним возвращается "
        f"требование isAnimationActive={{false}}"
    )


def test_no_pie_charts():
    """research §7.1: больше трёх сегментов — горизонтальные полосы."""
    offenders = [
        p
        for p in _tsx_sources()
        if "<PieChart" in _code(p) or "conic-gradient" in _code(p)
    ]
    assert not offenders, f"круговая диаграмма в {offenders}"


def test_hourly_bars_do_not_animate_on_every_refresh():
    """Столбцы ехали по высоте на каждое обновление ряда."""
    src = _code(SRC / "components/analytics/HourlyActivity.tsx")
    assert "transition-[height]" not in src, (
        "вернулась анимация столбцов — на рабочей панели она мешает читать "
        "числа, пик «переползает» во время чтения"
    )
