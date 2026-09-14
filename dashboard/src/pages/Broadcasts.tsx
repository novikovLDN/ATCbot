import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  Megaphone,
  RefreshCcw,
  ChevronRight,
  Check,
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
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/Spinner";
import { PageHeader, Surface } from "@/components/ui/Surface";
import { IconButton, PillProgress, StatusDot } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

/** A small chip that stays visible on a `bg-tile-3` row. */
const CHIP = "badge bg-tile-1 t-mute";

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
  tag?: string | null;
  tag_color?: string | null;
}

// 7 семантических цветов. Совпадают с backend _VALID_TAG_COLORS.
// Значения — классы на токенах v3 (работают в обеих темах). «Жёлтый»
// в палитре v3 — это фирменный кремовый accent.
const TAG_COLOR_CLASSES: Record<string, string> = {
  gray: "bg-ink/10 text-body",
  red: "bg-danger/15 text-danger",
  orange: "bg-warning/15 text-warning",
  yellow: "bg-accent/15 text-accent",
  green: "bg-success/15 text-success",
  blue: "bg-info/15 text-info",
  purple: "bg-special/15 text-special",
};

const TAG_COLOR_LABELS: Array<{ key: string; label: string }> = [
  { key: "gray", label: "Серый" },
  { key: "red", label: "Красный · срочное" },
  { key: "orange", label: "Оранжевый · реактивация" },
  { key: "yellow", label: "Жёлтый · тест" },
  { key: "green", label: "Зелёный · новое" },
  { key: "blue", label: "Синий · инфо" },
  { key: "purple", label: "Фиолетовый · особое" },
];

function TagEditor({
  broadcastId,
  currentTag,
  currentColor,
}: {
  broadcastId: number;
  currentTag: string;
  currentColor: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [tag, setTag] = useState(currentTag);
  const [color, setColor] = useState(currentColor || "gray");

  const save = useMutation({
    mutationFn: () =>
      endpoints.broadcastPatchTag(
        broadcastId,
        tag.trim() || null,
        tag.trim() ? color : null,
      ),
    onSuccess: () => {
      toast.success(tag.trim() ? "Тег обновлён" : "Тег снят");
      qc.invalidateQueries({ queryKey: ["broadcasts"] });
      setEditing(false);
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось сохранить тег"),
  });

  if (!editing) {
    return (
      <div className="mt-1.5">
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="t-mute tap-target text-[12px] underline decoration-dotted underline-offset-4 hover:text-ink"
        >
          {currentTag ? "изменить тег" : "+ добавить тег"}
        </button>
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-row bg-tile-2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          maxLength={40}
          placeholder="летняя акция"
          className="input min-w-0 flex-1"
          aria-label="Тег рассылки"
          autoFocus
        />
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="btn-primary"
          aria-label="Сохранить тег"
        >
          {save.isPending ? <Spinner /> : <CheckCircle2 className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={() => {
            setEditing(false);
            setTag(currentTag);
            setColor(currentColor || "gray");
          }}
          className="btn-ghost"
          aria-label="Отменить изменение тега"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {TAG_COLOR_LABELS.map((c) => {
          const active = color === c.key;
          return (
            <button
              type="button"
              key={c.key}
              onClick={() => setColor(c.key)}
              title={c.label}
              aria-label={c.label}
              aria-pressed={active}
              className={cn(
                "tap-target grid h-7 w-7 place-items-center rounded-full text-[12px] font-semibold transition-opacity",
                TAG_COLOR_CLASSES[c.key],
                active ? "opacity-100" : "opacity-60 hover:opacity-100",
              )}
            >
              {active ? <Check className="h-3.5 w-3.5" strokeWidth={2.5} /> : "●"}
            </button>
          );
        })}
        <span className="t-mute ml-1 text-[12px]">цвет метки</span>
      </div>
    </div>
  );
}

function TagChip({ tag, color }: { tag: string; color?: string | null }) {
  const cls = TAG_COLOR_CLASSES[color || "gray"] ?? TAG_COLOR_CLASSES.gray;
  return (
    <span className={`badge font-semibold ${cls}`}>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-60" />
      {tag}
    </span>
  );
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
    <>
      <PageHeader
        title="Рассылки"
        sub="Маркетинг"
        actions={
          <>
            <button
              type="button"
              onClick={() =>
                setViewMode((m) => (m === "compact" ? "expanded" : "compact"))
              }
              className="btn-secondary"
              aria-label={viewMode === "compact" ? "Расширенно" : "Компактно"}
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
              aria-label="Обновить"
            >
              <RefreshCcw className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Обновить</span>
            </button>
            <Link to="/broadcasts/new" className="btn-primary">
              <Plus className="h-3.5 w-3.5" /> Создать
            </Link>
          </>
        }
      />

      {/* Two columns from lg (1024px) — the same breakpoint the
          scroll-into-view effect above and the «К списку» button use. */}
      <div className="grid grid-cols-1 gap-[var(--gap)] lg:grid-cols-[minmax(0,1fr)_400px]">
        <Surface
          label="Последние 500"
          aside={list.isFetching ? <Spinner /> : undefined}
        >
          {list.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-[68px] w-full rounded-row" />
              ))}
            </div>
          ) : list.isError && !list.data ? (
            <ErrorState error={list.error} onRetry={() => list.refetch()} />
          ) : !list.data || list.data.length === 0 ? (
            <EmptyState
              title="Пока пусто"
              hint="Когда отправите первую рассылку, она появится здесь."
            />
          ) : (
            <ul className="flex flex-col gap-2">
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
        </Surface>

        <div ref={detailRef} className="flex min-w-0 flex-col gap-[var(--gap)]">
          {selected !== null ? (
            <BroadcastDetail
              id={selected}
              progress={sending[selected]}
              onBack={() => setSelected(null)}
            />
          ) : (
            <Surface className="hidden lg:block" variant="raised">
              <EmptyState
                title="Выбери рассылку"
                hint="Кликни по строке слева, чтобы посмотреть деталь и статистику отправки."
              />
            </Surface>
          )}
          <ScheduledBroadcastsSection />
        </div>
      </div>
    </>
  );
}

function SendProgressBar({ prog }: { prog: SendProgress }) {
  const pct =
    prog.total > 0
      ? Math.min(100, Math.round((prog.processed / prog.total) * 100))
      : prog.status === "done"
      ? 100
      : 0;
  return (
    <PillProgress
      value={pct}
      knob={prog.status === "running"}
      label={
        <span className="tabular text-[12px]">
          {prog.processed}/{prog.total} · {pct}%
        </span>
      }
      valueLabel={
        <span className="text-[12px]">
          ✓ {prog.sent}
          {prog.failed > 0 && (
            <span className="ml-1 text-danger">· ✗ {prog.failed}</span>
          )}
        </span>
      }
    />
  );
}

/** Live send status as a badge — the same look in the row and the detail. */
function SendStatusBadge({ status }: { status: SendProgress["status"] }) {
  if (status === "running") {
    return (
      <span className="badge-accent">
        <Send className="h-3 w-3 animate-pulse" /> отправляется
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="badge-success">
        <CheckCircle2 className="h-3 w-3" /> готово
      </span>
    );
  }
  return (
    <span className="badge-danger">
      <AlertCircle className="h-3 w-3" /> сбой
    </span>
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

  // Selection is told by shade (row one step lighter) and the icon
  // disc turning cream — no outline.
  const iconDisc = (
    <span
      className={cn(
        "grid h-9 w-9 shrink-0 place-items-center rounded-full transition-colors",
        selected ? "bg-accent text-onaccent" : "bg-tile-1 t-mute",
      )}
      aria-hidden="true"
    >
      <Megaphone className="h-3.5 w-3.5" />
    </span>
  );

  if (mode === "expanded") {
    // Расширенная карточка. Клик по всей карточке (кроме кнопки) → select.
    return (
      <li>
        <div
          className={cn(
            "rounded-row p-3 transition-colors",
            selected ? "bg-tile-4" : "bg-tile-3 hover:bg-tile-4",
          )}
        >
          <div className="flex items-start gap-3">
            <button
              type="button"
              onClick={onSelect}
              aria-pressed={selected}
              className="flex min-w-0 flex-1 items-start gap-3 text-left"
            >
              {iconDisc}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[14px] font-semibold">
                    {truncate(String(row.title ?? "Без названия"), 80)}
                  </span>
                  {row.tag && <TagChip tag={String(row.tag)} color={row.tag_color as string | undefined} />}
                  {row.is_ab_test && <span className={CHIP}>A/B</span>}
                  {(running || done || failed) && progress && (
                    <SendStatusBadge status={progress.status} />
                  )}
                </div>
                <div className="t-mute mt-0.5 text-[12px]">
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
              aria-label="Снова"
              title="Клонировать текст+фото+кнопки в новую рассылку"
            >
              <Copy className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Снова</span>
            </button>
          </div>

          {/* Полный текст сообщения */}
          {typeof row.message === "string" && row.message && (
            <div className="mt-3 rounded-[12px] bg-tile-2 p-3 text-[13px] leading-relaxed">
              <div
                className="whitespace-pre-wrap"
                dangerouslySetInnerHTML={{
                  __html: expandedSanitize(String(row.message)),
                }}
              />
            </div>
          )}

          {/* Правая колонка: сегмент + получатели + прогресс */}
          <div className="t-mute mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
            {row.segment && (
              <span className="inline-flex items-center gap-1">
                <UsersIcon className="h-3 w-3" /> сегмент:{" "}
                <b className="font-semibold text-ink">{String(row.segment)}</b>
              </span>
            )}
            {typeof row.total_recipients === "number" && (
              <span className="inline-flex items-center gap-1">
                · получателей: <b className="tabular font-semibold text-ink">{fmtNum(row.total_recipients)}</b>
              </span>
            )}
            {typeof row.sent_count === "number" && (
              <span className="tabular inline-flex items-center gap-1.5">
                · <StatusDot tone="ok" /> ✓ {fmtNum(row.sent_count)}
              </span>
            )}
            {typeof row.failed_count === "number" && row.failed_count > 0 && (
              <span className="tabular inline-flex items-center gap-1.5 text-danger">
                · <StatusDot tone="err" /> ✕ {fmtNum(row.failed_count)}
              </span>
            )}
          </div>
          {progress && progress.total > 0 && (
            <div className="mt-3">
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
        aria-pressed={selected}
        className={cn(
          "flex w-full items-start gap-3 rounded-row p-3 text-left transition-colors",
          selected ? "bg-tile-4" : "bg-tile-3 hover:bg-tile-4",
        )}
      >
        {iconDisc}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-medium">
              {truncate(String(row.title ?? "Без названия"), 60)}
            </span>
            {row.tag && <TagChip tag={String(row.tag)} color={row.tag_color as string | undefined} />}
            {row.is_ab_test && <span className={CHIP}>A/B</span>}
            {row.broadcast_type && (
              <span className={CHIP}>{String(row.broadcast_type)}</span>
            )}
            {(running || done || failed) && progress && (
              <SendStatusBadge status={progress.status} />
            )}
          </div>
          {typeof row.message === "string" && (
            <div className="t-body mt-1 truncate text-[13px]">
              {truncate(String(row.message), 100)}
            </div>
          )}
          <div className="t-mute mt-1 flex flex-wrap items-center gap-2 text-[12px]">
            {row.created_at && <span>{fmtDate(String(row.created_at))}</span>}
            {row.segment && <span>· сегмент {String(row.segment)}</span>}
          </div>
          {progress && progress.total > 0 && (
            <div className="mt-3">
              <SendProgressBar prog={progress} />
            </div>
          )}
        </div>
        <ChevronRight className="t-mute mt-2.5 h-4 w-4 shrink-0" />
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
      <Surface label={`Рассылка #${id}`} aria-label="Загрузка рассылки">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="mt-4 h-11 w-full rounded-full" />
        <div className="mt-4 grid grid-cols-3 gap-2">
          <Skeleton className="h-[72px] rounded-row" />
          <Skeleton className="h-[72px] rounded-row" />
          <Skeleton className="h-[72px] rounded-row" />
        </div>
        <Skeleton className="mt-4 h-32 w-full rounded-row" />
      </Surface>
    );
  }
  if (det.isError || !det.data) {
    return <ErrorState error={det.error} onRetry={() => det.refetch()} />;
  }

  const b = det.data as BroadcastRow;
  const s = (stats.data ?? {}) as BroadcastRow;

  return (
    <Surface
      className="animate-fade-in"
      label={`Рассылка #${id}`}
      aside={
        onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="btn-ghost lg:hidden"
            aria-label="Назад к списку"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> К списку
          </button>
        ) : undefined
      }
    >
      {showSchedule && (
        <ScheduleBroadcastModal
          broadcastId={id}
          onClose={() => setShowSchedule(false)}
        />
      )}
      <h3 className="flex flex-wrap items-center gap-2 text-[15px] font-semibold leading-6">
        {truncate(String(b.title ?? "Без названия"), 80)}
        {b.tag && <TagChip tag={String(b.tag)} color={b.tag_color as string | undefined} />}
      </h3>
      <TagEditor
        broadcastId={id}
        currentTag={(b.tag as string) || ""}
        currentColor={(b.tag_color as string) || "gray"}
      />

      <div className="mt-4 flex flex-wrap items-center gap-2">
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

      {progress && (
        <div className="mt-4 rounded-row bg-tile-3 p-3">
          <div className="mb-2.5 flex items-center justify-between gap-2">
            {progress.status === "running" && (
              <span className="badge-accent">
                <Send className="h-3.5 w-3.5 animate-pulse" /> Отправляю...
              </span>
            )}
            {progress.status === "done" && (
              <span className="badge-success">
                <CheckCircle2 className="h-3.5 w-3.5" /> Готово
              </span>
            )}
            {progress.status === "failed" && (
              <span className="badge-danger">
                <AlertCircle className="h-3.5 w-3.5" /> Сбой отправки
              </span>
            )}
            <span className="t-mute tabular text-[12px]">
              {progress.processed}/{progress.total}
            </span>
          </div>
          <SendProgressBar prog={progress} />
          {progress.status === "failed" && progress.error && (
            <div className="mt-2 break-all text-[12px] text-danger">
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

      <div className="mt-4 flex flex-col gap-1.5">
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
        <div className="mt-5">
          <h4 className="mb-2 flex items-center gap-1.5 text-[15px] font-semibold">
            <Clock className="t-mute h-3.5 w-3.5" /> Текст
          </h4>
          <div
            className="whitespace-pre-wrap rounded-row bg-tile-3 p-4 text-[14px] leading-relaxed"
            dangerouslySetInnerHTML={{ __html: sanitize(String(b.message)) }}
          />
        </div>
      )}
    </Surface>
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
      <div className="mt-5">
        <h4 className="text-[15px] font-semibold">Аналитика</h4>
        <p className="t-mute mt-1 text-[13px]">Считаю конверсию…</p>
      </div>
    );
  }
  if (error || !data) {
    return null;
  }
  const convRate = data.conversion_rate_7d;
  const blockedPct = data.blocked_estimate;
  return (
    <div className="mt-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-[15px] font-semibold">Конверсия и доход</h4>
        <span className="t-mute inline-flex items-center gap-1.5 text-[12px]">
          7д CR:
          <StatusDot
            tone={convRate >= 0.05 ? "ok" : convRate >= 0.01 ? "warn" : "idle"}
          />
          <span className="tabular font-semibold text-ink">
            {(convRate * 100).toFixed(2)}%
          </span>
        </span>
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

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
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
  // The 7-day window is the one key figure of the panel → cream.
  const sub = highlight ? "opacity-70" : "t-mute";
  return (
    <div
      className={cn(
        "min-w-0 rounded-row p-3",
        highlight ? "bg-accent text-onaccent" : "bg-tile-3",
      )}
    >
      <div className={cn("truncate text-[12px]", sub)}>Купили за {window}</div>
      <div className="tabular track-metric mt-0.5 truncate text-[22px] font-semibold leading-7">
        {fmtNum(count)}
      </div>
      <div className={cn("tabular mt-0.5 truncate text-[12px]", sub)}>
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
    <div className="flex items-baseline justify-between gap-2">
      <span className="t-mute truncate">{label}</span>
      <span
        className={
          tone === "danger"
            ? "tabular font-semibold text-danger"
            : "tabular font-semibold text-ink"
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
  const icon =
    tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : "";
  return (
    <div className="min-w-0 rounded-row bg-tile-3 p-3">
      <div className="t-mute flex items-center gap-1.5 text-[12px]">
        <Icon className={cn("h-3 w-3 shrink-0", icon)} />
        <span className="truncate">{label}</span>
      </div>
      <div className="tabular track-metric mt-1 truncate text-[22px] font-semibold leading-7">
        {value}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[14px]">
      <span className="t-mute">{label}</span>
      <span className="text-right font-medium">{value}</span>
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
    const tone =
      progress.status === "failed"
        ? "err"
        : progress.status === "done"
        ? "ok"
        : progress.status === "cancelled"
        ? "warn"
        : "accent";
    return (
      <div className="flex w-full min-w-0 flex-col gap-2 rounded-row bg-tile-3 p-3">
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="inline-flex items-center gap-1.5">
            <StatusDot tone={tone} />
            {statusLabel}{" "}
            <span className="tabular t-mute">
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
              className="btn-secondary bg-tile-1 px-3 py-1 text-[12px] text-warning"
            >
              {cancel.isPending ? "..." : "Стоп"}
            </button>
          )}
        </div>
        <PillProgress value={pct} knob={progress.status === "running"} />
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
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[13px] text-danger">Точно удалить?</span>
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
      <div
        className="tile w-full max-w-md p-5"
        role="dialog"
        aria-modal="true"
        aria-label={`Запланировать рассылку #${broadcastId}`}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <CalendarIcon className="t-mute h-4 w-4 shrink-0" />
            <h3 className="text-[18px] font-semibold leading-6">
              Запланировать рассылку #{broadcastId}
            </h3>
          </div>
          <IconButton label="Закрыть" onClick={onClose} className="bg-tile-3">
            <X className="h-4 w-4" />
          </IconButton>
        </div>

        <div className="t-body mb-4 rounded-row bg-tile-3 p-4 text-[13px] leading-5">
          <b className="font-semibold text-ink">Как работает:</b>
          <ul className="ml-4 mt-1 list-disc space-y-0.5">
            <li>Клон исходной рассылки: тот же текст, фото, кнопки, скидка</li>
            <li>Сегмент вычисляется в момент отправки (не сейчас)</li>
            <li>Время — <b className="font-semibold text-ink">Europe/Moscow (UTC+3)</b></li>
            <li>Максимум +4 недели вперёд от текущего момента</li>
            <li>Для повторяющихся можно указать дату окончания</li>
          </ul>
        </div>

        <label className="mb-4 block">
          <div className="t-mute mb-1.5 text-[13px]">Дата и время (МСК)</div>
          <input
            type="datetime-local"
            value={scheduledAt}
            min={_nowPlusMinInMSK(1)}
            max={_nowPlusMinInMSK(60 * 24 * 28)}
            onChange={(e) => setScheduledAt(e.target.value)}
            className="input"
          />
          <div className="t-mute mt-1 text-[12px]">
            Мин. +1 мин, макс. +28 дней · {currentWeekdayLabel}
          </div>
        </label>

        <div className="mb-4">
          <div className="t-mute mb-1.5 text-[13px]">Повторение</div>
          <div className="flex flex-col gap-1.5">
            {(["once", "daily", "weekdays", "weekly"] as const).map((r) => (
              <label
                key={r}
                className={cn(
                  "flex min-h-[44px] cursor-pointer items-center gap-3 rounded-row px-3 py-2 text-[14px] transition-colors",
                  recurrence === r ? "bg-tile-4 text-ink" : "t-body bg-tile-3 hover:bg-tile-4",
                )}
              >
                <input
                  type="radio"
                  name="recurrence"
                  value={r}
                  checked={recurrence === r}
                  onChange={() => setRecurrence(r)}
                  className="accent-accent"
                />
                <Repeat className="t-mute h-3 w-3" />
                {RECURRENCE_LABELS[r]}
              </label>
            ))}
          </div>
        </div>

        {recurrence === "weekly" && (
          <div className="mb-4 rounded-row bg-tile-3 p-3">
            <div className="t-mute mb-2 text-[13px]">День недели повтора</div>
            <div className="capsule-nav flex-wrap" role="group" aria-label="День недели повтора">
              {WEEKDAYS_MON_FIRST.map((w) => (
                <button
                  type="button"
                  key={w.js}
                  onClick={() =>
                    setScheduledAt((cur) => _snapToWeekday(cur, w.js))
                  }
                  aria-pressed={currentWeekday === w.js}
                  className="capsule-tab h-8 px-3 text-[12px]"
                  title={`Сдвинуть на ближайшую ${w.long.toLowerCase()}у`}
                >
                  {w.short}
                </button>
              ))}
            </div>
            <div className="t-mute mt-2 text-[12px] leading-4">
              Клик по дню — сдвигает первую отправку на ближайший этот
              день недели (время сохраняется). Дальше каждые 7 дней.
            </div>
          </div>
        )}

        <div className="mb-4">
          <div className="t-mute mb-1.5 text-[13px]">Сегмент получателей</div>
          <select
            value={segmentOverride ?? ""}
            onChange={(e) => setSegmentOverride(e.target.value || null)}
            className="input"
            aria-label="Сегмент получателей"
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
          <div className="t-mute mt-1 text-[12px] leading-4">
            Аудитория пересчитывается в момент отправки — показанное число
            «на сейчас» для ориентира.
          </div>
        </div>

        {recurrence !== "once" && (
          <div className="mb-4 rounded-row bg-tile-2 p-3">
            <label className="flex min-h-[44px] cursor-pointer items-center gap-2 text-[14px]">
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
                  className="input mt-1"
                  aria-label="Дата окончания повторений (МСК)"
                />
                <div className="t-mute mt-1 text-[12px]">
                  После этого момента повторения прекратятся (МСК)
                </div>
              </>
            )}
            {!endEnabled && (
              <div className="t-mute text-[12px]">
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
    <Surface
      variant="raised"
      label="Запланированные (МСК)"
      aside={
        <div className="flex items-center gap-2">
          <label className="t-mute tap-target inline-flex cursor-pointer items-center gap-1.5 text-[12px]">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="accent-accent"
            />
            история
          </label>
          <IconButton small label="Обновить" onClick={() => list.refetch()} className="bg-tile-3">
            <RefreshCcw className="h-3.5 w-3.5" />
          </IconButton>
        </div>
      }
    >
      {list.isLoading ? (
        <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
          <Skeleton className="h-[60px] w-full rounded-row" />
          <Skeleton className="h-[60px] w-full rounded-row" />
        </div>
      ) : list.isError && !list.data ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          title="Пока ничего не запланировано."
          hint="Нажми «⏰ Запланировать» на любой рассылке слева."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {list.data.map((row) => {
            const r = row as Record<string, unknown>;
            const id = Number(r.id ?? 0);
            const isActive = Boolean(r.is_active);
            const runCount = Number(r.run_count ?? 0);
            return (
              <li key={id} className="list-row items-start py-3">
                <span
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-tile-1 t-mute"
                  aria-hidden="true"
                >
                  {r.recurrence === "once" ? (
                    <CalendarIcon className="h-3.5 w-3.5" />
                  ) : (
                    <Repeat className="h-3.5 w-3.5" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[14px] font-medium">
                      {truncate(String(r.title ?? "—"), 60)}
                    </span>
                    {!isActive && <span className={CHIP}>неактивна</span>}
                    {r.recurrence !== "once" && (
                      <span className="badge-accent">
                        {RECURRENCE_LABELS[String(r.recurrence)] ?? String(r.recurrence)}
                      </span>
                    )}
                  </div>
                  <div className="t-mute mt-0.5 text-[12px]">
                    <b className="tabular font-semibold text-ink">
                      {_fmtMsk(String(r.scheduled_at ?? ""))} МСК
                    </b>
                    {Boolean(r.segment) && (() => {
                      const key = String(r.segment);
                      const s = segmentMap.get(key);
                      return (
                        <>
                          {" · "}
                          <span className="t-body">
                            {s?.label ?? key}
                          </span>
                          {s ? (
                            <span className="tabular ml-1 rounded-full bg-tile-1 px-1.5 py-0.5 font-semibold text-ink">
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
                  <IconButton
                    small
                    label="Отменить"
                    onClick={() => {
                      if (confirm("Отменить это запланированное задание?"))
                        cancel.mutate(id);
                    }}
                    className="bg-tile-1 text-danger"
                    disabled={cancel.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </IconButton>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Surface>
  );
}
