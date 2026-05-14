"""
One-shot migration-notice broadcast (Task 3).

Why a dedicated module: the existing `broadcast_service` ships a SHARED
text to a list of users.  This broadcast is per-user-personalised — each
recipient gets their own `remnawave_premium_sub_url` embedded in the
body — so we need a slightly different rendering layer while reusing
the same operational pieces (`safe_send_message`, audit columns, admin
completion report).

The text uses HTML:
  • `<blockquote><code>{url}</code></blockquote>` — Telegram renders
    `<code>` as tap-to-copy (modern clients), wrapped in a blockquote
    block for visual separation.
  • No occurrence of the word "Premium" in the visible body — the
    customer asked for "основные / безлимитные" instead, so users see
    that wording.  We DO log against the column `remnawave_premium_*`,
    that's internal nomenclature.

The keyboard has two buttons:
  • 🔄 Обновить  →  https://{PUBLIC_BASE_URL}/open/happ?url=<sub-url>
                   (Telegram inline buttons accept HTTPS only; the
                   bot's existing /open/happ endpoint returns an HTML
                   page that redirects to happ://add/<base64>.)
  • 💬 Поддержка →  config.SUPPORT_URL (e.g. https://t.me/atlassecure_support)

Idempotency:
  • subscriptions.migration_notice_sent_at is stamped on success.
  • Subsequent broadcast runs skip stamped rows via
    database.list_migration_broadcast_candidates.
  • users.is_reachable=FALSE rows are also skipped (managed by
    safe_send_message on Forbidden / chat-not-found).
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Optional
from urllib.parse import quote, urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from app.utils.telegram_safe import safe_send_message

logger = logging.getLogger(__name__)


# ── Configurable knobs ────────────────────────────────────────────────

MIGRATION_CUTOFF_DATE_STR = "18.05.2026"   # display text in the body
SUPPORT_URL = getattr(config, "SUPPORT_URL", None) or "https://t.me/Atlas_SupportSecurity"
BROADCAST_RATE_PER_SEC = int(getattr(config, "BROADCAST_RATE_PER_SEC", 20) or 20)
BROADCAST_CONCURRENCY = int(getattr(config, "MIGRATION_BROADCAST_CONCURRENCY", 5) or 5)

# Test-mode placeholder when the admin hits "Test" but doesn't have a
# remnawave_premium_sub_url on their own row (rare — but keeps the
# preview rendering even for a brand-new admin account).
_TEST_PLACEHOLDER_URL = "https://rmnw.atlassecure.ru/api/sub/TEST_PLACEHOLDER"


# ── Body + keyboard rendering ─────────────────────────────────────────

def render_migration_text(premium_subscription_url: str) -> str:
    """Build the HTML body for the migration notice.

    The body uses Telegram-Ads-style custom emoji markup
    `![<glyph>](tg://emoji?id=<id>)` — `app.utils.telegram_safe.
    safe_send_message` runs `convert_tg_emoji` before delivery, which
    rewrites those markers into `<tg-emoji emoji-id="...">…</tg-emoji>`
    tags Telegram understands.

    The URL is wrapped in <blockquote><code>...</code></blockquote> so
    Telegram clients render it as a single-tap-to-copy block.  HTML-
    special chars in the URL are escaped just in case (real URLs are
    safe, but defence in depth).
    """
    url = html.escape(premium_subscription_url or "", quote=False)
    cutoff = html.escape(MIGRATION_CUTOFF_DATE_STR, quote=False)
    return (
        "![🚀](tg://emoji?id=5188481279963715781) <b>Atlas Secure стал лучше — обнови ссылку!</b>\n"
        "\n"
        "Перевели основные серверы на новую инфраструктуру — стало быстрее и стабильнее.\n"
        "\n"
        "![⚠️](tg://emoji?id=5420323339723881652) <b>Только для основных (безлимитных) серверов.</b>\n"
        "![🧩](tg://emoji?id=5265120027853481187) <i>LTE-обходы — ничего менять не нужно.</i>\n"
        "\n"
        "━━━━━━━━━━━━━━━\n"
        "\n"
        "🔧 <b>Как обновить:</b>\n"
        "\n"
        "<b>Автоматически:</b> нажми <b>«🔄 Обновить»</b> ниже — готово.\n"
        "\n"
        f"<blockquote><code>{url}</code></blockquote>\n"
        "\n"
        "![1️⃣](tg://emoji?id=5382322671679708881) Нажми на свой ключ — скопируется сам\n"
        "![2️⃣](tg://emoji?id=5381990043642502553) Happ → <b>«+»</b> → <b>«Добавить из буфера обмена»</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━\n"
        f"![⌛️](tg://emoji?id=5454415424319931791) <b>Старые ссылки отключим {cutoff} — не жди!</b> 👇"
    )


def _bot_public_base_url() -> str:
    """Return https origin where /open/happ?url=... is reachable.

    Prefers PUBLIC_BASE_URL; falls back to the WEBHOOK_URL's origin
    (same domain in Railway deployments).
    """
    base = (getattr(config, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    if base:
        return base
    wh = getattr(config, "WEBHOOK_URL", "") or ""
    try:
        parsed = urlparse(wh)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return ""


def build_happ_deeplink(premium_subscription_url: str) -> Optional[str]:
    """Return the inline-button URL that opens Happ on the user's device.

    Telegram only accepts HTTPS in inline-button `url=` fields, so we
    can't put `happ://` directly there.  The bot's existing
    /open/happ?url=... endpoint serves an HTML page that redirects to
    `happ://add/<base64>`; that's the same pattern used by the auto-
    setup deeplink buttons in app/handlers/callbacks/navigation.py.

    Returns None when we can't determine the public base URL — caller
    should drop the button instead of rendering a broken one.
    """
    base = _bot_public_base_url()
    if not base or not premium_subscription_url:
        return None
    return f"{base}/open/happ?url={quote(premium_subscription_url, safe='')}"


def build_migration_keyboard(premium_subscription_url: str) -> InlineKeyboardMarkup:
    """Build the [🔄 Обновить] [💬 Поддержка] row."""
    row: list[InlineKeyboardButton] = []
    happ = build_happ_deeplink(premium_subscription_url)
    if happ:
        row.append(InlineKeyboardButton(text="🔄 Обновить", url=happ))
    row.append(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_URL))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def render_for_user(premium_subscription_url: str) -> tuple[str, InlineKeyboardMarkup]:
    """Convenience wrapper that returns (text, keyboard) in one call."""
    return (
        render_migration_text(premium_subscription_url),
        build_migration_keyboard(premium_subscription_url),
    )


# ── Per-user send ─────────────────────────────────────────────────────

async def send_migration_notice(
    bot,
    telegram_id: int,
    premium_subscription_url: str,
) -> bool:
    """Send one notice to one user.  Returns True on delivered, False
    on any handled failure (Forbidden / blocked / unknown).  Never raises.

    Caller is responsible for marking
    `subscriptions.migration_notice_sent_at` on True so we don't double-
    send on re-runs.
    """
    text, keyboard = render_for_user(premium_subscription_url)
    result = await safe_send_message(
        bot,
        telegram_id,
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return result is not None


# ── Test (admin only) ────────────────────────────────────────────────

async def send_test_notice_to_admin(bot, admin_telegram_id: int) -> bool:
    """Send a single rendered notice to the admin themselves.

    Uses the admin's own remnawave_premium_sub_url if they have one;
    falls back to a clearly-fake placeholder URL otherwise (so the
    rendering is still testable for a brand-new admin account).
    """
    premium_url = _TEST_PLACEHOLDER_URL
    try:
        import database
        pool = await database.get_pool()
        if pool is not None:
            async with pool.acquire() as conn:
                cached = await conn.fetchval(
                    "SELECT remnawave_premium_sub_url FROM subscriptions "
                    "WHERE telegram_id = $1 AND status = 'active'",
                    admin_telegram_id,
                )
            if cached:
                premium_url = cached
    except Exception as e:
        logger.warning("MIGRATION_TEST_DB_FAIL: tg=%s %s", admin_telegram_id, e)

    return await send_migration_notice(bot, admin_telegram_id, premium_url)


# ── Full broadcast ────────────────────────────────────────────────────

async def run_migration_broadcast(
    bot,
    admin_telegram_id: int,
    *,
    notify_admin_on_complete: bool = True,
) -> dict:
    """Send the migration notice to every active premium user that
    hasn't received it yet.  Mirrors broadcast_service.run_no_subscription_
    broadcast in structure (semaphore + counters + final report) but
    personalises the body per user.

    Returns a stats dict: {success, failed, skipped, total, duration_seconds}.
    """
    import database
    if not getattr(database, "DB_READY", False):
        logger.warning("MIGRATION_BROADCAST_SKIP: db_not_ready")
        return {"success": 0, "failed": 0, "skipped": 0, "total": 0, "duration_seconds": 0.0}

    start_time = time.time()
    counters = {"success": 0, "failed": 0, "skipped": 0}
    counters_lock = asyncio.Lock()

    try:
        candidates = await database.list_migration_broadcast_candidates()
    except Exception:
        logger.exception("MIGRATION_BROADCAST_FETCH_ERROR")
        return {"success": 0, "failed": 0, "skipped": 0, "total": 0, "duration_seconds": 0.0}

    total = len(candidates)
    logger.info("MIGRATION_BROADCAST_STARTED: total=%d", total)

    # Spacing between sends.  20 msg/s → 0.05s gap.  CONCURRENCY_LIMIT
    # is how many sends can be in flight at once; the per-task sleep
    # keeps the aggregate rate under Telegram's 30/sec/bot ceiling.
    sleep_between = max(0.0, 1.0 / max(1, BROADCAST_RATE_PER_SEC))
    semaphore = asyncio.Semaphore(max(1, BROADCAST_CONCURRENCY))

    async def _send_one(row: dict) -> None:
        tg = int(row["telegram_id"])
        url = (row.get("premium_url") or "").strip()
        if not url:
            async with counters_lock:
                counters["skipped"] += 1
            return
        async with semaphore:
            try:
                ok = await send_migration_notice(bot, tg, url)
            except Exception:
                logger.exception("MIGRATION_BROADCAST_SEND_ERROR: tg=%s", tg)
                async with counters_lock:
                    counters["failed"] += 1
                return
            if ok:
                try:
                    await database.mark_migration_notice_sent(tg)
                except Exception:
                    logger.exception("MIGRATION_BROADCAST_MARK_FAIL: tg=%s", tg)
                async with counters_lock:
                    counters["success"] += 1
            else:
                # safe_send_message already marked is_reachable=FALSE on
                # Forbidden / chat-not-found, so we don't double-attempt.
                async with counters_lock:
                    counters["failed"] += 1
            await asyncio.sleep(sleep_between)

    try:
        await asyncio.gather(*[_send_one(row) for row in candidates])
    except asyncio.CancelledError:
        logger.info("MIGRATION_BROADCAST_CANCELLED")
        raise

    duration_seconds = time.time() - start_time
    result = {
        "success": counters["success"],
        "failed": counters["failed"],
        "skipped": counters["skipped"],
        "total": total,
        "duration_seconds": duration_seconds,
    }
    logger.info(
        "MIGRATION_BROADCAST_COMPLETED: total=%d success=%d failed=%d skipped=%d duration=%.1fs",
        total, counters["success"], counters["failed"], counters["skipped"], duration_seconds,
    )

    if notify_admin_on_complete:
        try:
            await bot.send_message(
                admin_telegram_id,
                (
                    f"✅ <b>Migration broadcast completed</b>\n\n"
                    f"📊 Recipients: {total}\n"
                    f"✅ Delivered: {counters['success']}\n"
                    f"❌ Failed: {counters['failed']}\n"
                    f"⏭ Skipped: {counters['skipped']}\n"
                    f"⏱ Duration: {duration_seconds:.1f}s"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("MIGRATION_BROADCAST_NOTIFY_FAIL: %s", e)

    return result


__all__ = [
    "MIGRATION_CUTOFF_DATE_STR",
    "render_migration_text",
    "build_happ_deeplink",
    "build_migration_keyboard",
    "render_for_user",
    "send_migration_notice",
    "send_test_notice_to_admin",
    "run_migration_broadcast",
]
