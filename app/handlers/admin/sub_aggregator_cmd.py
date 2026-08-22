"""
/aggregator — admin-only команда для тестирования sub-aggregator сервиса.

Флоу:
1. Админ пишет /aggregator в боте.
2. Хендлер вызывает sub_aggregator.ensure_pair(admin_tg_id):
   - читает main_url + gb_url из subscriptions
   - upsert в sub_pairs
   - зовёт /internal/invalidate чтобы сбросить кеш агрегатора
3. Возвращает публичный URL агрегатора для копирования в клиент.

Пока SUB_AGGREGATOR_ADMIN_ONLY=true — эта команда единственный способ
получить aggregator ссылку. Никакие пользовательские экраны (профиль,
покупка и т.п.) агрегатор не отдают.

После валидации админом — флип SUB_AGGREGATOR_ADMIN_ONLY=false и
дописать вызов sub_aggregator.ensure_pair() в места отдачи ссылки
(profile screen, purchase success, /white).
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
from app.services import sub_aggregator
from app.utils.security import admin_only

logger = logging.getLogger(__name__)

sub_aggregator_admin_router = Router()


@sub_aggregator_admin_router.message(Command("aggregator"))
@admin_only
async def cmd_aggregator(message: Message) -> None:
    """Показать/пересоздать aggregator URL для админа. Debug-команда бета-фазы."""
    tg_id = message.from_user.id
    logger.info(
        "SUB_AGGREGATOR_CMD_ENTERED tg=%s enabled=%s url=%s admin_only=%s",
        tg_id, config.SUB_AGGREGATOR_ENABLED, config.SUB_AGGREGATOR_URL,
        config.SUB_AGGREGATOR_ADMIN_ONLY,
    )

    try:
        await _run_aggregator_cmd(message, tg_id)
    except Exception as e:
        logger.exception("SUB_AGGREGATOR_CMD_ERROR tg=%s: %s", tg_id, e)
        await message.answer(
            f"❌ <b>Внутренняя ошибка</b>\n\n<code>{type(e).__name__}: {str(e)[:400]}</code>\n\n"
            "Смотри логи Railway — там traceback.",
            parse_mode="HTML",
        )


async def _run_aggregator_cmd(message: Message, tg_id: int) -> None:
    if not config.SUB_AGGREGATOR_ENABLED:
        await message.answer(
            "❌ <b>SUB_AGGREGATOR_ENABLED=false</b>\n\n"
            "Сервис-агрегатор отключён глобально. Установи в ENV:\n"
            "<code>SUB_AGGREGATOR_ENABLED=true</code>\n"
            "<code>SUB_AGGREGATOR_URL=https://sub.YOUR-DOMAIN</code>\n"
            "<code>SUB_AGGREGATOR_INTERNAL_SECRET=&lt;same as service INTERNAL_SECRET&gt;</code>\n"
            "и перезапусти бота.",
            parse_mode="HTML",
        )
        return
    if not config.SUB_AGGREGATOR_URL:
        await message.answer(
            "❌ <b>SUB_AGGREGATOR_URL пуст</b>\n\n"
            "Установи <code>SUB_AGGREGATOR_URL=https://sub.YOUR-DOMAIN</code> и перезапусти.",
            parse_mode="HTML",
        )
        return

    url = await sub_aggregator.ensure_pair(tg_id)
    if not url:
        await message.answer(
            "⚠️ <b>Не удалось создать aggregator-пару</b>\n\n"
            "Проверь что у тебя есть <b>обе</b> ссылки Remnawave в БД "
            "(premium + bypass):\n"
            "<code>SELECT remnawave_premium_sub_url, remnawave_bypass_sub_url\n"
            "FROM subscriptions WHERE telegram_id = &lt;твой tg&gt;;</code>\n\n"
            "Если одной из них нет — сначала соверши покупку/активируй "
            "trial чтобы создались обе entity в панели.",
            parse_mode="HTML",
        )
        return

    # ⚠️ Telegram запрещает custom-protocol (happ://, v2raytun://, clash://)
    # в url-кнопках inline-клавиатуры — Bad Request Unsupported URL protocol.
    # Кладём deep-link'и в текст: Telegram сам подсвечивает их clickable.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать / открыть в браузере", url=url)],
        [InlineKeyboardButton(
            text="↻ Перевыпустить + сбросить кеш",
            callback_data="agg_admin_refresh",
        )],
    ])

    scope = "ADMIN-ONLY" if config.SUB_AGGREGATOR_ADMIN_ONLY else "ALL USERS"
    text = (
        "🔗 <b>Sub-Aggregator URL</b>\n\n"
        f"<code>{url}</code>\n\n"
        "<b>Deep-links</b> (тапни, чтобы открыть в клиенте):\n"
        f"• Happ: <code>happ://add/{url}</code>\n"
        f"• v2rayTun: <code>v2raytun://import/{url}</code>\n"
        f"• Streisand: <code>streisand://import/{url}</code>\n\n"
        f"Scope: <b>{scope}</b>\n"
        "Кэш агрегатора сброшен — следующий запрос перечитает обе апстрим ссылки.\n\n"
        "<b>Тест-план:</b>\n"
        "1. Открой ссылку в Happ / v2rayTun / Incy\n"
        "2. Клиент должен показать конфиги обоих типов (main + bypass)\n"
        "3. Убедись что <code>subscription-userinfo</code> корректный: "
        "трафик берётся из bypass, срок — из premium\n"
        "4. Отчитайся — тогда флипнем <code>SUB_AGGREGATOR_ADMIN_ONLY=false</code> "
        "и всё сообщество получит ссылку"
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@sub_aggregator_admin_router.callback_query(lambda c: c.data == "agg_admin_refresh")
@admin_only
async def cb_aggregator_refresh(callback) -> None:
    tg_id = callback.from_user.id
    url = await sub_aggregator.ensure_pair(tg_id)
    if not url:
        await callback.answer("Нет обеих ссылок в БД — не могу пересоздать", show_alert=True)
        return
    await callback.answer("Пара обновлена, кеш сброшен ✓", show_alert=True)
    logger.info("SUB_AGGREGATOR_ADMIN_REFRESH tg=%s url=%s", tg_id, url)


@sub_aggregator_admin_router.message(lambda m: m.text and m.text.strip().lower().startswith("/aggregator"))
@admin_only
async def cmd_aggregator_fallback(message: Message) -> None:
    """Fallback: если Command('aggregator') не сработал (FSM state / фильтр),
    ловим по text-startswith и логируем — понятно ли, что message доехал."""
    logger.warning(
        "SUB_AGGREGATOR_FALLBACK_HIT tg=%s text=%r — Command filter не сработал, "
        "видимо активный FSM state. Форсируем.",
        message.from_user.id, message.text,
    )
    # Очищаем state если есть — иначе Command filter продолжит игнориться.
    try:
        from aiogram.fsm.context import FSMContext  # type: ignore  # noqa: F401
    except Exception:
        pass
    await cmd_aggregator(message)


@sub_aggregator_admin_router.message(Command("aggstats"))
@admin_only
async def cmd_aggstats(message: Message) -> None:
    """Метрики агрегатора прямо в боте: hit-ratio, latency, кеши, атаки."""
    try:
        from app.api.sub_aggregator_route import get_metrics_snapshot
        s = get_metrics_snapshot()
    except Exception as e:
        await message.answer(f"❌ Не удалось получить метрики: {e}")
        return

    total = s["hits"] + s["misses"] + s["stale"]
    ratio_pct = round(s["hit_ratio"] * 100, 1)
    # Здоровье считаем только на осмысленной выборке — иначе ложная тревога
    # на первых запросах (холодный старт: TLS-хендшейк даёт высокий latency,
    # кеш ещё не прогрет → низкий hit-ratio). Реальные красные флаги:
    # фейлы upstream, stale-выдача (панель падала). hit/latency учитываем
    # только когда запросов/upstream-вызовов достаточно.
    enough_req = total >= 50
    enough_up = s["upstream_count"] >= 20
    warmup = not (enough_req or enough_up)
    healthy = (
        s["upstream_fail"] == 0
        and s["stale"] == 0
        and (not enough_req or s["hit_ratio"] > 0.85)
        and (not enough_up or s["avg_upstream_ms"] < 500)
    )
    if warmup:
        head = "🟢 Прогрев (мало запросов)"
    elif healthy:
        head = "🟢 Здоров"
    else:
        head = "🟡 Внимание"

    text = (
        f"📊 <b>Sub-Aggregator — метрики</b>  {head}\n\n"
        f"<b>Запросы</b> (всего {total}):\n"
        f"• Из кеша (hit): <b>{s['hits']}</b> — {ratio_pct}%\n"
        f"• В панель (miss): <b>{s['misses']}</b>\n"
        f"• Stale (панель упала): <b>{s['stale']}</b>\n"
        f"• Не найдено (404): <b>{s['not_found']}</b>\n"
        f"• Revoked-заглушки: <b>{s['revoked']}</b>\n\n"
        f"<b>Панель Remnawave</b>:\n"
        f"• Успешных upstream: <b>{s['upstream_ok']}</b>\n"
        f"• Фейлов upstream: <b>{s['upstream_fail']}</b>\n"
        f"• Средняя задержка: <b>{s['avg_upstream_ms']} мс</b>\n"
        f"• Singleflight-схлопов: <b>{s['singleflight_wait']}</b>\n\n"
        f"<b>Кеши</b>:\n"
        f"• Body: <b>{s['cache_size']}</b> · Pair: <b>{s['pair_cache_size']}</b> "
        f"· In-flight: <b>{s['inflight_size']}</b>\n\n"
        f"<b>Безопасность</b>:\n"
        f"• Алертов об атаках: <b>{s['attack_alerts_sent']}</b>\n\n"
        f"<i>Норма: hit >90%, задержка &lt;400мс, upstream_fail не растёт.</i>"
    )
    await message.answer(text, parse_mode="HTML")


@sub_aggregator_admin_router.message(Command("aggcheck"))
async def cmd_aggcheck(message: Message) -> None:
    """Диагностика «нет серверов»: /aggcheck [tg_id]. Доступна ВСЕМ.

    Обычный юзер проверяет только СЕБЯ (tg_id-аргумент игнорируется —
    приватность). Админ может проверить любого по /aggcheck <tg_id>.
    Показывает: есть ли пара → живы ли обе апстрим-ссылки → сколько
    серверов вернула каждая → итоговая склейка → публичный URL по UA.
    """
    from app.utils.security import is_admin
    requester = message.from_user.id
    parts = (message.text or "").split()
    arg = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else None
    # tg_id-аргумент только админу; остальные — только себя.
    tg = arg if (arg is not None and is_admin(requester)) else requester

    import database
    from app.api import sub_aggregator_route as agg

    # 1) sub_pairs строка
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        pair = await conn.fetchrow(
            "SELECT token, main_sub_url, gb_sub_url, status FROM sub_pairs WHERE telegram_id = $1", tg
        )
        subs = await conn.fetchrow(
            "SELECT remnawave_premium_sub_url AS main, remnawave_bypass_sub_url AS gb "
            "FROM subscriptions WHERE telegram_id = $1", tg
        )

    lines = [f"🔎 <b>Aggregator-check</b> tg=<code>{tg}</code>\n"]

    if not pair:
        lines.append("❌ <b>Пары в sub_pairs НЕТ.</b>")
        has_main = bool(subs and subs["main"])
        has_gb = bool(subs and subs["gb"])
        lines.append(f"subscriptions: premium_url={'✅' if has_main else '—'} bypass_url={'✅' if has_gb else '—'}")
        if not (has_main and has_gb):
            lines.append("\n→ Нет ОБЕИХ ссылок → ensure_pair вернёт None → юзер идёт по legacy (2 ключа), не агрегатор. Это ОК для bypass-only/trial.")
        else:
            lines.append("\n→ Обе ссылки есть, но пара не создана. Юзер ещё не открывал экран подключения ИЛИ ensure_pair упал. Пусть зайдёт в «Подключить».")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    lines.append(f"✅ Пара есть. status=<b>{pair['status']}</b> token=<code>{pair['token'][:10]}…</code>")
    if pair["status"] == "revoked":
        lines.append("⚠️ status=revoked → отдаётся заглушка, серверов не будет. Проверь, почему revoked.")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    # 2) Живые апстримы
    ua = agg._upstream_ua()  # тот же фикс-UA, что агрегатор в реале (base64)
    main_resp = await agg._fetch_upstream(pair["main_sub_url"], ua)
    gb_resp = await agg._fetch_upstream(pair["gb_sub_url"], ua)

    def _preview(resp):
        """Первые символы тела — сразу видно JSON ('{') vs base64/vless."""
        try:
            raw = (resp.text or "").strip()
        except Exception:
            return "?"
        head = raw[:36].replace("\n", "⏎")
        kind = "JSON" if raw[:1] in "{[" else ("base64/text" if raw else "пусто")
        from html import escape
        return f"{kind}: <code>{escape(head)}…</code>"

    def _desc(resp, url):
        if resp is None:
            return f"❌ НЕ ОТВЕТИЛ (таймаут/сеть) · {url[:45]}…"
        n = len(agg._decode_body(resp)) if resp.status_code == 200 else 0
        ct = (resp.headers.get("content-type") or "?")[:24]
        return (f"{'✅' if resp.status_code == 200 else '⚠️'} HTTP {resp.status_code} · "
                f"<b>{n}</b> серверов · ct=<code>{ct}</code>\n"
                f"тело: {_preview(resp)}\n{url[:48]}…")

    main_n = len(agg._decode_body(main_resp)) if (main_resp and main_resp.status_code == 200) else 0
    gb_n = len(agg._decode_body(gb_resp)) if (gb_resp and gb_resp.status_code == 200) else 0
    lines.append(f"\n<b>main (premium):</b>\n{_desc(main_resp, agg._normalize_upstream_url(pair['main_sub_url']))}")
    lines.append(f"\n<b>gb (bypass):</b>\n{_desc(gb_resp, agg._normalize_upstream_url(pair['gb_sub_url']))}")
    lines.append(f"\n<b>Итого серверов в склейке:</b> {main_n + gb_n}")

    if main_n + gb_n == 0:
        lines.append("\n❌ <b>0 серверов из панели</b> — апстрим не отдал (плохой URL / панель не вернула конфиги). Смотри выше.")

    # 3) End-to-end: публичный URL с iOS- и Android-UA (панель может
    #    отдавать РАЗНЫЙ content-type на разные UA — вот источник
    #    «неизвестный тип контента» на Android).
    public_url = f"{config.SUB_AGGREGATOR_URL}/a/{pair['token']}"
    _UAS = {
        "iOS": "Happ/2.0 (iPhone; iOS 17)",
        "Android": "Happ/2.0 (Android 14)",
        "v2rayTun-Android": "v2rayTun/1.0 (Android)",
    }
    lines.append(f"\n<b>Публичный URL</b> ({config.SUB_AGGREGATOR_URL}):")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as c:
            for label, uas in _UAS.items():
                # сброс кеша для чистого замера каждого UA
                agg.clear_cache(pair["token"])
                r = await c.get(public_url, headers={"User-Agent": uas})
                ct = (r.headers.get("content-type") or "?")[:28]
                srv = len(agg._decode_body(type("R", (), {"text": r.text})())) if r.status_code == 200 else 0
                ok = r.status_code == 200 and srv > 0
                lines.append(f"• {label}: {'✅' if ok else '❌'} HTTP {r.status_code} · <b>{srv}</b> серв · ct=<code>{ct}</code>")
        lines.append("→ Если content-type у iOS и Android разный — панель отдаёт разное на UA; агрегатор пробрасывает как есть. Сообщи оба ct.")
    except Exception as e:
        lines.append(f"❌ не достучались — {str(e)[:80]}\n→ Домен недоступен из бота (DNS/nginx/сеть).")

    await message.answer("\n".join(lines), parse_mode="HTML")


__all__ = ["sub_aggregator_admin_router"]
