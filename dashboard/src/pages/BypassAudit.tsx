import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
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
import { cn } from "@/lib/cn";
import { Bento, PageHeader, Surface } from "@/components/ui/Surface";
import { KpiTile } from "@/components/ui/KpiTile";
import { Segmented, StatusDot, type Tone } from "@/components/ui/controls";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";

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

const INLINE_CODE = "font-mono text-[12px] on-shell";

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
    <>
      <PageHeader
        title="Bypass Audit"
        sub={
          <>
            Юзеры, которым старый flow покупки трафика выдал «premium на 10 лет» поверх их реальной подписки. По
            каждому собрана полная история — платежи, продления, пакеты ГБ — и предложен корректный{" "}
            <code className={INLINE_CODE}>expires_at</code> на основе{" "}
            <code className={INLINE_CODE}>MAX(subscription_history.end_date)</code>.
          </>
        }
        actions={
          <button type="button" onClick={() => q.refetch()} disabled={q.isFetching} className="btn-secondary">
            {q.isFetching ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Перепроверить
          </button>
        }
      />

      <Bento>
        {/* Summary */}
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          label="Пострадавших"
          loading={q.isLoading}
          value={
            <span className="inline-flex items-center gap-2.5">
              <StatusDot tone="warn" />
              {fmtNum(total)}
            </span>
          }
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          variant="accent"
          label="Можно восстановить"
          loading={q.isLoading}
          value={fmtNum(canFix)}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          variant="raised"
          label="Без истории платежей"
          loading={q.isLoading}
          value={fmtNum(Math.max(0, total - canFix))}
        />
        <KpiTile
          className="sm:col-span-3 xl:col-span-3"
          size="sm"
          variant="steel"
          label="ГБ куплено пострадавшими"
          loading={q.isLoading}
          value={`${fmtNum(totalGb)} ГБ`}
        />

        {/* Action bar + list */}
        <Surface className="sm:col-span-6 xl:col-span-12" label="Пострадавшие">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="scrollbar-none -mx-1 max-w-full overflow-x-auto px-1">
              <Filter
                value={filter}
                onChange={setFilter}
                canFix={canFix}
                noFix={Math.max(0, total - canFix)}
                total={total}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {!confirmAll ? (
                <button
                  type="button"
                  onClick={() => setConfirmAll(true)}
                  disabled={canFix === 0 || fixAll.isPending}
                  className="btn-primary"
                >
                  <Wrench className="h-3.5 w-3.5" />
                  Восстановить всех (<span className="tabular">{fmtNum(canFix)}</span>)
                </button>
              ) : (
                <>
                  <span className="t-body text-[13px]">Точно? UPDATE на {fmtNum(canFix)} строк.</span>
                  <button
                    type="button"
                    onClick={() => setConfirmAll(false)}
                    className="btn-secondary"
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
                    className="btn-danger"
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
          </div>

          {q.isLoading ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Загрузка">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-24 rounded-row" />
              ))}
            </div>
          ) : q.isError ? (
            <ErrorState className="rounded-row bg-tile-3 p-4" error={q.error} onRetry={() => q.refetch()} />
          ) : victims.length === 0 ? (
            <EmptyState
              title={total === 0 ? "Пострадавших нет — всё чисто." : "Под выбранный фильтр никто не попал."}
              action={
                total > 0 && filter !== "all" ? (
                  <button type="button" onClick={() => setFilter("all")} className="btn-secondary mt-1">
                    Показать всех ({total})
                  </button>
                ) : undefined
              }
            />
          ) : (
            <div className="flex flex-col gap-2">
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
        </Surface>
      </Bento>
    </>
  );
}

// ─ Components ────────────────────────────────────────────────────────

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
    <Segmented
      label="Фильтр"
      value={value}
      onChange={onChange}
      options={opts.map((o) => ({ value: o.key, label: `${o.label} · ${fmtNum(o.count)}` }))}
    />
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
    <article className="overflow-hidden rounded-row bg-tile-3">
      {/* Top summary row */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 px-3 py-3 text-left transition-colors hover:bg-tile-4"
      >
        {expanded ? (
          <ChevronDown className="t-mute h-4 w-4 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="t-mute h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <div className="t-mute tabular grid h-9 w-9 shrink-0 place-items-center rounded-full bg-tile-1 text-[12px] font-semibold">
          {String(v.telegram_id).slice(-3)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-[14px] font-medium">
              {v.username ? `@${v.username}` : `tg:${v.telegram_id}`}
            </span>
            <span className="t-mute tabular text-[12px]">tg:{v.telegram_id}</span>
            {v.current_is_combo && <span className="badge-special">combo</span>}
          </div>
          <div className="t-mute tabular mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
            <span>
              {fmtNum(v.payments_count)} платежей · {fmtRub(totalPaidRub)}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              {fmtNum(v.traffic_purchases.length)} паков ГБ · {fmtNum(v.traffic_total_gb)} ГБ
            </span>
          </div>
        </div>

        {/* Before / After capsule */}
        <div className="hidden shrink-0 items-center gap-2 sm:flex">
          <DaysChip tone="warn" label="Сейчас" days={currDays} />
          <ChevronRight className="t-mute h-3 w-3 shrink-0" aria-hidden="true" />
          <DaysChip tone={grace ? "accent" : "ok"} label={grace ? "Grace +1д" : "Будет"} days={propDays} />
        </div>

        {v.can_fix ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onFix();
            }}
            disabled={fixing}
            className="btn-primary shrink-0"
          >
            {fixing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wrench className="h-3.5 w-3.5" />
            )}
            Восстановить
          </button>
        ) : (
          <span className="badge shrink-0 bg-tile-1 text-mute">нет истории</span>
        )}
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="flex flex-col gap-3 px-3 pb-3">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <DetailBlock title="Текущее состояние" tone="warn">
              <KV label="expires_at" value={fmtDateTime(v.current_expires_at)} />
              <KV label="через дней" value={currDays != null ? currDays.toLocaleString("ru-RU") : "—"} />
              <KV label="is_bypass_only" value={v.current_is_bypass_only ? "TRUE" : "FALSE"} />
              <KV label="type" value={v.current_subscription_type ?? "—"} />
              <KV label="source" value={v.current_source ?? "—"} />
            </DetailBlock>
            <DetailBlock title="Будет применено" tone={grace ? "accent" : "ok"}>
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
              <p className="t-mute text-[13px]">Нет одобренных платежей.</p>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="dtable min-w-[560px]">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th>Тариф</th>
                      <th className="num">Сумма</th>
                      <th>Purchase ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.payments.map((p) => (
                      <tr key={p.id}>
                        <td className="tabular">{fmtDateTime(p.paid_at ?? p.created_at)}</td>
                        <td className="font-mono">{p.tariff}</td>
                        <td className="num font-semibold">{fmtRub(p.amount_rubles)}</td>
                        <td className="t-mute font-mono text-[12px]">
                          {p.purchase_id ? p.purchase_id.slice(0, 16) : "—"}
                        </td>
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
              <p className="t-mute text-[13px]">История пуста.</p>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="dtable min-w-[560px]">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th>Action</th>
                      <th>Start</th>
                      <th>End</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.history.map((h) => {
                      const isPaid = ["purchase", "renewal", "auto_renew"].includes(h.action_type);
                      return (
                        <tr key={h.id}>
                          <td className="tabular">{fmtDateTime(h.created_at)}</td>
                          <td>
                            <span className={isPaid ? "badge-success" : "badge-muted"}>{h.action_type}</span>
                          </td>
                          <td className="tabular">{fmtDate(h.start_date)}</td>
                          <td className="tabular">{fmtDate(h.end_date)}</td>
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
              <p className="t-mute text-[13px]">Пакеты не покупал.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {v.traffic_purchases.map((t) => (
                  <div key={t.id} className="rounded-row bg-tile-3 p-3">
                    <div className="tabular text-[14px] font-semibold">{fmtNum(t.gb_amount)} ГБ</div>
                    <div className="t-mute tabular mt-0.5 text-[12px]">
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

function DaysChip({ tone, label, days }: { tone: Tone; label: string; days: number | null }) {
  return (
    <div className="rounded-row bg-tile-1 px-3 py-1.5 text-right">
      <div className="t-mute flex items-center justify-end gap-1.5 text-[12px]">
        <StatusDot tone={tone} />
        {label}
      </div>
      <div className="tabular text-[13px] font-semibold">
        {days != null ? `+${days.toLocaleString("ru-RU")} дн` : "—"}
      </div>
    </div>
  );
}

function DetailBlock({
  title,
  tone,
  children,
}: {
  title: string;
  tone: Tone;
  children: ReactNode;
}) {
  return (
    <div className="rounded-row bg-tile-1 p-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold">
        <StatusDot tone={tone} />
        {title}
      </div>
      <dl className="mt-2 flex flex-col gap-1.5">{children}</dl>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[13px]">
      <dt className="t-mute">{label}</dt>
      <dd className="tabular truncate font-mono text-[12px]">{value}</dd>
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
  children: ReactNode;
}) {
  return (
    <div className={cn("rounded-row bg-tile-1 p-4")}>
      <h3 className="mb-3 flex items-center gap-2 text-[15px] font-semibold">
        <Icon className="t-mute h-4 w-4" aria-hidden="true" />
        {title}
      </h3>
      {children}
    </div>
  );
}
