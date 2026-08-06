"""require_admin — accepts either:
  - session cookie (new, primary)  — set by /api/auth/login or /setup
  - Bearer JWT (legacy)             — kept for curl / API testing

Cookie wins if both are present.
"""
import logging
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from app.api.dashboard.auth import verify_token
from app.services import admin_auth

_log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


def _reject(request: Optional[Request], reason: str, **fields) -> None:
    """Записать отказ авторизации к админскому API.

    ЗАЧЕМ

        Ни один отказ здесь не писался, то есть перебор токенов по ЛЮБОМУ
        эндпоинту дашборда был полностью невидим: в логах видны только
        неудачные попытки на /api/auth/login, а весь остальной админский API
        отвечал 401/403 молча.

        Путь запроса и IP — то, ради чего запись и нужна: они отличают
        «фронт постучался с протухшей кукой» от «кто-то методично перебирает
        токены по /api/users».

    Сам токен не пишется НИКОГДА: он предъявительский, и запись об отказе не
    должна становиться местом, где рабочий ключ утекает в лог.
    """
    path = "?"
    ip = "?"
    if request is not None:
        try:
            path = request.url.path
            ip = request.client.host if request.client else "?"
        except Exception:
            # Логирование не имеет права превратить 401 в 500.
            pass
    tail = " ".join(f"{k}={v}" for k, v in fields.items())
    _log.warning(
        "DASHBOARD_AUTH_REJECTED path=%s ip=%s reason=%s%s",
        path, ip, reason, f" {tail}" if tail else "",
    )


async def require_admin(
    request: Request = None,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    atlas_admin_session: Optional[str] = Cookie(default=None),
) -> dict:
    # Почему причина копится, а не пишется сразу.
    #
    # Кука и Bearer — две попытки одного запроса, и отказ у запроса один.
    # Если писать провал куки отдельно, каждый заход браузера с протухшей
    # сессией даст ДВЕ строки вместо одной, и поток DASHBOARD_AUTH_REJECTED
    # перестанет быть пригодным для «сколько было отказов».
    session_failure: Optional[str] = None

    # 1) Cookie session
    if atlas_admin_session:
        tg = await admin_auth.lookup_session(atlas_admin_session)
        if tg is not None and admin_auth.is_admin(tg):
            return {"sub": tg, "role": "admin", "auth": "session"}
        # Различаем «сессии нет / протухла» и «сессия есть, но не админская»:
        # первое — обычный истёкший вход, второе — аномалия, которую нельзя
        # получить штатным путём.
        session_failure = "session_not_found" if tg is None else "session_not_admin"

    # 2) Bearer (legacy)
    if creds and creds.credentials:
        payload = verify_token(creds.credentials)
        if not payload:
            _reject(request, "invalid_or_expired_token")
            raise HTTPException(401, "Invalid or expired token")
        if payload.get("role") != "admin":
            _reject(request, "role_not_admin", role=payload.get("role"))
            raise HTTPException(403, "Forbidden")
        try:
            sub_id = int(payload["sub"])
        except (TypeError, ValueError, KeyError):
            _reject(request, "invalid_subject")
            raise HTTPException(401, "Invalid subject")
        if not admin_auth.is_admin(sub_id):
            _reject(request, "not_admin", sub=sub_id)
            raise HTTPException(403, "Forbidden")
        purpose = payload.get("purpose")
        if purpose == "magic_link":
            # Ссылка из чата Telegram, используемая как ключ к API. Само по
            # себе это рабочий сценарий входа, но факт должен быть виден:
            # ссылка остаётся в истории чата и её мог увидеть кто угодно.
            _log.info(
                "DASHBOARD_AUTH_VIA_MAGIC_LINK admin=%s — вход по ссылке из чата", sub_id
            )
        return {"sub": sub_id, "role": "admin", "auth": "bearer", "purpose": purpose}

    # Bearer'а не было вовсе: итоговая причина — та, на которой споткнулась
    # кука, а если куки тоже не было — предъявлять было нечего.
    _reject(request, session_failure or "no_credentials")
    raise HTTPException(401, "Missing bearer token")
