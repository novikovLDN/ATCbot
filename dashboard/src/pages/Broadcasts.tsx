import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  Megaphone,
  RefreshCcw,
  ChevronRight,
  CheckCircle2,
  AlertCircle,
  Clock,
  Calendar as CalendarIcon,
  Copy,
  LayoutList,
  Rows3,
  Plus,
  Repeat,
  Trash2,
  Users as UsersIcon,
  ArrowLeft,
  Send,
  X,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { useEventStream, type BusEvent } from "@/lib/ws";
import { toast } from "@/store/toast";
import { fmtDate, fmtNum, fmtRub, truncate } from "@/lib/format";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";

interface BroadcastRow extends Record<string, unknown> {
  id?: number;
  title?: string;
  message?: string;
  broadcast_type?: string;
  segment?: string;
  is_ab_test?: boolean;
  created_at?: string;
  sent_at?: string;
  total_recipients?: number;
  sent_count?: number;
  failed_count?: number;
  status?: string;
}

interface SendProgress {
  processed: number;
  total: number;
  sent: number;
  failed: number;
  status: "running" | "done" | "failed";
  error?: string;
  ts: number;
}

export function Broadcasts() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["broadcasts", "recent"],
    queryFn: () => endpoints.broadcastsRecent(500) as Promise<BroadcastRow[]>,
    refetchInterval: 15_000,
  });

  const [selected, setSelected] = useState<number | null>(null);
  // Map broadcast_id → live progress so the list row and the detail
  // panel can render the same up-to-date status. Cleared 8s after `done`.
  const [sending, setSending] = useState<Record<number, SendProgress>>({});
  const detailRef = useRef<HTMLDivElement | null>(null);

  // View mode: compact = только title (как раньше), expanded =
  // title + полный текст + сегмент + получатели + кнопка «Отправить снова».
  // Persist в localStorage — админ переключил один раз, остаётся так.
  const [viewMode, setViewMode] = useState<"compact" | "expanded">(() => {
    try {
      const v = localStorage.getItem("broadcasts:viewMode");
      return v === "expanded" ? "expanded" : "compact";
    } catch {
      return "compact";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("broadcasts:viewMode", viewMode);
    } catch {
      /* localStorage disabled */
    }
  }, [viewMode]);

  useEventStream((e: BusEvent) => {
    const bid = Number(e.broadcast_id ?? 0);
    if (!bid) return;
    if (e.type === "broadcast:created") {
      // A new broadcast just kicked off — pull the row into the list
      // immediately rather than waiting for the 30s poll.
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
      setSending((prev) => ({
        ...prev,
        [bid]: {
          processed: 0,
          total: Number(e.audience ?? 0),
          sent: 0,
          failed: 0,
          status: "running",
          ts: Date.now(),
        },
      }));
    } else if (e.type === "broadcast:progress") {
      setSending((prev) => ({
        ...prev,
        [bid]: {
          processed: Number(e.processed ?? 0),
          total: Number(e.total ?? 0),
          sent: Number(e.sent ?? 0),
          failed: Number(e.failed ?? 0),
          status: "running",
          ts: Date.now(),
        },
      }));
    } else if (e.type === "broadcast:done") {
      setSending((prev) => ({
        ...prev,
        [bid]: {
          processed: Number(e.total ?? 0),
          total: Number(e.total ?? 0),
          sent: Number(e.sent ?? 0),
          failed: Number(e.failed ?? 0),
          status: "done",
          ts: Date.now(),
        },
      }));
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
      // Auto-clear so the row goes back to the default look.
      window.setTimeout(() => {
        setSending((prev) => {
          if (prev[bid]?.status !== "done") return prev;
          const { [bid]: _, ...rest } = prev;
          return rest;
        });
      }, 8000);
    } else if (e.type === "broadcast:failed") {
      setSending((prev) => ({
        ...prev,
        [bid]: {
          processed: prev[bid]?.processed ?? 0,
          total: prev[bid]?.total ?? 0,
          sent: prev[bid]?.sent ?? 0,
          failed: prev[bid]?.failed ?? 0,
          status: "failed",
          error: String(e.error ?? ""),
          ts: Date.now(),
        },
      }));
    }
  });

  // Mobile: when a row is tapped, smoothly scroll the detail card into
  // view so the admin sees something happen. Desktop keeps the
  // side-by-side layout and skips the scroll.
  useEffect(() => {
    if (selected === null) return;
    if (typeof window === "undefined") return;
    if (window.innerWidth >= 1024) return;
    const t = window.setTimeout(() => {
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
    return () => window.clearTimeout(t);
  }, [selected]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Маркетинг
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Рассылки
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() =>
              setViewMode((m) => (m === "compact" ? "expanded" : "compact"))
            }
            className="btn-secondary"
            title={
              viewMode === "compact"
                ? "Переключить на расширенный вид (текст + сегмент + получатели)"
                : "Переключить на компактный вид (только заголовки)"
            }
          >
            {viewMode === "compact" ? (
              <Rows3 className="h-3.5 w-3.5" />
            ) : (
              <LayoutList className="h-3.5 w-3.5" />
            )}
            <span className="hidden sm:inline">
              {viewMode === "compact" ? "Расширенно" : "Компактно"}
            </span>
          </button>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="btn-secondary"
          >
            <RefreshCcw className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Обновить</span>
          </button>
          <Link to="/broadcasts/new" className="btn-primary">
            <Plus className="h-3.5 w-3.5" /> Создать
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_400px]">
        <div className="card p-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Последние 500
            </div>
            {list.isFetching && <Spinner />}
          </div>

          {list.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Spinner /> Загружаю...
            </div>
          ) : !list.data || list.data.length === 0 ? (
            <EmptyState
              icon={Megaphone}
              title="Пока пусто"
              description="Когда отправите первую рассылку, она появится здесь."
            />
          ) : (
            <ul className={viewMode === "expanded" ? "space-y-3" : "divide-y divide-border/60"}>
              {list.data.map((b) => {
                const id = Number(b.id ?? 0);
                const prog = sending[id];
                return (
                  <BroadcastListRow
                    key={id}
                    row={b}
                    id={id}
                    selected={selected === id}
                    onSelect={() => setSelected(id)}
                    progress={prog}
                    mode={viewMode}
                  />
                );
              })}
            </ul>
          )}
        </div>

        <div ref={detailRef} className="space-y-4">
          {selected !== null ? (
            <BroadcastDetail
              id={selected}
              progress={sending[selected]}
              onBack={() => setSelected(null)}
            />
          ) : (
            <div className="card hidden p-6 lg:block">
              <EmptyState
                icon={Megaphone}
                title="Выбери рассылку"
                description="Кликни по строке слева, чтобы посмотреть деталь и статистику отправки."
              />
            </div>
          )}
          <ScheduledBroadcastsSection />
        </div>
      </div>
    </div>
  );
}

function SendProgressBar({ prog }: { prog: SendProgress }) {
  const pct =
    prog.total > 0
      ? Math.min(100, Math.round((prog.processed / prog.total) * 100))
      : prog.status === "done"
      ? 100
      : 0;
  const bar =
    prog.status === "failed"
      ? "h-full bg-danger transition-all"
      : prog.status === "done"
      ? "h-full bg-success transition-all"
      : "h-full bg-accent transition-all";
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px] text-fg-muted">
        <span className="font-mono">
          {prog.processed}/{prog.total} · {pct}%
        </span>
        <span className="font-mono">
          ✓ {prog.sent}
          {prog.failed > 0 && (
            <span className="ml-1 text-danger">· ✗ {prog.failed}</span>
          )}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-elevated">
        <div className={bar} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/**
 * BroadcastListRow — рендер одной строки списка с поддержкой двух видов:
 *   compact  — как раньше: title + короткий превью + сегмент + прогресс
 *   expanded — карточка: title жирный + полный текст + сегмент/получатели
 *              в правой колонке + inline-кнопка «Отправить снова»
 */
function BroadcastListRow({
  row,
  id,
  selected,
  onSelect,
  progress,
  mode,
}: {
  row: BroadcastRow;
  id: number;
  selected: boolean;
  onSelect: () => void;
  progress?: SendProgress;
  mode: "compact" | "expanded";
}) {
  const navigate = useNavigate();
  const running = progress?.status === "running";
  const done = progress?.status === "done";
  const failed = progress?.status === "failed";

  if (mode === "expanded") {
    // Расширенная карточка. Клик по всей карточке (кроме кнопки) → select.
    return (
      <li>
        <div
          className={
            selected
              ? "rounded-xl border border-accent/40 bg-accent/[0.06] p-3 transition"
              : "rounded-xl border border-border bg-bg-card p-3 transition hover:border-fg-subtle"
          }
        >
          <div className="flex items-start gap-3">
            <button
              type="button"
              onClick={onSelect}
              className="flex min-w-0 flex-1 items-start gap-3 text-left"
            >
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                <Megaphone className="h-3.5 w-3.5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-fg">
                    {truncate(String(row.title ?? "Без названия"), 80)}
                  </span>
                  {row.is_ab_test && <span className="badge-muted">A/B</span>}
                  {running && (
                    <span className="badge-accent">
                      <Send className="h-3 w-3 animate-pulse" /> отправляется
                    </span>
                  )}
                  {done && (
                    <span className="badge-success">
                      <CheckCircle2 className="h-3 w-3" /> готово
                    </span>
                  )}
                  {failed && (
                    <span className="badge-danger">
                      <AlertCircle className="h-3 w-3" /> сбой
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] text-fg-subtle">
                  #{id}
                  {row.created_at && ` · ${fmtDate(String(row.created_at))}`}
                  {row.broadcast_type && ` · ${String(row.broadcast_type)}`}
                </div>
              </div>
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/broadcasts/new?clone=${id}`);
              }}
              className="btn-ghost shrink-0"
              title="Клонировать текст+фото+кнопки в новую рассылку"
            >
              <Copy className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Снова</span>
            </button>
          </div>

          {/* Полный текст сообщения */}
          {typeof row.message === "string" && row.message && (
            <div className="mt-2.5 rounded-lg border border-border/60 bg-bg-subtle/40 p-3 text-[13px] leading-relaxed text-fg">
              <div
                className="whitespace-pre-wrap"
                dangerouslySetInnerHTML={{
                  __html: expandedSanitize(String(row.message)),
                }}
              />
            </div>
          )}

          {/* Правая колонка: сегмент + получатели + прогресс */}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-fg-muted">
            {row.segment && (
              <span className="inline-flex items-center gap-1">
                <UsersIcon className="h-3 w-3" /> сегмент:{" "}
                <b className="text-fg">{String(row.segment)}</b>
              </span>
            )}
            {typeof row.total_recipients === "number" && (
              <span className="inline-flex items-center gap-1">
                · получателей: <b className="text-fg tabular-nums">{fmtNum(row.total_recipients)}</b>
              </span>
            )}
            {typeof row.sent_count === "number" && (
              <span className="inline-flex items-center gap-1 text-success">
                · ✓ {fmtNum(row.sent_count)}
              </span>
            )}
            {typeof row.failed_count === "number" && row.failed_count > 0 && (
              <span className="inline-flex items-center gap-1 text-danger">
                · ✕ {fmtNum(row.failed_count)}
              </span>
            )}
          </div>
          {progress && progress.total > 0 && (
            <div className="mt-2">
              <SendProgressBar prog={progress} />
            </div>
          )}
        </div>
      </li>
    );
  }

  // Compact — прежний вид.
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={
          selected
            ? "flex w-full items-start gap-3 rounded-lg bg-accent/10 px-2 py-3 text-left text-fg shadow-[inset_0_0_0_1px_rgba(14,165,233,0.25)] transition"
            : "flex w-full items-start gap-3 rounded-lg px-2 py-3 text-left transition hover:bg-accent/[0.04]"
        }
      >
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
          <Megaphone className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-fg">
              {truncate(String(row.title ?? "Без названия"), 60)}
            </span>
            {row.is_ab_test && <span className="badge-muted">A/B</span>}
            {row.broadcast_type && (
              <span className="badge-muted">{String(row.broadcast_type)}</span>
            )}
            {running && (
              <span className="badge-accent">
                <Send className="h-3 w-3 animate-pulse" /> отправляется
              </span>
            )}
            {done && (
              <span className="badge-success">
                <CheckCircle2 className="h-3 w-3" /> готово
              </span>
            )}
            {failed && (
              <span className="badge-danger">
                <AlertCircle className="h-3 w-3" /> сбой
              </span>
            )}
          </div>
          {typeof row.message === "string" && (
            <div className="mt-1 truncate text-xs text-fg-muted">
              {truncate(String(row.message), 100)}
            </div>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-fg-subtle">
            {row.created_at && <span>{fmtDate(String(row.created_at))}</span>}
            {row.segment && <span>· сегмент {String(row.segment)}</span>}
          </div>
          {progress && progress.total > 0 && (
            <div className="mt-2">
              <SendProgressBar prog={progress} />
            </div>
          )}
        </div>
        <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" />
      </button>
    </li>
  );
}

// Специальный sanitize для expanded-вида. Точно так же строгий как
// sanitize() ниже, но выделен отдельно — на случай если понадобится
// разрешить чуть больше HTML-тегов в превью.
function expandedSanitize(html: string): string {
  return html
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "")
    .replace(/on\w+="[^"]*"/gi, "")
    .replace(/javascript:/gi, "");
}

function BroadcastDetail({
  id,
  progress,
  onBack,
}: {
  id: number;
  progress?: SendProgress;
  onBack?: () => void;
}) {
  // ВСЕ hooks — до любых ранних return'ов (Rules of Hooks). Дважды
  // ловили этот баг: сначала useNavigate после return (ef60e4b), затем
  // useState(showSchedule) после return в новом планировщике. React
  // крашит компонент «Rendered more hooks than previous render», UI
  // показывает белый/чёрный экран без ErrorBoundary.
  const navigate = useNavigate();
  const [showSchedule, setShowSchedule] = useState(false);
  const det = useQuery({
    queryKey: ["broadcasts", "detail", id],
    queryFn: () => endpoints.broadcastDetail(id),
  });
  const stats = useQuery({
    queryKey: ["broadcasts", "stats", id],
    queryFn: () => endpoints.broadcastStats(id),
    // Poll faster while a live send is in progress — every second so
    // the admin sees delivered/failed climb in real time. Idle: 5s.
    refetchInterval: progress?.status === "running" ? 1_000 : 5_000,
  });
  // Расширенная аналитика: conversion в окнах 1д/3д/7д от sent_at
  // каждого получателя + revenue от этих оплат. Обновляется реже —
  // это тяжёлый JOIN broadcast_log × payments.
  const analytics = useQuery({
    queryKey: ["broadcasts", "analytics", id],
    queryFn: () => endpoints.broadcastAnalytics(id),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  if (det.isLoading) {
    return (
      <div className="card flex items-center gap-3 p-6 text-sm text-fg-muted">
        <Spinner /> Загружаю...
      </div>
    );
  }
  if (det.isError || !det.data) {
    return (
      <EmptyState
        icon={AlertCircle}
        title="Не удалось загрузить"
        description="Попробуй обновить страницу."
      />
    );
  }

  const b = det.data as BroadcastRow;
  const s = (stats.data ?? {}) as BroadcastRow;

  return (
    <div className="card p-5 animate-fade-in">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="btn-ghost lg:hidden"
              aria-label="Назад к списку"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> К списку
            </button>
          )}
          <div className="text-xs uppercase tracking-wider text-fg-subtle">
            Рассылка #{id}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => navigate(`/broadcasts/new?clone=${id}`)}
            className="btn-secondary"
            title="Открыть визард с текстом, фото и кнопками этой рассылки — только сегмент выбираешь заново"
          >
            <Copy className="h-3.5 w-3.5" /> Отправить снова
          </button>
          <button
            type="button"
            onClick={() => setShowSchedule(true)}
            className="btn-secondary"
            title="Запланировать эту рассылку на конкретную дату+время (МСК) или сделать повторяющейся"
          >
            <CalendarIcon className="h-3.5 w-3.5" /> Запланировать
          </button>
          <DeleteFromUsersControl broadcastId={id} />
        </div>
      </div>

      {showSchedule && (
        <ScheduleBroadcastModal
          broadcastId={id}
          onClose={() => setShowSchedule(false)}
        />
      )}
      <h3 className="text-lg font-semibold text-fg">
        {truncate(String(b.title ?? "Без названия"), 80)}
      </h3>

      {progress && (
        <div
          className={
            progress.status === "running"
              ? "mt-3 rounded-xl border border-accent/30 bg-accent/10 p-3"
              : progress.status === "done"
              ? "mt-3 rounded-xl border border-success/30 bg-success/10 p-3"
              : "mt-3 rounded-xl border border-danger/30 bg-danger/10 p-3"
          }
        >
          <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
            <span
              className={
                progress.status === "running"
                  ? "inline-flex items-center gap-1.5 font-semibold text-accent"
                  : progress.status === "done"
                  ? "inline-flex items-center gap-1.5 font-semibold text-success"
                  : "inline-flex items-center gap-1.5 font-semibold text-danger"
              }
            >
              {progress.status === "running" && (
                <>
                  <Send className="h-3.5 w-3.5 animate-pulse" /> Отправляю...
                </>
              )}
              {progress.status === "done" && (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5" /> Готово
                </>
              )}
              {progress.status === "failed" && (
                <>
                  <AlertCircle className="h-3.5 w-3.5" />
                  Сбой отправки
                </>
              )}
            </span>
            <span className="font-mono text-[11px] text-fg-muted">
              {progress.processed}/{progress.total}
            </span>
          </div>
          <SendProgressBar prog={progress} />
          {progress.status === "failed" && progress.error && (
            <div className="mt-2 break-all text-[11px] text-danger">
              {progress.error}
            </div>
          )}
        </div>
      )}

      <div className="mt-4 grid grid-cols-3 gap-2">
        <Tile
          icon={UsersIcon}
          label="Получатели"
          value={fmtNum(asNum(s.total_recipients ?? s.total ?? b.total_recipients))}
        />
        <Tile
          icon={CheckCircle2}
          label="Доставлено"
          value={fmtNum(asNum(s.sent_count ?? s.sent ?? b.sent_count))}
          tone="success"
        />
        <Tile
          icon={AlertCircle}
          label="Ошибок"
          value={fmtNum(asNum(s.failed_count ?? s.failed ?? b.failed_count))}
          tone="danger"
        />
      </div>

      <div className="mt-4 space-y-1.5 text-sm">
        <Row label="Тип" value={String(b.broadcast_type ?? "—")} />
        <Row label="Сегмент" value={String(b.segment ?? "—")} />
        <Row label="A/B" value={b.is_ab_test ? "да" : "нет"} />
        <Row label="Создана" value={fmtDate(String(b.created_at ?? ""))} />
        {b.sent_at && (
          <Row label="Отправлена" value={fmtDate(String(b.sent_at))} />
        )}
      </div>

      <BroadcastAnalyticsPanel
        loading={analytics.isLoading}
        error={analytics.isError}
        data={analytics.data}
      />

      {typeof b.message === "string" && b.message && (
        <div className="mt-4 rounded-xl border border-border bg-bg-subtle/60 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            <Clock className="h-3 w-3" /> Текст
          </div>
          <div
            className="whitespace-pre-wrap text-sm leading-relaxed text-fg"
            dangerouslySetInnerHTML={{ __html: sanitize(String(b.message)) }}
          />
        </div>
      )}
    </div>
  );
}

function BroadcastAnalyticsPanel({
  loading,
  error,
  data,
}: {
  loading: boolean;
  error: boolean;
  data?: {
    total_recipients: number;
    sent: number;
    failed: number;
    deleted: number;
    delivered: number;
    converted_1d: number;
    converted_3d: number;
    converted_7d: number;
    revenue_kop_1d: number;
    revenue_kop_3d: number;
    revenue_kop_7d: number;
    conversion_rate_7d: number;
    blocked_estimate: number;
  };
}) {
  if (loading) {
    return (
      <div className="mt-4 rounded-xl border border-border bg-bg-subtle/40 p-4">
        <div className="text-[11px] uppercase tracking-wider text-fg-subtle">
          Аналитика
        </div>
        <div className="mt-1.5 text-sm text-fg-muted">Считаю конверсию…</div>
      </div>
    );
  }
  if (error || !data) {
    return null;
  }
  const convRate = data.conversion_rate_7d;
  const blockedPct = data.blocked_estimate;
  return (
    <div className="mt-4 rounded-xl border border-border bg-gradient-to-br from-accent/5 to-white p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
          🎯 Конверсия и доход
        </div>
        <div className="text-[11px] text-fg-subtle">
          7д CR:{" "}
          <span
            className={
              convRate >= 0.05
                ? "font-semibold text-success"
                : convRate >= 0.01
                ? "font-semibold text-warning"
                : "font-semibold text-fg-muted"
            }
          >
            {(convRate * 100).toFixed(2)}%
          </span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <ConvTile
          window="24ч"
          count={data.converted_1d}
          revenueKop={data.revenue_kop_1d}
        />
        <ConvTile
          window="72ч"
          count={data.converted_3d}
          revenueKop={data.revenue_kop_3d}
        />
        <ConvTile
          window="7д"
          count={data.converted_7d}
          revenueKop={data.revenue_kop_7d}
          highlight
        />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-fg-muted sm:grid-cols-4">
        <MiniStat label="Доставлено" value={fmtNum(data.delivered)} />
        <MiniStat label="Ошибок" value={fmtNum(data.failed)} tone="danger" />
        <MiniStat
          label="Заблокировавших ≈"
          value={`${(blockedPct * 100).toFixed(1)}%`}
          tone={blockedPct > 0.1 ? "danger" : undefined}
        />
        <MiniStat label="Удалено" value={fmtNum(data.deleted)} />
      </div>
    </div>
  );
}

function ConvTile({
  window,
  count,
  revenueKop,
  highlight,
}: {
  window: string;
  count: number;
  revenueKop: number;
  highlight?: boolean;
}) {
  return (
    <div
      className={
        highlight
          ? "rounded-xl border border-accent/30 bg-accent/10 p-3"
          : "rounded-xl border border-border bg-bg-card p-3"
      }
    >
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
        Купили за {window}
      </div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums text-fg">
        {fmtNum(count)}
      </div>
      <div className="mt-0.5 text-[11px] tabular-nums text-fg-muted">
        {fmtRub(revenueKop / 100)}
      </div>
    </div>
  );
}

function MiniStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "danger";
}) {
  return (
    <div className="flex items-baseline justify-between gap-1">
      <span className="truncate text-fg-subtle">{label}</span>
      <span
        className={
          tone === "danger"
            ? "font-semibold tabular-nums text-danger"
            : "font-semibold tabular-nums text-fg"
        }
      >
        {value}
      </span>
    </div>
  );
}

function Tile({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof Megaphone;
  label: string;
  value: string;
  tone?: "success" | "danger";
}) {
  const text =
    tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : "text-fg";
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/60 p-3">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
        <Icon className="h-3 w-3" /> {label}
      </div>
      <div className={`mt-1 truncate text-lg font-semibold ${text}`}>{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="font-medium text-fg">{value}</span>
    </div>
  );
}

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

function sanitize(html: string): string {
  // Allow our common safe tags, strip everything else. The text comes
  // from admin-authored broadcasts, but better belt-and-suspenders.
  return html
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "")
    .replace(/on\w+="[^"]*"/gi, "")
    .replace(/javascript:/gi, "");
}

interface DeleteProgress {
  processed?: number;
  total?: number;
  deleted?: number;
  failed?: number;
  status?: "running" | "done" | "failed" | "cancelled";
  error?: string;
}

/**
 * "Удалить у пользователей" — calls bot.delete_message for every
 * recorded (telegram_id, message_id) pair. Confirmation in two clicks
 * (the second confirms) so it's not a one-tap mistake. Live progress
 * via the bus stream.
 */
function DeleteFromUsersControl({ broadcastId }: { broadcastId: number }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [progress, setProgress] = useState<DeleteProgress | null>(null);

  const mut = useMutation({
    mutationFn: () => endpoints.broadcastDeleteFromUsers(broadcastId),
    onSuccess: (data) => {
      toast.info(`Удаляю ${data.total_messages} сообщений из чатов...`);
      setProgress({
        processed: 0,
        total: data.total_messages,
        deleted: 0,
        failed: 0,
        status: "running",
      });
      setConfirming(false);
    },
    onError: (e: unknown) => {
      const err = e as ApiError;
      toast.error(err?.detail ?? "Не удалось запустить удаление");
      setConfirming(false);
    },
  });

  useEventStream((e: BusEvent) => {
    const bid = Number(e.broadcast_id ?? 0);
    if (bid !== broadcastId) return;
    if (e.type === "broadcast:delete_progress") {
      setProgress({
        processed: Number(e.processed ?? 0),
        total: Number(e.total ?? 0),
        deleted: Number(e.deleted ?? 0),
        failed: Number(e.failed ?? 0),
        status: "running",
      });
    } else if (e.type === "broadcast:delete_done") {
      setProgress({
        processed: Number(e.total ?? 0),
        total: Number(e.total ?? 0),
        deleted: Number(e.deleted ?? 0),
        failed: Number(e.failed ?? 0),
        status: "done",
      });
      toast.success(
        `Удалено ${Number(e.deleted ?? 0)} / ${Number(e.total ?? 0)}`,
      );
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
    } else if (e.type === "broadcast:delete_failed") {
      setProgress((p) => ({
        ...(p ?? {}),
        status: "failed",
        error: String(e.error ?? ""),
      }));
      toast.error(String(e.error ?? "Ошибка удаления"));
    } else if (e.type === "broadcast:delete_cancelled") {
      setProgress((p) => ({
        ...(p ?? {}),
        processed: Number(e.processed ?? p?.processed ?? 0),
        total: Number(e.total ?? p?.total ?? 0),
        deleted: Number(e.deleted ?? p?.deleted ?? 0),
        failed: Number(e.failed ?? p?.failed ?? 0),
        status: "cancelled",
      }));
      toast.info(
        `Остановлено: удалено ${Number(e.deleted ?? 0)} / ${Number(e.total ?? 0)}`,
      );
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
    }
  });

  const cancel = useMutation({
    mutationFn: () => endpoints.broadcastDeleteCancel(broadcastId),
    onError: (e: unknown) => {
      const err = e as ApiError;
      toast.error(err?.detail ?? "Не удалось остановить");
    },
  });

  // Auto-clear the inline progress after `done` so the card returns
  // to its default state on next open.
  useEffect(() => {
    if (progress?.status === "done") {
      const t = window.setTimeout(() => setProgress(null), 8000);
      return () => window.clearTimeout(t);
    }
  }, [progress?.status]);

  if (progress) {
    const total = progress.total ?? 0;
    const done = progress.processed ?? 0;
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const statusLabel =
      progress.status === "done"
        ? "Готово"
        : progress.status === "failed"
        ? "Сбой"
        : progress.status === "cancelled"
        ? "Остановлено"
        : "Удаляю...";
    const barClass =
      progress.status === "failed"
        ? "h-full bg-danger transition-all"
        : progress.status === "done"
        ? "h-full bg-success transition-all"
        : progress.status === "cancelled"
        ? "h-full bg-warning transition-all"
        : "h-full bg-accent transition-all";
    return (
      <div className="flex min-w-[220px] flex-col items-stretch gap-1.5 text-right">
        <div className="flex items-center justify-end gap-2 text-[11px] text-fg-muted">
          <span>
            {statusLabel}{" "}
            <span className="font-mono">
              {done}/{total}
            </span>
            {(progress.failed ?? 0) > 0 && (
              <span className="ml-1 text-danger">· {progress.failed} fail</span>
            )}
          </span>
          {progress.status === "running" && (
            <button
              type="button"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
              className="rounded-md border border-warning/40 bg-warning/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warning hover:bg-warning/20 disabled:opacity-50"
            >
              {cancel.isPending ? "..." : "Стоп"}
            </button>
          )}
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-elevated">
          <div className={barClass} style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="btn-ghost text-danger hover:text-danger"
        title="Удалить эту рассылку из чатов пользователей"
      >
        <Trash2 className="h-3.5 w-3.5" /> Удалить у юзеров
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-danger">Точно удалить?</span>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        className="btn-ghost"
        disabled={mut.isPending}
      >
        Нет
      </button>
      <button
        type="button"
        onClick={() => mut.mutate()}
        disabled={mut.isPending}
        className="btn-danger"
      >
        <Trash2 className="h-3.5 w-3.5" /> Да, удалить
      </button>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════
// SCHEDULED BROADCASTS
// ═══════════════════════════════════════════════════════════════════
//
// Время планировщика — Europe/Moscow (UTC+3). Админ вводит дату+время
// в MSK, бэкенд конвертирует в UTC для хранения. Максимум +4 недели
// вперёд. Recurrence:
//   once     — один запуск в указанное время
//   daily    — каждый день в это же время
//   weekdays — пн-пт
//   weekly   — раз в неделю в тот же день недели

const RECURRENCE_LABELS: Record<string, string> = {
  once: "Разовая (один запуск)",
  daily: "Каждый день",
  weekdays: "Только по будням (пн–пт)",
  weekly: "Раз в неделю",
};

// JS Date.getDay(): 0=Sun..6=Sat. Наш порядок — с понедельника.
const WEEKDAYS_MON_FIRST: { js: number; short: string; long: string }[] = [
  { js: 1, short: "Пн", long: "понедельник" },
  { js: 2, short: "Вт", long: "вторник" },
  { js: 3, short: "Ср", long: "среда" },
  { js: 4, short: "Чт", long: "четверг" },
  { js: 5, short: "Пт", long: "пятница" },
  { js: 6, short: "Сб", long: "суббота" },
  { js: 0, short: "Вс", long: "воскресенье" },
];

/** MSK-время сейчас в формате `YYYY-MM-DDTHH:MM` (для <input type=datetime-local>). */
function _nowPlusMinInMSK(minutesAhead: number): string {
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
  const msk = new Date(utcMs + 3 * 60 * 60_000 + minutesAhead * 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${msk.getUTCFullYear()}-${pad(msk.getUTCMonth() + 1)}-${pad(
    msk.getUTCDate(),
  )}T${pad(msk.getUTCHours())}:${pad(msk.getUTCMinutes())}`;
}

/** Convert `YYYY-MM-DDTHH:MM` (datetime-local) → API-format `YYYY-MM-DD HH:MM`. */
function _toApiFmt(local: string): string {
  return local.replace("T", " ");
}

/** Weekday (0=Sun..6=Sat) для `YYYY-MM-DDTHH:MM`. Wall-дата, tz-independent. */
function _weekdayOf(local: string): number {
  const [datePart] = local.split("T");
  const [Y, M, D] = datePart.split("-").map(Number);
  return new Date(Y, M - 1, D).getDay();
}

/** Сдвигает scheduled_at на ближайшее указанное weekday (0=Sun..6=Sat),
 *  сохраняя HH:MM. Если сегодня уже нужный день — оставит сегодня
 *  (валидация "в прошлом" — на backend). */
function _snapToWeekday(local: string, targetWeekday: number): string {
  const [datePart, timePart] = local.split("T");
  const [Y, M, D] = datePart.split("-").map(Number);
  const jsWeekday = new Date(Y, M - 1, D).getDay();
  const daysAhead = ((targetWeekday - jsWeekday) + 7) % 7;
  const next = new Date(Y, M - 1, D + daysAhead);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(
    next.getDate(),
  )}T${timePart}`;
}

function ScheduleBroadcastModal({
  broadcastId,
  onClose,
}: {
  broadcastId: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [scheduledAt, setScheduledAt] = useState(() => _nowPlusMinInMSK(60)); // +1ч по умолчанию
  const [recurrence, setRecurrence] = useState<"once" | "daily" | "weekdays" | "weekly">("once");
  const [endEnabled, setEndEnabled] = useState(false);
  const [endAt, setEndAt] = useState(() => _nowPlusMinInMSK(60 * 24 * 7)); // +1 неделя
  // null → отправлять по сегменту исходной рассылки; строка → override.
  const [segmentOverride, setSegmentOverride] = useState<string | null>(null);

  // Список всех сегментов + live-счётчики. Обновляется каждые 5 мин на бэкенде,
  // на клиенте — при открытии модалки (staleTime дефолт).
  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: () => endpoints.broadcastSegments(),
    staleTime: 60_000,
  });

  const currentWeekday = _weekdayOf(scheduledAt);
  const currentWeekdayLabel =
    WEEKDAYS_MON_FIRST.find((w) => w.js === currentWeekday)?.long ?? "—";

  const create = useMutation({
    mutationFn: () =>
      endpoints.broadcastScheduleCreate({
        source_broadcast_id: broadcastId,
        scheduled_at_msk: _toApiFmt(scheduledAt),
        recurrence,
        recurrence_end_at_msk:
          recurrence !== "once" && endEnabled ? _toApiFmt(endAt) : null,
        segment: segmentOverride,
      }),
    onSuccess: (data) => {
      toast.success(
        `Запланировано на ${data.scheduled_at_msk.slice(0, 16).replace("T", " ")} МСК`,
      );
      qc.invalidateQueries({ queryKey: ["broadcasts", "scheduled"] });
      onClose();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось запланировать"),
  });

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="card w-full max-w-md p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CalendarIcon className="h-4 w-4 text-fg-muted" />
            <h3 className="text-base font-semibold">
              Запланировать рассылку #{broadcastId}
            </h3>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-3 rounded-lg border border-info/20 bg-info/[0.06] p-3 text-xs text-fg-muted">
          <b className="text-info">Как работает:</b>
          <ul className="ml-3 mt-1 list-disc space-y-0.5">
            <li>Клон исходной рассылки: тот же текст, фото, кнопки, скидка</li>
            <li>Сегмент вычисляется в момент отправки (не сейчас)</li>
            <li>Время — <b>Europe/Moscow (UTC+3)</b></li>
            <li>Максимум +4 недели вперёд от текущего момента</li>
            <li>Для повторяющихся можно указать дату окончания</li>
          </ul>
        </div>

        <label className="mb-3 block">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            Дата и время (МСК)
          </div>
          <input
            type="datetime-local"
            value={scheduledAt}
            min={_nowPlusMinInMSK(1)}
            max={_nowPlusMinInMSK(60 * 24 * 28)}
            onChange={(e) => setScheduledAt(e.target.value)}
            className="input"
          />
          <div className="mt-1 text-[11px] text-fg-subtle">
            Мин. +1 мин, макс. +28 дней · {currentWeekdayLabel}
          </div>
        </label>

        <div className="mb-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            Повторение
          </div>
          <div className="space-y-1">
            {(["once", "daily", "weekdays", "weekly"] as const).map((r) => (
              <label
                key={r}
                className={
                  recurrence === r
                    ? "flex cursor-pointer items-center gap-3 rounded-xl border border-accent/40 bg-accent/10 px-3 py-2 text-sm"
                    : "flex cursor-pointer items-center gap-3 rounded-xl border border-border bg-bg-card px-3 py-2 text-sm hover:border-fg-subtle"
                }
              >
                <input
                  type="radio"
                  name="recurrence"
                  value={r}
                  checked={recurrence === r}
                  onChange={() => setRecurrence(r)}
                  className="accent-accent"
                />
                <Repeat className="h-3 w-3 text-fg-muted" />
                {RECURRENCE_LABELS[r]}
              </label>
            ))}
          </div>
        </div>

        {recurrence === "weekly" && (
          <div className="mb-3 rounded-xl border border-border bg-bg-subtle/40 p-3">
            <div className="mb-2 text-[11px] uppercase tracking-wider text-fg-subtle">
              День недели повтора
            </div>
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS_MON_FIRST.map((w) => (
                <button
                  type="button"
                  key={w.js}
                  onClick={() =>
                    setScheduledAt((cur) => _snapToWeekday(cur, w.js))
                  }
                  className={
                    currentWeekday === w.js
                      ? "rounded-lg border border-accent/40 bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accent"
                      : "rounded-lg border border-border bg-bg-card px-2.5 py-1 text-xs font-medium text-fg-muted hover:border-fg-subtle hover:text-fg"
                  }
                  title={`Сдвинуть на ближайшую ${w.long.toLowerCase()}у`}
                >
                  {w.short}
                </button>
              ))}
            </div>
            <div className="mt-1.5 text-[11px] text-fg-subtle">
              Клик по дню — сдвигает первую отправку на ближайший этот
              день недели (время сохраняется). Дальше каждые 7 дней.
            </div>
          </div>
        )}

        <div className="mb-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
            Сегмент получателей
          </div>
          <select
            value={segmentOverride ?? ""}
            onChange={(e) => setSegmentOverride(e.target.value || null)}
            className="input"
            disabled={segments.isLoading}
          >
            <option value="">
              {segments.isLoading
                ? "Загружаю сегменты…"
                : "— Как в исходной рассылке —"}
            </option>
            {(segments.data ?? []).map((s) => (
              <option key={s.key} value={s.key}>
                {s.group ? `[${s.group}] ` : ""}
                {s.label} · {s.count} чел
              </option>
            ))}
          </select>
          <div className="mt-1 text-[11px] text-fg-subtle">
            Аудитория пересчитывается в момент отправки — показанное число
            «на сейчас» для ориентира.
          </div>
        </div>

        {recurrence !== "once" && (
          <div className="mb-3 rounded-xl border border-border bg-bg-subtle/40 p-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={endEnabled}
                onChange={(e) => setEndEnabled(e.target.checked)}
                className="accent-accent"
              />
              Ограничить дату окончания
            </label>
            {endEnabled && (
              <>
                <input
                  type="datetime-local"
                  value={endAt}
                  onChange={(e) => setEndAt(e.target.value)}
                  className="input mt-2"
                />
                <div className="mt-1 text-[11px] text-fg-subtle">
                  После этого момента повторения прекратятся (МСК)
                </div>
              </>
            )}
            {!endEnabled && (
              <div className="mt-1 text-[11px] text-fg-subtle">
                Без ограничения — будет повторяться пока не отменишь вручную
              </div>
            )}
          </div>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="btn-secondary flex-1"
            disabled={create.isPending}
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => create.mutate()}
            disabled={create.isPending || !scheduledAt}
            className="btn-primary flex-1"
          >
            {create.isPending ? (
              <Spinner />
            ) : (
              <CalendarIcon className="h-3.5 w-3.5" />
            )}
            Запланировать
          </button>
        </div>
      </div>
    </div>
  );
}

/** Отдельная секция — список запланированных задач, встраивается в
 *  главную страницу Broadcasts под общим списком. */
export function ScheduledBroadcastsSection() {
  const qc = useQueryClient();
  const [showInactive, setShowInactive] = useState(false);
  const list = useQuery({
    queryKey: ["broadcasts", "scheduled", showInactive],
    queryFn: () => endpoints.broadcastScheduleList(!showInactive, 200),
    refetchInterval: 30_000,
  });
  // Меппинг key → {label, count} для отображения читаемого имени сегмента
  // и live-счётчика в каждом ряду списка.
  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: () => endpoints.broadcastSegments(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
  const segmentMap = new Map(
    (segments.data ?? []).map((s) => [s.key, s]),
  );

  const cancel = useMutation({
    mutationFn: (id: number) => endpoints.broadcastScheduleCancel(id),
    onSuccess: () => {
      toast.success("Задание отменено");
      qc.invalidateQueries({ queryKey: ["broadcasts", "scheduled"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось отменить"),
  });

  const _fmtMsk = (iso: string): string => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      const msk = new Date(d.getTime() + (d.getTimezoneOffset() + 180) * 60_000);
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${pad(msk.getDate())}.${pad(
        msk.getMonth() + 1,
      )} ${pad(msk.getHours())}:${pad(msk.getMinutes())}`;
    } catch {
      return iso;
    }
  };

  return (
    <div className="card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <CalendarIcon className="h-4 w-4 text-fg-muted" />
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Запланированные (МСК)
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-fg-muted">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="accent-accent"
            />
            история
          </label>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="btn-ghost"
          >
            <RefreshCcw className="h-3 w-3" />
          </button>
        </div>
      </div>

      {list.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю...
        </div>
      ) : !list.data || list.data.length === 0 ? (
        <div className="rounded-lg border border-border/60 bg-bg-subtle/40 px-3 py-6 text-center text-sm text-fg-muted">
          Пока ничего не запланировано.
          <br />
          <span className="text-[11px] text-fg-subtle">
            Нажми «⏰ Запланировать» на любой рассылке слева.
          </span>
        </div>
      ) : (
        <ul className="divide-y divide-border/60">
          {list.data.map((row) => {
            const r = row as Record<string, unknown>;
            const id = Number(r.id ?? 0);
            const isActive = Boolean(r.is_active);
            const runCount = Number(r.run_count ?? 0);
            return (
              <li key={id} className="flex items-start gap-3 py-2.5">
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                  {r.recurrence === "once" ? (
                    <CalendarIcon className="h-3.5 w-3.5" />
                  ) : (
                    <Repeat className="h-3.5 w-3.5" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium text-fg">
                      {truncate(String(r.title ?? "—"), 60)}
                    </span>
                    {!isActive && (
                      <span className="badge-muted text-[10px]">
                        неактивна
                      </span>
                    )}
                    {r.recurrence !== "once" && (
                      <span className="badge-accent text-[10px]">
                        {RECURRENCE_LABELS[String(r.recurrence)] ?? String(r.recurrence)}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-fg-subtle">
                    <b>{_fmtMsk(String(r.scheduled_at ?? ""))} МСК</b>
                    {Boolean(r.segment) && (() => {
                      const key = String(r.segment);
                      const s = segmentMap.get(key);
                      return (
                        <>
                          {" · "}
                          <span className="text-fg-muted">
                            {s?.label ?? key}
                          </span>
                          {s ? (
                            <span className="ml-1 rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent tabular-nums">
                              {fmtNum(s.count)}
                            </span>
                          ) : null}
                        </>
                      );
                    })()}
                    {runCount > 0 && <> · запусков: {runCount}</>}
                    {Boolean(r.last_error) && (
                      <>
                        {" "}
                        · <span className="text-danger">ошибка</span>
                      </>
                    )}
                  </div>
                </div>
                {isActive && (
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm("Отменить это запланированное задание?"))
                        cancel.mutate(id);
                    }}
                    className="btn-ghost shrink-0 text-danger hover:text-danger"
                    title="Отменить"
                    disabled={cancel.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
