"""Одно WebSocket-соединение на вкладку, а не по одному на каждый хук.

Дефект. useEventStream поднимал свой WebSocket в каждом вызывающем
компоненте. На главной хука два — лента платежей (LivePaymentTicker) и
список событий (Dashboard), — значит два сокета на вкладку, две очереди
в шине на сервере (app/events.py), два ping-таймера и два независимых
цикла переподключения. События в них одинаковые: шина вещает всем
подписчикам.

Второй дефект тут же: обработчик на главной звал
qc.invalidateQueries(['stats']) на КАЖДОЕ событие любого типа. Под этим
ключом лежат самые тяжёлые агрегаты страницы, и при всплеске (массовая
регистрация, пачка платежей после рассылки) они перезапрашивались чаще,
чем их собственный refetchInterval.

Тестов на TypeScript в проекте нет и запустить их нечем (в dashboard/
отсутствует node_modules), поэтому проверяем структурно — по исходнику.
"""
import re
from pathlib import Path

WS = Path("dashboard/src/lib/ws.ts")
# Троттлинг переехал вместе с лентой событий: сводку разложили на зоны, и
# подписка на шину теперь живёт там, где рисуется лента, а не в файле
# страницы. Сам дефект от переезда никуда не делся — обработчик по-прежнему
# срабатывает на КАЖДОЕ событие любого типа.
FEED = Path("dashboard/src/components/summary/EventFeed.tsx")
SRC_DIR = Path("dashboard/src")


def test_only_one_place_in_the_app_creates_a_socket():
    """Единственный `new WebSocket` на весь фронт — это и есть синглтон."""
    hits = [
        (p, p.read_text(encoding="utf-8").count("new WebSocket("))
        for p in SRC_DIR.rglob("*.ts*")
    ]
    total = sum(c for _, c in hits)
    assert total == 1, (
        "сокет создаётся в нескольких местах: "
        f"{[str(p) for p, c in hits if c]}"
    )
    assert WS.read_text(encoding="utf-8").count("new WebSocket(") == 1


def test_socket_lives_in_the_module_not_in_the_hook():
    """Сокет и набор подписчиков — модульные, иначе каждый вызов хука
    снова заведёт своё соединение."""
    src = WS.read_text(encoding="utf-8")
    assert re.search(r"^const subscribers = new Set<Handler>\(\);", src, re.M)
    assert re.search(r"^let socket: WebSocket \| null = null;", src, re.M)

    hook = src.split("export function useEventStream", 1)[1]
    assert "new WebSocket" not in hook, "хук снова открывает соединение сам"


def test_every_event_reaches_every_subscriber():
    """Смысл мультиплексирования: один пакет — всем подписчикам."""
    src = WS.read_text(encoding="utf-8")
    assert "for (const h of Array.from(subscribers))" in src, (
        "рассылка подписчикам должна идти по копии набора: обработчик "
        "имеет право отписаться прямо в колбэке"
    )


def test_subscribers_still_come_from_several_components():
    """Если бы вызывающий остался один, синглтон был бы бессмысленным."""
    callers = [
        p for p in SRC_DIR.rglob("*.ts*")
        if p != WS and "useEventStream(" in p.read_text(encoding="utf-8")
    ]
    assert len(callers) >= 3, f"подписчиков стало мало: {callers}"


def test_socket_survives_a_brief_drop_to_zero_subscribers():
    """StrictMode в dev и переход между страницами на миг оставляют ноль
    подписчиков. Рвать соединение ради этого — лишний handshake и дыра,
    в которую проваливаются события."""
    src = WS.read_text(encoding="utf-8")
    assert "IDLE_CLOSE_MS" in src
    assert "idleCloseTimer" in src


def test_stats_invalidation_is_throttled():
    """Инвалидация тяжёлых агрегатов — не чаще раза в несколько секунд."""
    src = FEED.read_text(encoding="utf-8")
    m = re.search(r"const INVALIDATE_THROTTLE_MS = (\d+);", src)
    assert m, "нет константы троттлинга"
    assert int(m.group(1)) >= 5000

    # В обработчике событий — только троттленный вызов.
    handler = src.split("useEventStream(", 1)[1].split("});", 1)[0]
    assert "refresh()" in handler
    assert "invalidateQueries" not in handler, (
        "прямой вызов invalidateQueries в обработчике обходит троттлинг"
    )


def test_throttle_does_not_swallow_the_last_event():
    """Троттлинг с хвостом: последнее событие всплеска обязано доехать,
    иначе экран замрёт на предпоследнем состоянии."""
    src = FEED.read_text(encoding="utf-8")
    body = src.split("const refresh = useCallback", 1)[1].split("}, [qc]);", 1)[0]
    assert "setTimeout" in body, "нет отложенного (хвостового) вызова"
