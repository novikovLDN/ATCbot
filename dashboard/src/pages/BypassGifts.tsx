import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Gift,
  Plus,
  Trash2,
  Copy,
  ChevronRight,
  RefreshCcw,
  TrendingUp,
  Database,
  Users as UsersIcon,
  X,
  Calendar,
} from "lucide-react";
import { ApiError, endpoints } from "@/lib/api";
import { fmtNum, fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";
import { StatCard } from "@/components/StatCard";

export function BypassGifts() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  const summary = useQuery({
    queryKey: ["bgift", "summary"],
    queryFn: endpoints.bgiftSummary,
  });
  const list = useQuery({
    queryKey: ["bgift", "list"],
    queryFn: () => endpoints.bgiftList(0, 50, false),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Промо
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Гифт-ссылки на ГБ
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => list.refetch()}
            className="btn-secondary"
          >
            <RefreshCcw className="h-3.5 w-3.5" /> Обновить
          </button>
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="btn-primary"
          >
            <Plus className="h-3.5 w-3.5" /> Создать
          </button>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="Всего ссылок"
          value={fmtNum(asNum(summary.data?.total_links))}
          icon={Gift}
          loading={summary.isLoading}
        />
        <StatCard
          label="Активных"
          value={fmtNum(asNum(summary.data?.active_links))}
          tone="success"
          loading={summary.isLoading}
        />
        <StatCard
          label="Использовано"
          value={fmtNum(asNum(summary.data?.total_redemptions))}
          icon={UsersIcon}
          loading={summary.isLoading}
        />
        <StatCard
          label="Выдано ГБ"
          value={`${fmtNum(asNum(summary.data?.total_gb_granted))} GB`}
          tone="accent"
          icon={Database}
          loading={summary.isLoading}
        />
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_400px]">
        <div className="card p-5">
          <div className="mb-4 text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Активные ссылки
          </div>

          {list.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Spinner /> Загружаю...
            </div>
          ) : !list.data || list.data.length === 0 ? (
            <EmptyState
              icon={Gift}
              title="Ссылок ещё нет"
              description="Создай первую — поделишься ею с пользователями, при переходе бот выдаст N ГБ обхода."
              action={
                <button
                  type="button"
                  onClick={() => setShowCreate(true)}
                  className="btn-primary"
                >
                  <Plus className="h-3.5 w-3.5" /> Создать
                </button>
              }
            />
          ) : (
            <ul className="divide-y divide-border/60">
              {list.data.map((b) => {
                const id = Number(b.id ?? 0);
                if (!id) return null;
                const uses = asNum(b.redemption_count) ?? 0;
                const max = asNum(b.max_uses) ?? 0;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setSelected(id)}
                      className={
                        selected === id
                          ? "flex w-full items-center gap-3 rounded-lg bg-accent/10 px-2 py-3 text-left text-fg shadow-[inset_0_0_0_1px_rgba(14,165,233,0.25)] transition"
                          : "flex w-full items-center gap-3 rounded-lg px-2 py-3 text-left transition hover:bg-accent/[0.04]"
                      }
                    >
                      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
                        <Gift className="h-4 w-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate font-mono text-sm text-fg">
                            {String(b.code ?? "—")}
                          </span>
                          <span className="badge-accent">
                            {fmtNum(asNum(b.gb_amount))} GB
                          </span>
                          <span
                            className={
                              uses >= max && max > 0
                                ? "badge-danger"
                                : "badge-muted"
                            }
                          >
                            {uses}/{max} исп.
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-fg-muted">
                          действует до {fmtDate(String(b.expires_at ?? ""))}
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

        {selected ? (
          <GiftDetail id={selected} onDeleted={() => {
            setSelected(null);
            qc.invalidateQueries({ queryKey: ["bgift"] });
          }} />
        ) : (
          <div className="card hidden p-6 lg:block">
            <EmptyState
              icon={Gift}
              title="Выбери ссылку"
              description="Кликни — увидишь детали и список тех, кто активировал."
            />
          </div>
        )}
      </div>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ["bgift"] });
          }}
        />
      )}
    </div>
  );
}

function GiftDetail({
  id,
  onDeleted,
}: {
  id: number;
  onDeleted: () => void;
}) {
  const detail = useQuery({
    queryKey: ["bgift", "detail", id],
    queryFn: () => endpoints.bgiftDetail(id),
  });
  const redemptions = useQuery({
    queryKey: ["bgift", "redemptions", id],
    queryFn: () => endpoints.bgiftRedemptions(id, 200),
  });
  const del = useMutation({
    mutationFn: () => endpoints.bgiftDelete(id),
    onSuccess: () => {
      toast.success("Ссылка удалена");
      onDeleted();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось удалить"),
  });

  if (detail.isLoading) {
    return (
      <div className="card flex items-center gap-3 p-6 text-sm text-fg-muted">
        <Spinner /> Загружаю...
      </div>
    );
  }
  if (!detail.data) {
    return (
      <EmptyState
        icon={Gift}
        title="Не найдено"
        description="Возможно ссылку удалили."
      />
    );
  }

  const b = detail.data;
  const code = String(b.code ?? "");
  // Format from app/handlers/start.py — deep link to the bot.
  const shareUrl = `https://t.me/atlas_secure_vpn_bot?start=bgift_${code}`;

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="card p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wider text-fg-subtle">
              Код
            </div>
            <div className="mt-1 truncate font-mono text-xl font-semibold text-fg">
              {code}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              if (confirm("Удалить ссылку? Уже активированные сохранятся."))
                del.mutate();
            }}
            disabled={del.isPending}
            className="btn-danger"
          >
            <Trash2 className="h-3.5 w-3.5" /> Удалить
          </button>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
          <Cell icon={Database} label="ГБ" value={`${fmtNum(asNum(b.gb_amount))} GB`} />
          <Cell
            icon={Calendar}
            label="Срок"
            value={`${fmtNum(asNum(b.validity_days))} дн`}
          />
          <Cell
            icon={UsersIcon}
            label="Максимум"
            value={fmtNum(asNum(b.max_uses))}
          />
          <Cell
            icon={TrendingUp}
            label="Истекает"
            value={fmtDate(String(b.expires_at ?? ""))}
          />
        </div>

        <div className="mt-4 rounded-xl border border-border bg-bg-subtle/60 p-3">
          <div className="mb-1 text-[11px] uppercase tracking-wider text-fg-subtle">
            Поделиться
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate font-mono text-xs text-fg">
              {shareUrl}
            </code>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(shareUrl);
                toast.success("Скопировано");
              }}
              className="btn-ghost"
              aria-label="Скопировать"
            >
              <Copy className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      <div className="card p-5">
        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Активации ({redemptions.data?.total ?? 0})
        </div>
        {redemptions.isLoading ? (
          <Spinner />
        ) : !redemptions.data || redemptions.data.rows.length === 0 ? (
          <div className="text-sm text-fg-muted">Никто не активировал.</div>
        ) : (
          <ul className="max-h-[400px] divide-y divide-border/60 overflow-y-auto">
            {redemptions.data.rows.map((r, i) => (
              <li key={i} className="flex items-center justify-between py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-fg">
                    tg:{String(r.telegram_id ?? "—")}
                  </div>
                  <div className="text-xs text-fg-muted">
                    {fmtDate(String(r.redeemed_at ?? ""))}
                  </div>
                </div>
                <span className="badge-success">
                  +{fmtNum(asNum(r.gb_granted))} GB
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Cell({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Gift;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/60 p-3">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg-subtle">
        <Icon className="h-3 w-3" /> {label}
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-fg">{value}</div>
    </div>
  );
}

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [gb, setGb] = useState(5);
  const [days, setDays] = useState(7);
  const [maxUses, setMaxUses] = useState(10);

  const create = useMutation({
    mutationFn: () =>
      endpoints.bgiftCreate({
        gb_amount: gb,
        validity_days: days,
        max_uses: maxUses,
      }),
    onSuccess: () => {
      toast.success("Ссылка создана");
      onCreated();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось создать"),
  });

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4 backdrop-blur-sm">
      <div className="card w-full max-w-md p-6 animate-slide-up">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-wider text-fg-subtle">
              Новая ссылка
            </div>
            <h3 className="mt-1 text-lg font-semibold text-fg">Гифт-ГБ</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost"
            aria-label="Закрыть"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="space-y-3">
          <label className="block">
            <div className="mb-1 text-xs text-fg-subtle">ГБ обхода</div>
            <input
              className="input"
              type="number"
              min={1}
              max={1024}
              value={gb}
              onChange={(e) => setGb(Math.max(1, Number(e.target.value) || 1))}
            />
            <div className="mt-1 flex gap-1.5 text-xs">
              {[1, 3, 5, 10, 20, 50].map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setGb(v)}
                  className={
                    gb === v
                      ? "rounded-md bg-accent/15 px-2 py-0.5 text-accent"
                      : "rounded-md bg-bg-elevated px-2 py-0.5 text-fg-muted hover:text-fg"
                  }
                >
                  {v}
                </button>
              ))}
            </div>
          </label>

          <label className="block">
            <div className="mb-1 text-xs text-fg-subtle">Срок действия (дней)</div>
            <input
              className="input"
              type="number"
              min={1}
              max={365}
              value={days}
              onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
            />
            <div className="mt-1 flex gap-1.5 text-xs">
              {[1, 3, 5, 7, 14, 30].map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setDays(v)}
                  className={
                    days === v
                      ? "rounded-md bg-accent/15 px-2 py-0.5 text-accent"
                      : "rounded-md bg-bg-elevated px-2 py-0.5 text-fg-muted hover:text-fg"
                  }
                >
                  {v}
                </button>
              ))}
            </div>
          </label>

          <label className="block">
            <div className="mb-1 text-xs text-fg-subtle">Максимум активаций</div>
            <input
              className="input"
              type="number"
              min={1}
              max={10000}
              value={maxUses}
              onChange={(e) =>
                setMaxUses(Math.max(1, Number(e.target.value) || 1))
              }
            />
            <div className="mt-1 flex gap-1.5 text-xs">
              {[1, 5, 10, 50, 100, 500].map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setMaxUses(v)}
                  className={
                    maxUses === v
                      ? "rounded-md bg-accent/15 px-2 py-0.5 text-accent"
                      : "rounded-md bg-bg-elevated px-2 py-0.5 text-fg-muted hover:text-fg"
                  }
                >
                  {v}
                </button>
              ))}
            </div>
          </label>
        </div>

        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost"
            disabled={create.isPending}
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="btn-primary"
          >
            {create.isPending ? <Spinner /> : <Plus className="h-3.5 w-3.5" />}
            Создать
          </button>
        </div>
      </div>
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
