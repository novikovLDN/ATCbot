"""CSV export endpoints — streamed line-by-line.

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets.

ЛОГИ
    Оба обработчика пишут в лог, и в записи есть число строк. Выгрузка — это
    вынос всей базы пользователей за пределы панели одним файлом, и по логу
    должно быть видно, кто и сколько выгрузил.
"""
import csv
import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

import database
from app.api.dashboard.deps import require_admin
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


def _stringify(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return ""
    return str(value)


def _csv_stream(rows: list, columns: list[str] | None = None):
    """Generator that yields CSV chunks as bytes. Header first, then rows."""
    if not rows:
        # Empty result: emit just a sentinel empty CSV with no header so the
        # caller's download still completes.
        yield b""
        return

    if columns is None:
        columns = list(rows[0].keys())

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")

    writer.writerow(columns)
    yield buf.getvalue().encode("utf-8")
    buf.seek(0)
    buf.truncate()

    # Flush in 500-row chunks to keep the per-write cost small without
    # spamming the network with one frame per row.
    flush_every = 500
    written = 0
    for row in rows:
        writer.writerow([_stringify(row.get(c)) for c in columns])
        written += 1
        if written % flush_every == 0:
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate()
    # Final tail
    tail = buf.getvalue()
    if tail:
        yield tail.encode("utf-8")


@router.get("/users.csv")
async def export_users():
    """All users — id, telegram_id, username, language, balance, created_at, etc."""
    try:
        rows = await database.get_all_users_for_export()
    except Exception as e:
        logger.error("export.users failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"export_users_failed: {scrub_secrets(e)}")
    logger.info("export.users ok: rows=%s", len(rows))
    name = f"users_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        _csv_stream(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/subscriptions.csv")
async def export_subscriptions():
    """All currently active subscriptions."""
    try:
        rows = await database.get_active_subscriptions_for_export()
    except Exception as e:
        logger.error("export.subscriptions failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"export_subscriptions_failed: {scrub_secrets(e)}")
    logger.info("export.subscriptions ok: rows=%s", len(rows))
    name = f"subscriptions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        _csv_stream(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
