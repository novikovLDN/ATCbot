"""Лимит размера тела запроса нельзя обойти chunked-передачей.

Дефект: RequestSizeLimitMiddleware проверял только заголовок
Content-Length. Клиент, отправляющий тело с `Transfer-Encoding: chunked`,
такого заголовка не шлёт — ветка проверки не срабатывала, и запрос
любого размера доходил до обработчика. Дальше upload_photo делал
`await file.read()` целиком в память.

Почему это важно: бот, вебхуки Telegram и дашборд живут в одном
процессе. Один chunked-POST на сотни мегабайт раздувал память до OOM —
и вместе с дашбордом падал приём платёжных вебхуков.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import RequestSizeLimitMiddleware

SMALL_LIMIT = 64 * 1024


@pytest.fixture()
def client():
    """Приложение-эхо с лимитом 64 КБ — чтобы не гонять мегабайты в тестах."""
    app = FastAPI()
    app.add_middleware(RequestSizeLimitMiddleware, max_size=SMALL_LIMIT)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"size": len(body)}

    @app.post("/dashboard/api/broadcasts/upload-photo")
    async def upload(request: Request):
        body = await request.body()
        return {"size": len(body)}

    return TestClient(app)


def _chunked(total: int, chunk: int = 8 * 1024):
    """Генератор тела — httpx превращает его в Transfer-Encoding: chunked,
    то есть запрос уходит БЕЗ Content-Length."""
    sent = 0
    while sent < total:
        n = min(chunk, total - sent)
        sent += n
        yield b"x" * n


def test_chunked_over_limit_rejected(client):
    """Главный дефект: тело без Content-Length больше лимита — 413."""
    r = client.post("/echo", content=_chunked(SMALL_LIMIT * 4))
    assert r.status_code == 413


def test_chunked_under_limit_passes(client):
    """Обычная chunked-загрузка в пределах лимита не должна ломаться."""
    r = client.post("/echo", content=_chunked(1024))
    assert r.status_code == 200
    assert r.json()["size"] == 1024


def test_content_length_over_limit_rejected(client):
    """Старая проверка по заголовку продолжает работать."""
    r = client.post("/echo", content=b"x" * (SMALL_LIMIT + 1))
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_broken_content_length_rejected():
    """Нечисловой Content-Length — 400, а не падение внутри middleware.

    Проверяем на голом ASGI: http-клиент такой заголовок сформировать не
    даст, а злоумышленник шлёт байты руками.
    """
    async def never_called(scope, receive, send):  # pragma: no cover
        raise AssertionError("запрос не должен доходить до приложения")

    mw = RequestSizeLimitMiddleware(never_called, max_size=SMALL_LIMIT)
    scope = {
        "type": "http",
        "path": "/echo",
        "method": "POST",
        "headers": [(b"content-length", b"not-a-number")],
    }
    sent = []

    async def receive():  # pragma: no cover
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    assert sent[0]["status"] == 400


def test_upload_photo_keeps_its_bigger_limit(client):
    """У загрузки фото для рассылки свой потолок 10 МБ: то, что для
    остальных ручек уже перебор, здесь обязано проходить."""
    r = client.post(
        "/dashboard/api/broadcasts/upload-photo",
        content=_chunked(SMALL_LIMIT * 2),
    )
    assert r.status_code == 200
    assert r.json()["size"] == SMALL_LIMIT * 2


def test_upload_photo_limit_is_not_infinite(client):
    """Но и у неё лимит есть — 10 МБ, чтобы `file.read()` в обработчике
    не мог съесть память процесса."""
    over = 10 * 1024 * 1024 + 1
    r = client.post(
        "/dashboard/api/broadcasts/upload-photo",
        content=_chunked(over, chunk=256 * 1024),
    )
    assert r.status_code == 413
