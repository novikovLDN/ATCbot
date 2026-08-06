"""Подарочные ссылки на гигабайты обхода: создание, список, разбор, удаление.

GET    /bgift/summary                — числа по всем ссылкам
GET    /bgift/list                   — страница списка
GET    /bgift/{link_id}              — одна ссылка
GET    /bgift/{link_id}/redemptions  — кто активировал
POST   /bgift                        — создать
DELETE /bgift/{link_id}              — мягко удалить

ПОРЯДОК МАРШРУТОВ
    Литеральные /summary и /list объявлены ПЕРЕД /{link_id}. Путь с
    параметром перехватывает любой односегментный путь, объявленный
    ниже: переставите — список начнёт отвечать 422, потому что «list»
    не число. Появится новый литеральный путь — ставьте его выше
    /{link_id}, а не в конец файла.

ССЫЛКА СОБИРАЕТСЯ ЗДЕСЬ, А НЕ НА КЛИЕНТЕ
    t_me_url отдаётся сервером и берёт имя бота из config.BOT_USERNAME.
    Раньше экран склеивал диплинк сам из строки, зашитой в JSX, и там
    стояло другое имя бота — ссылка копировалась битой, а понять это
    можно было только попробовав перейти.

СКОЛЬКО УЖЕ ПОТРАЧЕНО
    /bgift/{link_id} возвращает redemption_count рядом с max_uses.
    Раньше карточка знала только предел, но не расход: «максимум 100»
    без «использовано 97» ничего не сообщает о том, кончается ссылка
    или нет.

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

import config
import database
from app.api.dashboard.deps import require_admin
from app.events import bus
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize(value):
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


def _bot_username() -> str:
    """Имя бота для диплинка. Пусто — отдадим короткую форму, экран
    покажет её как есть; это лучше, чем ссылка на несуществующего бота."""
    return (
        getattr(config, "BOT_USERNAME", None)
        or getattr(config, "TELEGRAM_BOT_USERNAME", "")
        or ""
    )


def _gift_url(code: str) -> str:
    """Диплинк вида t.me/<bot>?start=bgift_<КОД>.

    Префикс bgift_ разбирается в app/handlers/user/start/command.py.
    Поменяете здесь — ссылки перестанут срабатывать, а бот ответит
    обычным приветствием, будто кода и не было.
    """
    bot = _bot_username()
    if not bot:
        return f"?start=bgift_{code}"
    return f"https://t.me/{bot}?start=bgift_{code}"


@router.get("/summary")
async def bgift_summary():
    try:
        data = await database.get_bypass_gift_links_summary()
    except Exception as e:
        logger.error("bgift.summary failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"summary_failed: {scrub_secrets(e)}")
    logger.info("bgift.summary ok")
    return _serialize(data or {})


@router.get("/list")
async def bgift_list(
    page: int = Query(0, ge=0),
    page_size: int = Query(20, gt=0, le=200),
    include_deleted: bool = Query(False),
):
    try:
        rows = await database.list_bypass_gift_links(
            include_deleted=include_deleted,
            limit=page_size,
            offset=page * page_size,
        )
    except Exception as e:
        logger.error("bgift.list failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"list_failed: {scrub_secrets(e)}")
    out = []
    for row in rows or []:
        item = _serialize(row)
        item["t_me_url"] = _gift_url(str(item.get("code") or ""))
        out.append(item)
    logger.info("bgift.list ok: %d ссылок", len(out))
    return out


@router.get("/{link_id}")
async def bgift_detail(link_id: int = Path(..., gt=0)):
    try:
        row = await database.get_bypass_gift_link_by_id(link_id)
    except Exception as e:
        logger.error("bgift.detail failed id=%s: %s", link_id, scrub_secrets(e))
        raise HTTPException(500, f"detail_failed: {scrub_secrets(e)}")
    if not row:
        raise HTTPException(404, "Link not found")
    out = _serialize(row)
    out["t_me_url"] = _gift_url(str(out.get("code") or ""))
    # Расход считаем отдельным запросом: сама строка ссылки его не
    # хранит, а без него max_uses на экране — число без смысла.
    # Отказ счётчика не роняет карточку: лучше показать ссылку без
    # расхода, чем не показать ничего.
    try:
        out["redemption_count"] = await database.count_bypass_gift_link_redemptions(
            link_id,
        )
    except Exception as e:
        logger.warning(
            "bgift.detail: счётчик активаций не сошёлся id=%s: %s",
            link_id, scrub_secrets(e),
        )
        out["redemption_count"] = None
    logger.info("bgift.detail ok id=%s", link_id)
    return out


@router.get("/{link_id}/redemptions")
async def bgift_redemptions(
    link_id: int = Path(..., gt=0),
    limit: int = Query(100, gt=0, le=1000),
):
    try:
        rows = await database.get_bypass_gift_link_redemptions(link_id, limit)
        total = await database.count_bypass_gift_link_redemptions(link_id)
    except Exception as e:
        logger.error(
            "bgift.redemptions failed id=%s: %s", link_id, scrub_secrets(e),
        )
        raise HTTPException(500, f"redemptions_failed: {scrub_secrets(e)}")
    logger.info(
        "bgift.redemptions ok id=%s: показано %d из %d",
        link_id, len(rows or []), total,
    )
    return {
        "rows": _serialize(rows or []),
        "total": total,
    }


class GiftLinkCreate(BaseModel):
    gb_amount: int = Field(..., gt=0, le=1024)
    validity_days: int = Field(..., gt=0, le=365)
    max_uses: int = Field(..., gt=0, le=10000)


@router.post("")
async def bgift_create(
    body: GiftLinkCreate,
    admin: dict = Depends(require_admin),
):
    try:
        row = await database.create_bypass_gift_link(
            created_by=int(admin["sub"]),
            gb_amount=body.gb_amount,
            validity_days=body.validity_days,
            max_uses=body.max_uses,
        )
    except Exception as e:
        logger.error("bgift.create failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"create_failed: {scrub_secrets(e)}")
    if not row:
        logger.error("bgift.create failed: база не вернула строку")
        raise HTTPException(500, "create_failed")
    bus.publish({
        "type": "bgift:created",
        "link_id": row.get("id"),
        "code": row.get("code"),
        "by": admin.get("sub"),
    })
    logger.info(
        "bgift.create id=%s: %s ГБ, %s дней, до %s активаций, by admin=%s",
        row.get("id"), body.gb_amount, body.validity_days, body.max_uses,
        admin.get("sub"),
    )
    out = _serialize(row)
    out["t_me_url"] = _gift_url(str(out.get("code") or ""))
    return out


@router.delete("/{link_id}")
async def bgift_delete(
    link_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Мягкое удаление: ссылка перестаёт работать, уже выданные
    гигабайты остаются у людей и в статистике."""
    try:
        ok = await database.soft_delete_bypass_gift_link(link_id)
    except Exception as e:
        logger.error("bgift.delete failed id=%s: %s", link_id, scrub_secrets(e))
        raise HTTPException(500, f"delete_failed: {scrub_secrets(e)}")
    if not ok:
        raise HTTPException(404, "Link not found")
    bus.publish({
        "type": "bgift:deleted",
        "link_id": link_id,
        "by": admin.get("sub"),
    })
    logger.info("bgift.delete id=%s by admin=%s", link_id, admin.get("sub"))
    return {"ok": True}
