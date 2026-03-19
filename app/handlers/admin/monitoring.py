"""
Extended admin monitoring dashboard.

Provides real-time visibility into:
- System health and component status
- Request throughput and latency
- Worker health and iteration stats
- DB pool utilization
- Memory usage
- Error log
- Rate limiting stats
- Payment metrics
"""
import logging
import time
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.handlers.common.utils import safe_edit_text
from app.utils.security import admin_only
from app.services.language_service import resolve_user_language
from app.core.runtime_context import get_bot_start_time

monitoring_router = Router()
logger = logging.getLogger(__name__)


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}с"
    if seconds < 3600:
        return f"{seconds // 60}м {seconds % 60}с"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}ч {m}м"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}д {h}ч"


def _severity_emoji(status: str) -> str:
    return {
        "healthy": "✅",
        "starting": "🔄",
        "running": "🟢",
        "stale": "⚠️",
        "failing": "🔴",
        "dead": "💀",
        "unknown": "❓",
    }.get(status, "❓")


def _get_monitoring_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    """Main monitoring navigation keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Обзор", callback_data="mon:overview"),
            InlineKeyboardButton(text="🔄 Воркеры", callback_data="mon:workers"),
        ],
        [
            InlineKeyboardButton(text="📈 Нагрузка", callback_data="mon:load"),
            InlineKeyboardButton(text="🗄 БД", callback_data="mon:database"),
        ],
        [
            InlineKeyboardButton(text="🛡 Безопасность", callback_data="mon:security"),
            InlineKeyboardButton(text="💳 Платежи", callback_data="mon:payments"),
        ],
        [
            InlineKeyboardButton(text="🐛 Ошибки", callback_data="mon:errors"),
            InlineKeyboardButton(text="💾 Ресурсы", callback_data="mon:resources"),
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:overview")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main")],
    ])


@monitoring_router.callback_query(F.data == "admin:monitoring")
@admin_only
async def callback_monitoring_main(callback: CallbackQuery):
    """Entry point to extended monitoring dashboard."""
    text = "📡 Центр мониторинга\n\nВыберите раздел:"
    await safe_edit_text(
        callback.message, text,
        reply_markup=_get_monitoring_keyboard(),
    )
    await callback.answer()


@monitoring_router.callback_query(F.data == "mon:overview")
@admin_only
async def callback_monitoring_overview(callback: CallbackQuery):
    """System overview — the single most important screen."""
    try:
        from app.core.metrics import get_metrics
        from app.core.system_state import recalculate_from_runtime, SystemSeverity

        m = get_metrics()
        snap = m.snapshot()
        system_state = recalculate_from_runtime()
        severity = system_state.get_severity()

        severity_map = {
            SystemSeverity.GREEN: "🟢 СИСТЕМА OK",
            SystemSeverity.YELLOW: "🟡 СИСТЕМА DEGRADED",
            SystemSeverity.RED: "🔴 СИСТЕМА CRITICAL",
        }

        def _icon(comp):
            return {"healthy": "✅", "degraded": "⚠️", "unavailable": "❌"}.get(comp.status.value, "❓")

        # Uptime
        start_time = get_bot_start_time()
        uptime_s = int((datetime.now(timezone.utc) - start_time).total_seconds()) if start_time else 0

        text = f"📡 МОНИТОРИНГ — ОБЗОР\n\n"
        text += f"<b>{severity_map[severity]}</b>\n"
        text += f"⏱ Аптайм: {_format_duration(uptime_s)}\n\n"

        # Components
        text += "━━━ Компоненты ━━━\n"
        text += f"{_icon(system_state.database)} БД  "
        text += f"{_icon(system_state.vpn_api)} VPN  "
        text += f"{_icon(system_state.payments)} Платежи\n\n"

        # Key metrics
        text += "━━━ Нагрузка (с момента старта) ━━━\n"
        text += f"📨 Запросов: {snap['requests']['total']:,}\n"
        text += f"⚡ Скорость: {snap['requests']['rate_per_sec']:.1f} req/s\n"
        text += f"⏱ Латенси: p50={snap['requests']['latency']['p50']*1000:.0f}ms "
        text += f"p95={snap['requests']['latency']['p95']*1000:.0f}ms "
        text += f"p99={snap['requests']['latency']['p99']*1000:.0f}ms\n"
        text += f"🔄 Concurrent: {snap['concurrency']['current']} (пик: {snap['concurrency']['peak']})\n\n"

        # Errors summary
        text += "━━━ Ошибки ━━━\n"
        errors_total = snap["requests"]["errors"]
        total = snap["requests"]["total"]
        error_pct = (errors_total / total * 100) if total > 0 else 0
        text += f"❌ Ошибок: {errors_total} ({error_pct:.1f}%)\n"
        text += f"🚫 Rate limited: {snap['rate_limiting']['hits']}\n"
        text += f"🔒 Flood bans: {snap['rate_limiting']['flood_bans']}\n\n"

        # Workers summary
        workers = snap.get("workers", {})
        healthy_count = sum(1 for w in workers.values() if w.get("status") == "healthy")
        total_workers = len(workers)
        text += f"━━━ Воркеры ━━━\n"
        text += f"🔧 {healthy_count}/{total_workers} healthy\n"
        for wname, winfo in workers.items():
            text += f"  {_severity_emoji(winfo['status'])} {wname}\n"

        # Memory
        text += f"\n💾 RAM: {snap['process']['memory_rss_mb']:.0f} MB | PID: {snap['process']['pid']}\n"

        await safe_edit_text(
            callback.message, text,
            reply_markup=_get_monitoring_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring overview error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:workers")
@admin_only
async def callback_monitoring_workers(callback: CallbackQuery):
    """Detailed worker status."""
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        workers = m.get_worker_status()

        text = "🔄 МОНИТОРИНГ — ВОРКЕРЫ\n\n"

        if not workers:
            text += "Нет зарегистрированных воркеров.\n"
        else:
            for name, info in workers.items():
                emoji = _severity_emoji(info["status"])
                text += f"{emoji} <b>{name}</b>\n"
                text += f"  Статус: {info['status'].upper()}\n"
                text += f"  Итераций: {info['iterations']:,}\n"
                text += f"  Ошибок: {info['errors']} ({info['error_rate']})\n"

                if info.get("since_last_ok_s") is not None:
                    text += f"  Последний OK: {_format_duration(info['since_last_ok_s'])} назад\n"

                if info.get("latency"):
                    lat = info["latency"]
                    text += f"  Латенси: avg={lat['avg']:.1f}s p95={lat['p95']:.1f}s\n"

                if info.get("last_error"):
                    err = info["last_error"][:80]
                    text += f"  Последняя ошибка: <code>{err}</code>\n"

                text += "\n"

        # Try to get supervisor info
        try:
            # Import the global supervisor from main if available
            from app.core.worker_monitor import WorkerSupervisor
            # Supervisor info is embedded in metrics already
        except Exception:
            pass

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:workers")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring workers error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:load")
@admin_only
async def callback_monitoring_load(callback: CallbackQuery):
    """Request load and throughput details."""
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()

        text = "📈 МОНИТОРИНГ — НАГРУЗКА\n\n"

        req = snap["requests"]
        text += "━━━ Запросы ━━━\n"
        text += f"📨 Всего: {req['total']:,}\n"
        text += f"✅ Успешных: {req['success']:,}\n"
        text += f"❌ Ошибок: {req['errors']:,}\n"
        text += f"⏱ Таймаутов: {req['timeouts']:,}\n"
        text += f"🚫 Rate limited: {req['rate_limited']:,}\n\n"

        text += "━━━ Скорость ━━━\n"
        text += f"⚡ Текущая: {req['rate_per_sec']:.2f} req/s\n"
        text += f"❌ Ошибки: {req['error_rate_per_sec']:.2f} err/s\n\n"

        text += "━━━ Латенси (обработка update) ━━━\n"
        lat = req["latency"]
        text += f"  Среднее: {lat['avg']*1000:.0f}ms\n"
        text += f"  p50: {lat['p50']*1000:.0f}ms\n"
        text += f"  p95: {lat['p95']*1000:.0f}ms\n"
        text += f"  p99: {lat['p99']*1000:.0f}ms\n"
        text += f"  Кол-во: {lat['count']:,}\n\n"

        text += "━━━ Concurrency ━━━\n"
        conc = snap["concurrency"]
        text += f"🔄 Текущий: {conc['current']}\n"
        text += f"📈 Пиковый: {conc['peak']}\n\n"

        wh = snap["webhooks"]
        text += "━━━ Webhooks ━━━\n"
        text += f"📨 Всего: {wh['total']:,}\n"
        text += f"❌ Ошибок: {wh['errors']:,}\n"
        if wh["latency"]["count"] > 0:
            text += f"⏱ Латенси: avg={wh['latency']['avg']*1000:.0f}ms p95={wh['latency']['p95']*1000:.0f}ms\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:load")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring load error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:database")
@admin_only
async def callback_monitoring_database(callback: CallbackQuery):
    """Database pool and query stats."""
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()

        text = "🗄 МОНИТОРИНГ — БАЗА ДАННЫХ\n\n"

        text += f"📊 Статус: {'✅ READY' if database.DB_READY else '❌ NOT READY'}\n\n"

        # Pool stats
        pool = await database.get_pool() if database.DB_READY else None
        if pool:
            try:
                size = pool.get_size()
                idle = pool.get_idle_size()
                used = size - idle
                min_s = pool.get_min_size()
                max_s = pool.get_max_size()
                utilization = (used / max_s * 100) if max_s > 0 else 0

                text += "━━━ Пул соединений ━━━\n"
                text += f"  Всего: {size}\n"
                text += f"  Активных: {used}\n"
                text += f"  Свободных: {idle}\n"
                text += f"  Min/Max: {min_s}/{max_s}\n"
                text += f"  Загрузка: {utilization:.0f}%\n"

                # Visual bar
                bar_len = 20
                filled = int(utilization / 100 * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                text += f"  [{bar}] {utilization:.0f}%\n\n"
            except Exception:
                text += "⚠️ Не удалось получить статус пула\n\n"

        # Query metrics
        db = snap["database"]
        text += "━━━ Запросы к БД ━━━\n"
        text += f"📊 Всего: {db['queries']:,}\n"
        text += f"❌ Ошибок: {db['errors']:,}\n"
        text += f"⏱ Pool timeouts: {db['pool_timeouts']:,}\n"

        if db["query_latency"]["count"] > 0:
            ql = db["query_latency"]
            text += f"\n━━━ Латенси запросов ━━━\n"
            text += f"  Среднее: {ql['avg']*1000:.1f}ms\n"
            text += f"  p50: {ql['p50']*1000:.1f}ms\n"
            text += f"  p95: {ql['p95']*1000:.1f}ms\n"
            text += f"  p99: {ql['p99']*1000:.1f}ms\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:database")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring database error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:security")
@admin_only
async def callback_monitoring_security(callback: CallbackQuery):
    """Security metrics: rate limits, bans, alerts."""
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()

        text = "🛡 МОНИТОРИНГ — БЕЗОПАСНОСТЬ\n\n"

        rl = snap["rate_limiting"]
        text += "━━━ Rate Limiting ━━━\n"
        text += f"🚫 Заблокировано запросов: {rl['hits']:,}\n"
        text += f"🔒 Flood-бан выдано: {rl['flood_bans']:,}\n\n"

        al = snap["alerts"]
        text += "━━━ Алерты админу ━━━\n"
        text += f"📤 Отправлено: {al['sent']:,}\n"
        text += f"❌ Не доставлено: {al['failed']:,}\n\n"

        # Security-relevant error samples
        text += "━━━ Последние ошибки безопасности ━━━\n"
        errors = m.errors.recent(5)
        security_errors = [e for e in errors if "security" in e.component.lower() or "forbidden" in e.error_type.lower() or "auth" in e.error_type.lower()]
        if security_errors:
            for err in security_errors[-5:]:
                ts = datetime.fromtimestamp(err.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
                text += f"  {ts} [{err.error_type}] {err.message[:60]}\n"
        else:
            text += "  Нет инцидентов безопасности\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:security")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring security error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:payments")
@admin_only
async def callback_monitoring_payments(callback: CallbackQuery):
    """Payment metrics."""
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()

        text = "💳 МОНИТОРИНГ — ПЛАТЕЖИ\n\n"

        pay = snap["payments"]
        text += "━━━ С момента старта ━━━\n"
        text += f"📋 Инициировано: {pay['initiated']:,}\n"
        text += f"✅ Успешных: {pay['success']:,}\n"
        text += f"❌ Ошибок: {pay['failed']:,}\n"
        success_rate = (pay['success'] / pay['initiated'] * 100) if pay['initiated'] > 0 else 0
        text += f"📊 Успешность: {success_rate:.1f}%\n"
        text += f"💰 Доход: {pay['revenue_rub']:,.2f} ₽\n\n"

        # Try to get daily DB stats
        if database.DB_READY:
            try:
                daily = await database.get_daily_summary(None)
                text += "━━━ Сегодня (БД) ━━━\n"
                text += f"💰 Доход: {daily.get('revenue', 0):.2f} ₽\n"
                text += f"💳 Платежей: {daily.get('payments_count', 0)}\n"
                text += f"🆕 Новых польз.: {daily.get('new_users', 0)}\n"
                text += f"🔑 Новых подписок: {daily.get('new_subscriptions', 0)}\n"
            except Exception as e:
                text += f"⚠️ Не удалось загрузить данные из БД: {str(e)[:60]}\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:payments")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring payments error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:errors")
@admin_only
async def callback_monitoring_errors(callback: CallbackQuery):
    """Recent error log."""
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()

        text = "🐛 МОНИТОРИНГ — ОШИБКИ\n\n"
        text += f"Всего ошибок: {snap['errors']['total']:,}\n\n"

        recent = snap["errors"]["recent"]
        if recent:
            text += "━━━ Последние 10 ━━━\n"
            for err in reversed(recent):
                ts = datetime.fromtimestamp(err["time"], tz=timezone.utc).strftime("%H:%M:%S")
                text += f"\n<b>{ts}</b> [{err['component']}]\n"
                text += f"  {err['type']}: {err['msg'][:80]}\n"
        else:
            text += "✅ Ошибок нет. Всё работает.\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:errors")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring errors error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@monitoring_router.callback_query(F.data == "mon:resources")
@admin_only
async def callback_monitoring_resources(callback: CallbackQuery):
    """System resources: memory, CPU, file descriptors."""
    try:
        from app.core.metrics import get_metrics
        import resource as res_mod

        m = get_metrics()
        snap = m.snapshot()
        proc = snap["process"]

        text = "💾 МОНИТОРИНГ — РЕСУРСЫ\n\n"

        text += "━━━ Процесс ━━━\n"
        text += f"  PID: {proc['pid']}\n"
        text += f"  Аптайм: {_format_duration(proc['uptime_seconds'])}\n"
        text += f"  RAM (RSS): {proc['memory_rss_mb']:.0f} MB\n\n"

        # Extended memory info from /proc
        try:
            with open("/proc/self/status") as f:
                status_lines = f.readlines()
            mem_info = {}
            for line in status_lines:
                for key in ("VmPeak", "VmRSS", "VmSize", "VmSwap", "Threads"):
                    if line.startswith(f"{key}:"):
                        mem_info[key] = line.split(":", 1)[1].strip()
            if mem_info:
                text += "━━━ /proc/self/status ━━━\n"
                for k, v in mem_info.items():
                    text += f"  {k}: {v}\n"
                text += "\n"
        except Exception:
            pass

        # File descriptors
        try:
            fd_count = len([f for f in __import__("os").listdir("/proc/self/fd")])
            text += f"━━━ Файловые дескрипторы ━━━\n"
            text += f"  Открыто: {fd_count}\n"
            try:
                import subprocess
                result = subprocess.run(
                    ["cat", "/proc/self/limits"],
                    capture_output=True, text=True, timeout=2,
                )
                for line in result.stdout.split("\n"):
                    if "open files" in line.lower():
                        text += f"  Лимит: {line.split()[-3] if len(line.split()) >= 3 else 'N/A'}\n"
            except Exception:
                pass
            text += "\n"
        except Exception:
            pass

        # Resource usage
        try:
            usage = res_mod.getrusage(res_mod.RUSAGE_SELF)
            text += "━━━ Resource Usage ━━━\n"
            text += f"  User CPU: {usage.ru_utime:.1f}s\n"
            text += f"  System CPU: {usage.ru_stime:.1f}s\n"
            text += f"  Max RSS: {usage.ru_maxrss / 1024:.0f} MB\n"
            text += f"  Context switches (vol): {usage.ru_nvcsw:,}\n"
            text += f"  Context switches (invol): {usage.ru_nivcsw:,}\n"
        except Exception:
            pass

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="mon:resources")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mon:overview")],
        ])

        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.exception("monitoring resources error: %s", e)
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)
