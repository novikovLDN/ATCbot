import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Megaphone,
  RefreshCcw,
  ChevronRight,
  CheckCircle2,
  AlertCircle,
  Clock,
  Users as UsersIcon,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtDate, fmtNum, truncate } from "@/lib/format";
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

export function Broadcasts() {
  const list = useQuery({
    queryKey: ["broadcasts", "recent"],
    queryFn: () => endpoints.broadcastsRecent(50) as Promise<BroadcastRow[]>,
    refetchInterval: 30_000,
  });

  const [selected, setSelected] = useState<number | null>(null);

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
        <button
          type="button"
          onClick={() => list.refetch()}
          className="btn-secondary"
        >
          <RefreshCcw className="h-3.5 w-3.5" /> Обновить
        </button>
      </header>

      <div className="rounded-2xl border border-accent/30 bg-accent/5 p-4 text-sm text-fg-muted">
        <div className="flex items-start gap-3">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent/15 text-accent">
            <Megaphone className="h-4 w-4" />
          </div>
          <div>
            <div className="font-medium text-fg">Конструктор рассылок — в следующей фазе</div>
            <div className="mt-1">
              Создание новых рассылок и preset-шаблонов пока остаётся в боте (через
              <code className="mx-1 rounded bg-bg-elevated px-1 py-0.5 font-mono text-xs">/admin</code>).
              Здесь — журнал отправленных и доставки в realtime.
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_400px]">
        <div className="card p-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Последние 50
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
            <ul className="divide-y divide-border/60">
              {list.data.map((b) => {
                const id = Number(b.id ?? 0);
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setSelected(id)}
                      className={
                        selected === id
                          ? "flex w-full items-start gap-3 rounded-lg bg-bg-elevated/60 px-2 py-3 text-left transition"
                          : "flex w-full items-start gap-3 rounded-lg px-2 py-3 text-left transition hover:bg-bg-elevated/40"
                      }
                    >
                      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                        <Megaphone className="h-3.5 w-3.5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-fg">
                            {truncate(String(b.title ?? "Без названия"), 60)}
                          </span>
                          {b.is_ab_test && (
                            <span className="badge-muted">A/B</span>
                          )}
                          {b.broadcast_type && (
                            <span className="badge-muted">
                              {String(b.broadcast_type)}
                            </span>
                          )}
                        </div>
                        {typeof b.message === "string" && (
                          <div className="mt-1 truncate text-xs text-fg-muted">
                            {truncate(String(b.message), 100)}
                          </div>
                        )}
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-fg-subtle">
                          {b.created_at && (
                            <span>{fmtDate(String(b.created_at))}</span>
                          )}
                          {b.segment && (
                            <span>· сегмент {String(b.segment)}</span>
                          )}
                        </div>
                      </div>
                      <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {selected !== null ? (
          <BroadcastDetail id={selected} />
        ) : (
          <div className="card hidden p-6 lg:block">
            <EmptyState
              icon={Megaphone}
              title="Выбери рассылку"
              description="Кликни по строке слева, чтобы посмотреть деталь и статистику отправки."
            />
          </div>
        )}
      </div>
    </div>
  );
}

function BroadcastDetail({ id }: { id: number }) {
  const det = useQuery({
    queryKey: ["broadcasts", "detail", id],
    queryFn: () => endpoints.broadcastDetail(id),
  });
  const stats = useQuery({
    queryKey: ["broadcasts", "stats", id],
    queryFn: () => endpoints.broadcastStats(id),
    refetchInterval: 5_000,
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

  const b = det.data;
  const s = stats.data ?? {};

  return (
    <div className="card p-5 animate-fade-in">
      <div className="mb-3 text-xs uppercase tracking-wider text-fg-subtle">
        Рассылка #{id}
      </div>
      <h3 className="text-lg font-semibold text-fg">
        {truncate(String(b.title ?? "Без названия"), 80)}
      </h3>

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
