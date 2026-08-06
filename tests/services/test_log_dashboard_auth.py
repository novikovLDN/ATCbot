"""Вход в дашборд и отказы авторизации оставляют след.

ЧТО БЫЛО СЛОМАНО

    Провалы входа по паролю писались (DASHBOARD_LOGIN_FAILED), а успех — нет.
    Разбор «кто поменял цены / выдал себе VIP / удалил пользователя» начинать
    было не с чего: подобранный пароль и штатный вход по логам неотличимы,
    потому что видны ТОЛЬКО неудачные попытки.

    Вход по passkey не писался ни при успехе, ни при провале, и он не
    ограничен `_login_attempts` — то есть перебор по этому пути был полностью
    невидим.

    Установка пароля (`/auth/setup`) выдаёт сессию сразу же. Если magic-ссылку
    прочитал не владелец, это захват админки в один вызов — и он не оставлял
    ни строчки.

    `app/api/dashboard/deps.py` не писал НИ ОДНОГО отказа авторизации: перебор
    токенов по любому эндпоинту админского API был невидим целиком.

ЧЕГО ЗДЕСЬ СОЗНАТЕЛЬНО НЕТ

    Проверки на то, что в записи лежит сам токен или пароль. Их там быть не
    должно, и тест на это стоит ниже отдельным пунктом
    (`test_rejection_never_carries_the_token`).
"""
import inspect
import logging
from unittest.mock import AsyncMock, MagicMock

from fastapi import Depends

import pytest

from app.api.dashboard import auth as auth_mod
from app.api.dashboard import deps as deps_mod


def _request(path: str = "/api/users", ip: str = "10.0.0.7"):
    req = MagicMock()
    req.url.path = path
    req.client.host = ip
    req.headers = {}
    return req


# ── Вход по паролю ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_password_login_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(
        auth_mod.admin_auth, "get_credentials",
        AsyncMock(return_value={"username": "atlas", "password_hash": "h"}),
    )
    monkeypatch.setattr(auth_mod.admin_auth, "verify_password", lambda p, h: True)
    monkeypatch.setattr(auth_mod.admin_auth, "create_session", AsyncMock(return_value="t"))

    body = auth_mod.LoginRequest(username="atlas", password="secret")
    with caplog.at_level(logging.INFO):
        await auth_mod.auth_login(body, response=MagicMock(), request=_request())

    logged = [r.getMessage() for r in caplog.records if "DASHBOARD_LOGIN_OK" in r.getMessage()]
    assert logged, "успешный вход в дашборд не записан"
    assert "method=password" in logged[0]
    assert "10.0.0.7" in logged[0], "без IP запись не отвечает на «откуда вошли»"


@pytest.mark.asyncio
async def test_password_is_never_in_the_login_record(monkeypatch, caplog):
    monkeypatch.setattr(
        auth_mod.admin_auth, "get_credentials",
        AsyncMock(return_value={"username": "atlas", "password_hash": "h"}),
    )
    monkeypatch.setattr(auth_mod.admin_auth, "verify_password", lambda p, h: True)
    monkeypatch.setattr(auth_mod.admin_auth, "create_session", AsyncMock(return_value="t"))

    body = auth_mod.LoginRequest(username="atlas", password="SUPERSECRETPASS")
    with caplog.at_level(logging.INFO):
        await auth_mod.auth_login(body, response=MagicMock(), request=_request())

    assert not any("SUPERSECRETPASS" in r.getMessage() for r in caplog.records)


# ── Вход по passkey ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_passkey_login_success_and_failure_are_logged(monkeypatch, caplog):
    """Второй путь входа не ограничен счётчиком попыток, поэтому запись —
    единственный способ вообще увидеть перебор по нему."""
    passkeys = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules, "app.services.admin_passkeys", passkeys,
    )
    monkeypatch.setattr(auth_mod.admin_auth, "create_session", AsyncMock(return_value="t"))
    body = auth_mod.PasskeyAuthVerifyRequest(challenge_token="x" * 10, credential={})

    passkeys.verify_authentication = AsyncMock(return_value=(False, "bad_signature"))
    with caplog.at_level(logging.INFO):
        with pytest.raises(auth_mod.HTTPException):
            await auth_mod.passkey_auth_verify(body, MagicMock(), request=_request())
    failed = [r.getMessage() for r in caplog.records if "DASHBOARD_PASSKEY_AUTH_FAILED" in r.getMessage()]
    assert failed, "провал входа по passkey не записан"
    assert "bad_signature" in failed[0]

    caplog.clear()
    passkeys.verify_authentication = AsyncMock(return_value=(True, None))
    with caplog.at_level(logging.INFO):
        await auth_mod.passkey_auth_verify(body, MagicMock(), request=_request())
    ok = [r.getMessage() for r in caplog.records if "DASHBOARD_LOGIN_OK" in r.getMessage()]
    assert ok, "успешный вход по passkey не записан"
    assert "method=passkey" in ok[0], (
        "вход по ключу пишется другим маркером — «кто входил» перестаёт "
        "отвечаться одним grep'ом"
    )


# ── Установка пароля ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setting_credentials_is_logged(monkeypatch, caplog):
    """set_credentials + немедленная сессия — захват аккаунта в один вызов."""
    monkeypatch.setattr(auth_mod, "verify_token", lambda _t: {"role": "admin", "sub": "42"})
    monkeypatch.setattr(auth_mod.admin_auth, "is_admin", lambda tg: True)
    monkeypatch.setattr(auth_mod.admin_auth, "credentials_exist", AsyncMock(return_value=False))
    monkeypatch.setattr(auth_mod.admin_auth, "set_credentials", AsyncMock(return_value=True))
    monkeypatch.setattr(auth_mod.admin_auth, "create_session", AsyncMock(return_value="t"))

    body = auth_mod.SetupRequest(
        username="atlas", password="verysecret1", bootstrap_token="x" * 20,
    )
    with caplog.at_level(logging.INFO):
        await auth_mod.auth_setup(body, response=MagicMock(), request=_request())

    assert any(
        "DASHBOARD_CREDENTIALS_SET" in r.getMessage() for r in caplog.records
    ), "установка пароля админки не оставила записи"


def test_setup_still_callable_without_a_request():
    """Request добавлен ради IP в записи и обязан иметь умолчание: прямые
    вызовы (в том числе из тестов регресса на одноразовый setup) передают
    два аргумента и не должны падать."""
    sig = inspect.signature(auth_mod.auth_setup)
    assert sig.parameters["request"].default is None


# ── Отказы авторизации к админскому API ───────────────────────────────


@pytest.mark.asyncio
async def test_rejected_admin_api_call_is_logged(caplog):
    """Перебор токенов по эндпоинтам дашборда был невидим целиком."""
    with caplog.at_level(logging.INFO):
        with pytest.raises(deps_mod.HTTPException):
            await deps_mod.require_admin(
                request=_request("/api/users"), creds=None, atlas_admin_session=None,
            )

    rejected = [r.getMessage() for r in caplog.records if "DASHBOARD_AUTH_REJECTED" in r.getMessage()]
    assert rejected, "отказ авторизации не записан"
    assert "/api/users" in rejected[0], "без пути непонятно, что именно перебирают"
    assert "10.0.0.7" in rejected[0], "без IP перебор не с чем связать"


@pytest.mark.asyncio
async def test_expired_token_rejection_names_the_reason(monkeypatch, caplog):
    monkeypatch.setattr(deps_mod, "verify_token", lambda _t: None)
    creds = MagicMock()
    creds.credentials = "expired.jwt.value"

    with caplog.at_level(logging.INFO):
        with pytest.raises(deps_mod.HTTPException):
            await deps_mod.require_admin(
                request=_request("/api/pricing"), creds=creds, atlas_admin_session=None,
            )

    rejected = [r.getMessage() for r in caplog.records if "DASHBOARD_AUTH_REJECTED" in r.getMessage()]
    assert any("invalid_or_expired_token" in m for m in rejected)


@pytest.mark.asyncio
async def test_rejection_never_carries_the_token(monkeypatch, caplog):
    """Запись об отказе не должна становиться местом, где предъявительский
    токен утекает в лог: 401 не значит, что токен нерабочий в другом месте."""
    monkeypatch.setattr(deps_mod, "verify_token", lambda _t: {"role": "user"})
    creds = MagicMock()
    creds.credentials = "TOKENVALUE_THAT_MUST_NOT_LEAK"

    with caplog.at_level(logging.INFO):
        with pytest.raises(deps_mod.HTTPException):
            await deps_mod.require_admin(
                request=_request(), creds=creds, atlas_admin_session=None,
            )

    assert not any(
        "TOKENVALUE_THAT_MUST_NOT_LEAK" in r.getMessage() for r in caplog.records
    ), "токен попал в запись об отказе"


@pytest.mark.asyncio
async def test_valid_session_is_not_logged_as_rejection(monkeypatch, caplog):
    """Обратная сторона: рабочая сессия не должна плодить записи об отказах,
    иначе поток DASHBOARD_AUTH_REJECTED станет бесполезным."""
    monkeypatch.setattr(deps_mod.admin_auth, "lookup_session", AsyncMock(return_value=42))
    monkeypatch.setattr(deps_mod.admin_auth, "is_admin", lambda tg: True)

    with caplog.at_level(logging.INFO):
        out = await deps_mod.require_admin(
            request=_request(), creds=None, atlas_admin_session="cookie",
        )

    assert out["sub"] == 42
    assert not [r for r in caplog.records if "DASHBOARD_AUTH_REJECTED" in r.getMessage()]


@pytest.mark.asyncio
async def test_one_rejected_request_writes_one_record(monkeypatch, caplog):
    """Кука и Bearer — две попытки ОДНОГО запроса, отказ у запроса один.

    Если писать провал куки отдельно, каждый заход браузера с протухшей
    сессией даст две строки, и по потоку DASHBOARD_AUTH_REJECTED нельзя
    будет ответить даже на «сколько было отказов».
    """
    monkeypatch.setattr(deps_mod.admin_auth, "lookup_session", AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO):
        with pytest.raises(deps_mod.HTTPException):
            await deps_mod.require_admin(
                request=_request(), creds=None, atlas_admin_session="stale-cookie",
            )

    rejected = [r for r in caplog.records if "DASHBOARD_AUTH_REJECTED" in r.getMessage()]
    assert len(rejected) == 1, f"на один отказ записано строк: {len(rejected)}"
    assert "session_not_found" in rejected[0].getMessage(), (
        "итоговая причина потеряла то, на чём споткнулась кука"
    )


def test_fastapi_really_injects_the_request():
    """`request: Request = None` — умолчание стоит ради прямых вызовов, но
    FastAPI обязан подставить настоящий объект по аннотации. Иначе путь и IP
    в записях об отказе навсегда останутся «?», и правка окажется пустой.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/probe")
    async def probe(admin: dict = Depends(deps_mod.require_admin)):  # noqa: B008
        return admin

    # caplog сюда не годится: запрос обслуживается в отдельном потоке
    # TestClient'а, поэтому вешаем свой обработчик прямо на логгер deps.
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect()
    deps_logger = logging.getLogger(deps_mod.__name__)
    deps_logger.addHandler(handler)
    try:
        with TestClient(app) as client:
            resp = client.get("/probe")
    finally:
        deps_logger.removeHandler(handler)

    assert resp.status_code == 401
    rejected = [r.getMessage() for r in records if "DASHBOARD_AUTH_REJECTED" in r.getMessage()]
    assert rejected, "отказ на реальном запросе не записан"
    assert "path=/probe" in rejected[0], (
        f"FastAPI не подставил Request — путь в записи потерян: {rejected[0]}"
    )


def test_no_silent_handler_left_on_the_auth_path():
    """`except Exception: pass` на пути аутентификации прятал отказ базы:
    кнопка входа по ключу молча исчезала с экрана."""
    src = inspect.getsource(auth_mod.auth_status)
    assert "except Exception:\n        pass" not in src
    assert "DASHBOARD_PASSKEY_COUNT_FAILED" in src
