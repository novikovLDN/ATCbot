import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Database,
  Loader2,
  Receipt,
  RefreshCw,
  Wrench,
} from "lucide-react";
import { endpoints, ApiError } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { toast } from "@/store/toast";

// Bypass-audit — таблица пострадавших от бага «premium на 10 лет».
// Backend (см. database/admin.py:get_bypass_overwrite_victims) ловит
// юзеров с is_bypass_only=TRUE AND expires_at > NOW+3y AND есть платная
// история. Для каждого вычисляет proposed_expires_at = MAX(end_date) по
// subscription_history → именно эту дату админ применяет на UPDATE.

const fmtDateTime = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const fmtDate = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
};

const daysDiff = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return Math.round((d.getTime() - Date.now()) / 86_400_000);
};

export function BypassAudit() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["bypass-audit"],
    queryFn: endpoints.bypassAuditList,
  });

  const fixOne = useMutation({
    mutationFn: (tg: number) => endpoints.bypassAuditFixOne(tg),
    onSuccess: (data) => {
      toast.success(`Юзер ${data.telegram_id} восстановлен`);
      qc.invalidateQueries({ queryKey: ["bypass-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось восстановить"),
  });

  const fixAll = useMutation({
    mutationFn: () => endpoints.bypassAuditFixAll(),
    onSuccess: (data) => {
      toast.success(
        `Готово: восстановлено ${data.fixed} из ${data.total}, ошибок ${data.failed}`,
      );
      qc.invalidateQueries({ queryKey: ["bypass-audit"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Массовое восстановление упало"),
  });

  const [confirmAll, setConfirmAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<"all" | "can_fix" | "no_fix">("can_fix");

  const total = q.data?.total ?? 0;
  const canFix = q.data?.can_fix ?? 0;
  const totalGb = q.data?.total_traffic_gb_purchased ?? 0;

  const victims = useMemo(() => {
    const all = q.data?.victims ?? [];
    if (filter === "can_fix") return all.filter((v) => v.can_fix);
    if (filter === "no_fix") return all.filter((v) => !v.can_fix);
    return all;
  }, [q.data, filter]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
            Maintenance
          </div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-fg md:text-4xl">
            Bypass Audit
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-fg-muted">
            Юзеры, которым старый flow покупки трафика выдал «premium на 10 лет»
            поверх их реальной подписки. По каждому собрана полная история —
            платежи, продления, пакеты ГБ — и предложен корректный{" "}
            <code className="rounded bg-bg-subtle px-1 py-0.5 font-mono text-xs">
              expires_at
            </code>{" "}
            на основе{" "}
            <code className="rounded bg-bg-subtle px-1 py-0.5 font-mono text-xs">
              MAX(subscription_history.end_date)
            </code>
            .
          </p>
        </div>
        <button
          type="button"
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          className="btn-secondary text-xs"
        >
          {q.isFetching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Перепроверить
        </button>
      </header>

      {/* Summary */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard
          label="Пострадавших"
          value={fmtNum(total)}
          icon={AlertTriangle}
          tone="warning"
          loading={q.isLoading}
        />
        <SummaryCard
          label="Можно восстановить"
          value={fmtNum(canFix)}
          icon={Wrench}
          tone="accent"
          loading={q.isLoading}
        />
        <SummaryCard
          label="Без истории платежей"
          value={fmtNum(Math.max(0, total - canFix))}
          icon={Database}
          tone="muted"
          loading={q.isLoading}
        />
        <SummaryCard
          label="ГБ куплено пострадавшими"
          value={`${fmtNum(totalGb)} ГБ`}
          icon={Receipt}
          tone="info"
          loading={q.isLoading}
        />
      </section>

      {/* Action bar */}
      <section className="card flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-2">
          <Filter value={filter} onChange={setFilter} canFix={canFix} noFix={Math.max(0, total - canFix)} total={total} />
        </div>
        <div className="flex items-center gap-2">
          {!confirmAll ? (
            <button
              type="button"
              onClick={() => setConfirmAll(true)}
              disabled={canFix === 0 || fixAll.isPending}
              className="btn-primary"
            >
              <Wrench className="h-3.5 w-3.5" />
              Восстановить всех ({fmtNum(canFix)})
            </button>
          ) : (
            <>
              <span className="text-xs text-fg-muted">
                Точно? UPDATE на {fmtNum(canFix)} строк.
              </span>
              <button
                type="button"
                onClick={() => setConfirmAll(false)}
                className="btn-secondary text-xs"
                disabled={fixAll.isPending}
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={() => {
                  fixAll.mutate(undefined, {
                    onSettled: () => setConfirmAll(false),
                  });
                }}
                disabled={fixAll.isPending}
                className="btn-danger text-xs"
              >
                {fixAll.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wrench className="h-3.5 w-3.5" />
                )}
                Да, применить
              </button>
            </>
          )}
        </div>
      </section>

      {/* List */}
      {q.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton h-32" />
          ))}
        </div>
      ) : q.isError ? (
        <div className="card border-danger/30 bg-danger/5 p-4 text-sm text-danger">
          Не удалось загрузить список. Попробуй обновить.
        </div>
      ) : victims.length === 0 ? (
        <div className="card grid place-items-center gap-2 p-12 text-center">
          <CheckCircle2 className="h-10 w-10 text-success" />
          <div className="text-base font-medium text-fg">
            {total === 0 ? "Пострадавших нет — всё чисто." : "Под выбранный фильтр никто не попал."}
          </div>
          {total > 0 && filter !== "all" && (
            <button
              type="button"
              onClick={() => setFilter("all")}
              className="text-xs text-accent underline"
            >
              Показать всех ({total})
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {victims.map((v) => (
            <VictimRow
              key={v.telegram_id}
              v={v}
              expanded={expanded.has(v.telegram_id)}
              onToggle={() =>
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(v.telegram_id)) next.delete(v.telegram_id);
                  else next.add(v.telegram_id);
                  return next;
                })
              }
              onFix={() => fixOne.mutate(v.telegram_id)}
              fixing={fixOne.isPending && fixOne.variables === v.telegram_id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─ Components ────────────────────────────────────────────────────────

function SummaryCard({
  label,
  value,
  icon: Icon,
  tone,
  loading,
}: {
  label: string;
  value: string;
  icon: typeof AlertTriangle;
  tone: "warning" | "accent" | "muted" | "info";
  loading?: boolean;
}) {
  const toneClass =
    tone === "warning"
      ? "text-amber-500 bg-amber-50 ring-amber-200"
      : tone === "accent"
      ? "text-sky-600 bg-sky-50 ring-sky-200"
      : tone === "info"
      ? "text-violet-600 bg-violet-50 ring-violet-200"
      : "text-slate-500 bg-slate-50 ring-slate-200";
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
            {label}
          </div>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-fg md:text-3xl">
            {loading ? "…" : value}
          </div>
        </div>
        <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ring-1 ${toneClass}`}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
    </div>
  );
}

function Filter({
  value,
  onChange,
  canFix,
  noFix,
  total,
}: {
  value: "all" | "can_fix" | "no_fix";
  onChange: (v: "all" | "can_fix" | "no_fix") => void;
  canFix: number;
  noFix: number;
  total: number;
}) {
  const opts: Array<{ key: "all" | "can_fix" | "no_fix"; label: string; count: number }> = [
    { key: "can_fix", label: "Можно восстановить", count: canFix },
    { key: "no_fix", label: "Без истории", count: noFix },
    { key: "all", label: "Все", count: total },
  ];
  return (
    <div className="inline-flex rounded-full border border-border bg-bg-card p-0.5 text-xs font-medium">
      {opts.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={
            "rounded-full px-3 py-1.5 transition-colors " +
            (value === o.key
              ? "bg-fg text-bg-card"
              : "text-fg-muted hover:text-fg")
          }
        >
          {o.label} · <span className="tabular-nums">{fmtNum(o.count)}</span>
        </button>
      ))}
    </div>
  );
}

type Victim = NonNullable<Awaited<ReturnType<typeof endpoints.bypassAuditList>>>["victims"][number];

function VictimRow({
  v,
  expanded,
  onToggle,
  onFix,
  fixing,
}: {
  v: Victim;
  expanded: boolean;
  onToggle: () => void;
  onFix: () => void;
  fixing: boolean;
}) {
  const currDays = daysDiff(v.current_expires_at);
  const propDays = daysDiff(v.proposed_expires_at);
  const historyDays = daysDiff(v.history_end_date);
  const grace = v.grace_will_apply;
  const totalPaidRub = v.payments.reduce((a, p) => a + (p.amount_rubles || 0), 0);

  return (
    <article className="card overflow-hidden p-0">
      {/* Top summary row */}
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-fg-subtle" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-fg-subtle" />
        )}
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-subtle text-xs font-semibold tabular-nums text-fg-muted ring-1 ring-border">
          {String(v.telegram_id).slice(-3)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium text-fg">
              {v.username ? `@${v.username}` : `tg:${v.telegram_id}`}
            </span>
            <span className="text-[11px] tabular-nums text-fg-subtle">
              tg:{v.telegram_id}
            </span>
            {v.current_is_combo && (
              <span className="badge bg-violet-100 text-violet-700 ring-1 ring-violet-200">combo</span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-fg-muted">
            <span>
              {fmtNum(v.payments_count)} платежей · {fmtRub(totalPaidRub)}
            </span>
            <span>·</span>
            <span>
              {fmtNum(v.traffic_purchases.length)} паков ГБ · {fmtNum(v.traffic_total_gb)} ГБ
            </span>
          </div>
        </div>

        {/* Before / After capsule */}
        <div className="hidden shrink-0 items-center gap-2 sm:flex">
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-right">
            <div className="text-[9px] font-medium uppercase tracking-wider text-amber-700">
              Сейчас
            </div>
            <div className="text-xs font-semibold tabular-nums text-amber-900">
              {currDays != null ? `+${currDays.toLocaleString("ru-RU")} дн` : "—"}
            </div>
          </div>
          <ChevronRight className="h-3 w-3 shrink-0 text-fg-subtle" />
          <div
            className={
              "rounded-lg px-2.5 py-1 text-right border " +
              (grace
                ? "border-sky-200 bg-sky-50"
                : "border-emerald-200 bg-emerald-50")
            }
          >
            <div
              className={
                "text-[9px] font-medium uppercase tracking-wider " +
                (grace ? "text-sky-700" : "text-emerald-700")
              }
            >
              {grace ? "Grace +1д" : "Будет"}
            </div>
            <div
              className={
                "text-xs font-semibold tabular-nums " +
                (grace ? "text-sky-900" : "text-emerald-900")
              }
            >
              {propDays != null ? `+${propDays.toLocaleString("ru-RU")} дн` : "—"}
            </div>
          </div>
        </div>

        {v.can_fix ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onFix();
            }}
            disabled={fixing}
            className="btn-primary shrink-0 text-xs"
          >
            {fixing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wrench className="h-3.5 w-3.5" />
            )}
            Восстановить
          </button>
        ) : (
          <span className="badge bg-slate-100 text-slate-500 ring-1 ring-slate-200">
            нет истории
          </span>
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-border bg-bg-subtle/30 p-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <DetailBlock title="Текущее состояние" tone="warning">
              <KV label="expires_at" value={fmtDateTime(v.current_expires_at)} />
              <KV label="через дней" value={currDays != null ? currDays.toLocaleString("ru-RU") : "—"} />
              <KV label="is_bypass_only" value={v.current_is_bypass_only ? "TRUE" : "FALSE"} />
              <KV label="type" value={v.current_subscription_type ?? "—"} />
              <KV label="source" value={v.current_source ?? "—"} />
            </DetailBlock>
            <DetailBlock title="Будет применено" tone={grace ? "info" : "success"}>
              <KV label="expires_at" value={fmtDateTime(v.proposed_expires_at)} />
              <KV
                label="через"
                value={propDays != null ? `${propDays.toLocaleString("ru-RU")} дн` : "—"}
              />
              {grace && (
                <KV
                  label="grace"
                  value={
                    historyDays != null
                      ? `+1 день (история истекла ${Math.abs(historyDays).toLocaleString("ru-RU")} дн назад)`
                      : "+1 день"
                  }
                />
              )}
              <KV label="is_bypass_only" value="FALSE" />
              <KV label="source" value="payment (если был bypass_only)" />
              <KV label="источник" value={v.last_paid_action_type ?? "—"} />
            </DetailBlock>
          </div>

          {/* Payments table */}
          <SubBlock title={`Платежи · ${v.payments.length}`} icon={Receipt}>
            {v.payments.length === 0 ? (
              <div className="text-xs text-fg-subtle">Нет одобренных платежей.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-fg-subtle">
                      <Th>Когда</Th>
                      <Th>Тариф</Th>
                      <Th align="right">Сумма</Th>
                      <Th>Purchase ID</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {v.payments.map((p) => (
                      <tr key={p.id}>
                        <Td>{fmtDateTime(p.paid_at ?? p.created_at)}</Td>
                        <Td>
                          <span className="font-mono">{p.tariff}</span>
                        </Td>
                        <Td align="right">
                          <span className="font-semibold tabular-nums">{fmtRub(p.amount_rubles)}</span>
                        </Td>
                        <Td>
                          <span className="font-mono text-[10px] text-fg-subtle">
                            {p.purchase_id ? p.purchase_id.slice(0, 16) : "—"}
                          </span>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SubBlock>

          {/* Subscription history */}
          <SubBlock title={`История подписок · ${v.history.length}`} icon={Clock}>
            {v.history.length === 0 ? (
              <div className="text-xs text-fg-subtle">История пуста.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-fg-subtle">
                      <Th>Когда</Th>
                      <Th>Action</Th>
                      <Th>Start</Th>
                      <Th>End</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {v.history.map((h) => {
                      const isPaid = ["purchase", "renewal", "auto_renew"].includes(h.action_type);
                      return (
                        <tr key={h.id}>
                          <Td>{fmtDateTime(h.created_at)}</Td>
                          <Td>
                            <span
                              className={
                                "badge text-[10px] " +
                                (isPaid
                                  ? "bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200"
                                  : "bg-slate-100 text-slate-600 ring-1 ring-slate-200")
                              }
                            >
                              {h.action_type}
                            </span>
                          </Td>
                          <Td>{fmtDate(h.start_date)}</Td>
                          <Td>{fmtDate(h.end_date)}</Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </SubBlock>

          {/* Traffic purchases */}
          <SubBlock title={`Пакеты ГБ · ${v.traffic_purchases.length}`} icon={Database}>
            {v.traffic_purchases.length === 0 ? (
              <div className="text-xs text-fg-subtle">Пакеты не покупал.</div>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {v.traffic_purchases.map((t) => (
                  <div key={t.id} className="rounded-lg border border-border bg-bg-card p-3">
                    <div className="text-xs font-semibold tabular-nums text-fg">
                      {fmtNum(t.gb_amount)} ГБ
                    </div>
                    <div className="mt-0.5 text-[10px] text-fg-muted tabular-nums">
                      {fmtRub(t.price_rub)} · {fmtDate(t.created_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SubBlock>
        </div>
      )}
    </article>
  );
}

function DetailBlock({
  title,
  tone,
  children,
}: {
  title: string;
  tone: "warning" | "success" | "muted" | "info";
  children: React.ReactNode;
}) {
  const ringClass =
    tone === "warning"
      ? "border-amber-200 bg-amber-50/50"
      : tone === "success"
      ? "border-emerald-200 bg-emerald-50/50"
      : tone === "info"
      ? "border-sky-200 bg-sky-50/50"
      : "border-slate-200 bg-slate-50/50";
  return (
    <div className={`rounded-xl border ${ringClass} p-3`}>
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {title}
      </div>
      <dl className="mt-2 space-y-1.5">{children}</dl>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="truncate font-mono text-fg">{value}</dd>
    </div>
  );
}

function SubBlock({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: typeof Receipt;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-4 rounded-xl border border-border bg-bg-card p-3">
      <div className="mb-2 flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        <Icon className="h-3.5 w-3.5" />
        {title}
      </div>
      {children}
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={
        "px-2 py-1.5 text-[10px] font-medium uppercase tracking-wider " +
        (align === "right" ? "text-right" : "text-left")
      }
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <td
      className={
        "px-2 py-2 text-xs text-fg " + (align === "right" ? "text-right" : "text-left")
      }
    >
      {children}
    </td>
  );
}
