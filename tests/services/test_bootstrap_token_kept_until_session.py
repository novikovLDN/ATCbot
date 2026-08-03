"""Bootstrap-токен нельзя выбрасывать до подтверждённой сессии.

Дефект. После первичной настройки пароля App.tsx делал auth.clear()
безусловно, прямо в onDone, и только потом шёл спрашивать /auth/status.
Ветка setup доступна только пока в localStorage лежит токен из
magic-ссылки, а сам /auth/setup одноразовый — повторный вызов даёт 409.

Сценарий потери доступа. set_credentials прошёл, а ответ с Set-Cookie не
долетел (оборвалась сеть, Safari выкинул куку в standalone-контексте).
Токен уже стёрт: сессии нет, второй setup невозможен, пароль админ может
не помнить или опечататься при вводе. Остаётся «Сбросить пароль» в боте,
который заодно сносит все passkey.

Чиним ровно преждевременное удаление. Одноразовость setup — осознанная
защита: magic-ссылка живёт в переписке вечно, второй setup по ней это
смена пароля без знания старого (см. tests/services/test_pwa_setup_recovery.py).
"""
import re
from pathlib import Path

import pytest

APP = Path("dashboard/src/App.tsx")


@pytest.fixture()
def setup_branch() -> str:
    """JSX-ветка <SetupPassword …> целиком."""
    src = APP.read_text(encoding="utf-8")
    return src.split("<SetupPassword", 1)[1].split("/>", 1)[0]


def test_status_check_reports_whether_the_session_is_real():
    """Без возвращаемого значения из refresh решать нечем."""
    src = APP.read_text(encoding="utf-8")
    assert "const refresh = useCallback(async (): Promise<boolean>" in src
    body = src.split("const refresh = useCallback", 1)[1].split("}, []);", 1)[0]
    assert "return true;" in body and "return false;" in body


def test_token_is_cleared_only_after_a_confirmed_session(setup_branch):
    """Порядок важен: сначала подтверждение, потом очистка."""
    assert "const ok = await refresh();" in setup_branch
    assert "if (ok) auth.clear();" in setup_branch

    clear_at = setup_branch.index("auth.clear()")
    refresh_at = setup_branch.index("await refresh()")
    assert refresh_at < clear_at, "токен стирается раньше проверки сессии"


def test_token_is_not_cleared_unconditionally(setup_branch):
    """Ровно тот дефект, который чиним: голый auth.clear() в onDone."""
    assert not re.search(r"^\s*auth\.clear\(\);\s*$", setup_branch, re.M)


@pytest.mark.asyncio
async def test_setup_is_still_one_shot(monkeypatch):
    """Страховка от «починю PWA, разрешив setup ещё раз».

    Ссылка из /admin остаётся в переписке навсегда. Разреши второй
    setup — и любой, кто пролистал чат, меняет пароль без знания старого.
    """
    from unittest.mock import AsyncMock

    from app.api.dashboard import auth as auth_mod

    monkeypatch.setattr(
        auth_mod, "verify_token", lambda _t: {"role": "admin", "sub": "42"},
    )
    monkeypatch.setattr(auth_mod.admin_auth, "is_admin", lambda tg: True)
    monkeypatch.setattr(
        auth_mod.admin_auth, "credentials_exist", AsyncMock(return_value=True),
    )
    set_creds = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_mod.admin_auth, "set_credentials", set_creds)

    body = auth_mod.SetupRequest(
        username="atlas", password="verysecret1", bootstrap_token="x" * 20,
    )
    with pytest.raises(auth_mod.HTTPException) as e:
        await auth_mod.auth_setup(body, response=None)

    assert e.value.status_code == 409
    assert set_creds.await_count == 0


def test_retained_token_is_actually_usable():
    """Смысл сохранения токена: он ещё работает как Bearer для API.
    Если бы бэкенд его не принимал, сохранять было бы нечего."""
    deps = Path("app/api/dashboard/deps.py").read_text(encoding="utf-8")
    assert "HTTPBearer" in deps
    api = Path("dashboard/src/lib/api.ts").read_text(encoding="utf-8")
    assert 'headers.set("Authorization", `Bearer ${token}`)' in api
