"""Рефералы: сводные числа, лидерборд партнёров, карточка и история выплат.

GET /referrals/overall              — числа по всей платформе
GET /referrals/top                  — лидерборд с сортировкой и поиском
GET /referrals/{referrer_id}        — карточка партнёра
GET /referrals/{partner_id}/history — история начислений кешбэка

ПОРЯДОК МАРШРУТОВ
    Литеральные /overall и /top объявлены ПЕРЕД /{referrer_id}. Путь с
    параметром перехватывает любой односегментный путь, объявленный
    ниже: поставите /overall после — экран получит 422 вместо чисел.

ДЕНЬГИ
    total_revenue и cashback приходят из базы уже в рублях
    (database/referral_analytics.py переводит копейки в рубли на выходе).
    Здесь ничего не пересчитывается — второй перевод дал бы сотую долю
    настоящей суммы, и заметить это по экрану невозможно.

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database
from app.api.dashboard.deps import require_admin
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


@router.get("/overall")
async def referrals_overall():
    """Сводные числа партнёрской программы по всей платформе."""
    try:
        data = await database.get_referral_overall_stats()
    except Exception as e:
        logger.error("referrals.overall failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"overall_failed: {scrub_secrets(e)}")
    logger.info("referrals.overall ok")
    return _serialize(data or {})


@router.get("/top")
async def referrals_top(
    # pattern=, а не regex=: последнее удалено в Pydantic v2 и валится
    # предупреждением о снятой поддержке.
    sort_by: str = Query(
        "total_revenue",
        pattern="^(total_revenue|invited_count|cashback_paid)$",
    ),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$"),
    limit: int = Query(50, gt=0, le=500),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, max_length=64),
):
    """Лидерборд партнёров. `q` ищет по telegram_id и username."""
    try:
        rows = await database.get_admin_referral_stats(
            search_query=q,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error("referrals.top failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"top_failed: {scrub_secrets(e)}")
    logger.info(
        "referrals.top ok: %d строк, сортировка %s %s",
        len(rows or []), sort_by, sort_order,
    )
    return _serialize(rows or [])


@router.get("/{referrer_id}")
async def referrer_detail(referrer_id: int = Path(..., gt=0)):
    """Карточка партнёра: кто приглашён и сколько он принёс.

    ДВА ЗАПРОСА, А НЕ ОДИН, И ЭТО НЕ ИЗБЫТОЧНОСТЬ.
        get_admin_referral_detail отдаёт только имя и список приглашённых
        (invited_list) — ни одного итогового числа. Экран же показывает
        «пригласил / купили / доход / кешбэк / текущий процент», и раньше
        читал их прямо из этого ответа: полей там нет, поэтому во всех
        пяти плитках стоял прочерк, а список приглашённых был пуст, потому
        что экран искал ключ invited_users вместо invited_list. Числа
        живут в агрегате лидерборда — берём их оттуда.

        Отказ агрегата не роняет карточку: список приглашённых важнее
        итогов, и показать его без чисел лучше, чем не показать ничего.
    """
    try:
        data = await database.get_admin_referral_detail(referrer_id)
    except Exception as e:
        logger.error(
            "referrals.detail failed id=%s: %s", referrer_id, scrub_secrets(e),
        )
        raise HTTPException(500, f"detail_failed: {scrub_secrets(e)}")
    if not data:
        raise HTTPException(404, "Referrer not found")

    out = _serialize(data)
    try:
        agg = await database.get_admin_referral_stats(
            search_query=str(referrer_id), limit=1,
        )
    except Exception as e:
        logger.warning(
            "referrals.detail: итоги не сошлись id=%s: %s",
            referrer_id, scrub_secrets(e),
        )
        agg = None
    if agg:
        row = _serialize(agg[0])
        # Ключи карточки не перетираем: имя и список приглашённых —
        # из детального запроса, он про них и есть.
        for key, value in row.items():
            out.setdefault(key, value)

    logger.info("referrals.detail ok id=%s", referrer_id)
    return out


@router.get("/{partner_id}/history")
async def referrer_history(
    partner_id: int = Path(..., gt=0),
    limit: int = Query(50, gt=0, le=500),
):
    try:
        rows = await database.get_referral_rewards_history(partner_id, limit)
        total = await database.get_referral_rewards_history_count(partner_id)
    except Exception as e:
        logger.error(
            "referrals.history failed id=%s: %s", partner_id, scrub_secrets(e),
        )
        raise HTTPException(500, f"history_failed: {scrub_secrets(e)}")
    logger.info(
        "referrals.history ok id=%s: показано %d из %d",
        partner_id, len(rows or []), total,
    )
    return {
        "rows": _serialize(rows or []),
        "total": total,
    }
