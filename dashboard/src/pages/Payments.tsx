import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip as RTooltip,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Legend,
} from "recharts";
import {
  Wallet,
  CreditCard,
  TrendingUp,
  Database,
  Gauge,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  AlertCircle,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub, fmtDate } from "@/lib/format";
import { StatCard } from "@/components/StatCard";
import { Spinner } from "@/components/Spinner";
import { EmptyState } from "@/components/EmptyState";
import { Link } from "react-router-dom";

const RANGES = [
  { label: "24ч", hours: 24 },
  { label: "7д", hours: 168 },
  { label: "30д", hours: 720 },
  { label: "180д", hours: 4320 },
  { label: "1г", hours: 8760 },
];

const PROVIDER_LABELS: Record<string, string> = {
  platega: "Platega",
  cryptobot: "CryptoBot",
  telegram_stars: "Telegram Stars",
  telegram_payment: "Telegram",
  lava: "Lava",
  balance: "Балансом",
  unknown: "Не определено",
};

// Lime → cyan → category colors → neutral. Lime first because it's
// the brand accent and tends to land on the largest segment.
const CHART_COLORS = [
  "#ABF43F", // lime
  "#3FF4E5", // cyan
  "#A855F7", // purple
  "#3B82F6", // blue
  "#F59E0B", // amber
  "#F43F5E", // rose
  "#64748b", // slate fallback
];

export function Payments() {
  const [hours, setHours] = useState(168);

  const revenue = useQuery({
    queryKey: ["payments", "revenue", hours],
    queryFn: () => endpoints.paymentsRevenue(hours),
    refetchInterval: 30_000,
  });
  const byProvider = useQuery({
    queryKey: ["payments", "by-provider", hours],
    queryFn: () => endpoints.paymentsByProvider(hours),
    refetchInterval: 60_000,
  });
  const traffic = useQuery({
    queryKey: ["payments", "traffic", hours],
    queryFn: () => endpoints.paymentsTraffic(hours),
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Деньги
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Платежи
          </h1>
        </div>
        <div className="card flex items-center gap-1 p-1">
          {RANGES.map((r) => (
            <button
              key={r.hours}
              type="button"
              onClick={() => setHours(r.hours)}
              className={
                hours === r.hours
                  ? "rounded-lg bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent"
                  : "rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted hover:bg-bg-elevated hover:text-fg"
              }
            >
              {r.label}
            </button>
          ))}
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="Доход"
          value={fmtRub(revenue.data?.revenue_rubles)}
          icon={Wallet}
          tone="success"
          loading={revenue.isLoading}
        />
        <StatCard
          label="Платежей"
          value={fmtNum(revenue.data?.payments_count)}
          icon={CreditCard}
          tone="accent"
          loading={revenue.isLoading}
        />
        <StatCard
          label="Средний чек"
          value={fmtRub(revenue.data?.avg_check_rubles)}
          icon={Gauge}
          loading={revenue.isLoading}
        />
        <StatCard
          label="Трафик: продано"
          value={`${fmtNum(traffic.data?.total_gb)} GB`}
          hint={
            traffic.data ? `${fmtRub(traffic.data.revenue_rubles)} · ${fmtNum(traffic.data.count)} покупок` : undefined
          }
          icon={Database}
          tone="warning"
          loading={traffic.isLoading}
        />
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="По провайдерам" subtitle="Доход за период">
          <ProviderPie data={byProvider.data ?? []} loading={byProvider.isLoading} />
        </ChartCard>
        <ChartCard title="По тарифам" subtitle="Доход и количество">
          <TypeBar data={revenue.data?.by_type ?? {}} loading={revenue.isLoading} />
        </ChartCard>
      </div>

      {traffic.data && traffic.data.by_method.length > 0 && (
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Трафик: способы оплаты
            </div>
            <TrendingUp className="h-3 w-3 text-fg-subtle" />
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {traffic.data.by_method.map((m) => (
              <div key={m.method} className="rounded-xl border border-border bg-bg-subtle/60 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-fg">
                    {PROVIDER_LABELS[m.method] ?? m.method}
                  </span>
                  <span className="text-xs text-fg-subtle">{fmtNum(m.count)}</span>
                </div>
                <div className="mt-2 text-lg font-semibold text-success">
                  {fmtRub(m.revenue_rubles)}
                </div>
                <div className="text-xs text-fg-muted">{fmtNum(m.total_gb)} GB</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <PaymentErrors hours={hours} />
      <PaymentsFeed hours={hours} />
    </div>
  );
}

function PaymentErrors({ hours }: { hours: number }) {
  const summary = useQuery({
    queryKey: ["payments", "errors", "summary", hours],
    queryFn: () => endpoints.paymentsErrorsSummary(hours),
    refetchInterval: 30_000,
  });
  const [expanded, setExpanded] = useState(false);
  const rows = useQuery({
    queryKey: ["payments", "errors", "list", hours],
    queryFn: () => endpoints.paymentsErrors({ hours, limit: 200 }),
    enabled: expanded,
    refetchInterval: expanded ? 15_000 : false,
  });

  const total = summary.data?.total ?? 0;
  const empty = total === 0;

  return (
    <section
      className={
        empty
          ? "card p-5"
          : "card border-danger/30 bg-danger/[0.04] p-5"
      }
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <div
            className={
              empty
                ? "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border"
                : "grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-danger/15 text-danger"
            }
          >
            <AlertCircle className="h-4 w-4" />
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Ошибки платежей
            </div>
            <h2 className="text-lg font-semibold text-fg">
              {empty ? "Без сбоев" : `${fmtNum(total)} событий`}
            </h2>
          </div>
        </div>
        <ChevronDown
          className={
            expanded
              ? "h-4 w-4 rotate-180 text-fg-subtle transition"
              : "h-4 w-4 text-fg-subtle transition"
          }
        />
      </button>

      {!empty && !expanded && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {summary.data?.by_stage.slice(0, 6).map((s) => (
            <span key={s.stage} className="badge-danger">
              {labelForStage(s.stage)} · {fmtNum(s.count)}
            </span>
          ))}
          {summary.data?.by_provider.slice(0, 6).map((p) => (
            <span key={p.provider} className="badge-muted">
              {PROVIDER_LABELS[p.provider] ?? p.provider} · {fmtNum(p.count)}
            </span>
          ))}
        </div>
      )}

      {expanded && (
        <div className="mt-4 space-y-3">
          {!empty && (
            <div className="flex flex-wrap gap-1.5">
              {summary.data?.by_stage.map((s) => (
                <span key={s.stage} className="badge-danger">
                  {labelForStage(s.stage)} · {fmtNum(s.count)}
                </span>
              ))}
              {summary.data?.by_provider.map((p) => (
                <span key={p.provider} className="badge-muted">
                  {PROVIDER_LABELS[p.provider] ?? p.provider} · {fmtNum(p.count)}
                </span>
              ))}
            </div>
          )}

          {rows.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Spinner /> Загружаю...
            </div>
          ) : !rows.data || rows.data.length === 0 ? (
            <div className="text-sm text-fg-muted">
              Под текущие фильтры ошибок нет.
            </div>
          ) : (
            <ul className="divide-y divide-border/60">
              {rows.data.map((r, i) => {
                const provider = String(r.payment_provider ?? "unknown");
                const stage = String(r.stage ?? "");
                const tg = asNum(r.telegram_id);
                const code = r.error_code ? String(r.error_code) : "";
                const msg = r.error_message ? String(r.error_message) : "";
                const created =
                  typeof r.created_at === "string"
                    ? fmtDate(r.created_at)
                    : "";
                const amt = asNum(r.amount_rubles);
                return (
                  <li key={String(r.id ?? i)} className="py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="badge-danger">
                        {labelForStage(stage)}
                      </span>
                      <span className="badge-muted">
                        {PROVIDER_LABELS[provider] ?? provider}
                      </span>
                      {tg != null && (
                        <span className="text-fg">tg:{tg}</span>
                      )}
                      {r.username ? (
                        <span className="text-fg-muted">
                          @{String(r.username)}
                        </span>
                      ) : null}
                      {amt != null && (
                        <span className="text-fg-muted">
                          · {fmtRub(amt)}
                        </span>
                      )}
                      {code && (
                        <code className="rounded bg-bg-elevated px-1.5 py-0.5 text-[11px] text-fg-muted">
                          {code}
                        </code>
                      )}
                    </div>
                    {msg && (
                      <div className="mt-1 font-mono text-xs text-fg-muted line-clamp-2">
                        {msg}
                      </div>
                    )}
                    <div className="mt-1 text-[11px] text-fg-subtle">
                      {created}{" "}
                      {r.purchase_id ? (
                        <span>· {String(r.purchase_id).slice(0, 20)}</span>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function labelForStage(stage: string): string {
  const map: Record<string, string> = {
    webhook_invalid_json: "Неверный JSON",
    setup_missing: "Бот не готов",
    service_missing: "Сервис не подключён",
    transient: "Временная ошибка",
    timeout: "Таймаут",
    unhandled_exception: "Исключение",
    amount_mismatch: "Сумма не совпадает",
    provider_callback_invalid: "Невалидный callback",
    provision_failed: "Provisioning",
    idempotency_rejected: "Идемпотентность",
  };
  return map[stage] ?? stage;
}

function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card p-5">
      <div className="mb-4">
        <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
          {title}
        </div>
        {subtitle && (
          <div className="text-sm text-fg-muted">{subtitle}</div>
        )}
      </div>
      <div className="h-[260px]">{children}</div>
    </div>
  );
}

function ProviderPie({
  data,
  loading,
}: {
  data: Array<{ provider: string; revenue_rubles: number; count: number }>;
  loading: boolean;
}) {
  const rows = useMemo(
    () =>
      data
        .filter((d) => d.revenue_rubles > 0)
        .map((d) => ({
          name: PROVIDER_LABELS[d.provider] ?? d.provider,
          value: d.revenue_rubles,
          count: d.count,
        })),
    [data],
  );

  if (loading) {
    return (
      <div className="grid h-full place-items-center text-sm text-fg-muted">
        <Spinner />
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="grid h-full place-items-center text-sm text-fg-subtle">
        Нет данных за период
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie
          data={rows}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius={50}
          outerRadius={90}
          paddingAngle={2}
        >
          {rows.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
          ))}
        </Pie>
        <RTooltip
          contentStyle={{
            background: "rgb(20 23 32)",
            border: "1px solid rgb(38 42 54)",
            borderRadius: 12,
            fontSize: 12,
          }}
          formatter={(value: number, _name: string, item) =>
            [`${fmtRub(value)} · ${fmtNum(item?.payload?.count)} шт`, item?.payload?.name]
          }
        />
        <Legend
          verticalAlign="bottom"
          iconType="circle"
          wrapperStyle={{ fontSize: 11, color: "rgb(156 163 175)" }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

function TypeBar({
  data,
  loading,
}: {
  data: Record<string, { count: number; revenue_rubles: number }>;
  loading: boolean;
}) {
  const rows = useMemo(
    () =>
      Object.entries(data)
        .map(([type, v]) => ({
          name: type,
          revenue: v.revenue_rubles,
          count: v.count,
        }))
        .sort((a, b) => b.revenue - a.revenue),
    [data],
  );

  if (loading) {
    return (
      <div className="grid h-full place-items-center text-sm text-fg-muted">
        <Spinner />
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="grid h-full place-items-center text-sm text-fg-subtle">
        Нет данных
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgb(38 42 54)" />
        <XAxis
          dataKey="name"
          tick={{ fill: "rgb(156 163 175)", fontSize: 11 }}
          axisLine={{ stroke: "rgb(38 42 54)" }}
        />
        <YAxis
          tick={{ fill: "rgb(156 163 175)", fontSize: 11 }}
          axisLine={{ stroke: "rgb(38 42 54)" }}
        />
        <RTooltip
          contentStyle={{
            background: "rgb(20 23 32)",
            border: "1px solid rgb(38 42 54)",
            borderRadius: 12,
            fontSize: 12,
          }}
          formatter={(value: number, _name: string, item) =>
            item?.dataKey === "revenue"
              ? [fmtRub(value), "Доход"]
              : [fmtNum(value), "Штук"]
          }
        />
        <Legend
          verticalAlign="top"
          height={28}
          iconType="circle"
          wrapperStyle={{ fontSize: 11, color: "rgb(156 163 175)" }}
        />
        <Bar dataKey="revenue" name="Доход" fill="#ABF43F" radius={[4, 4, 0, 0]} />
        <Bar dataKey="count" name="Штук" fill="#3FF4E5" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

interface PaymentRow extends Record<string, unknown> {
  id?: number;
  purchase_id?: string;
  telegram_id?: number;
  username?: string;
  tariff?: string;
  purchase_type?: string;
  period_days?: number;
  price_rubles?: number;
  price_kopecks?: number;
  payment_provider?: string;
  status?: string;
  promo_code?: string;
  is_combo?: boolean;
  country?: string;
  created_at?: string;
}

function PaymentsFeed({ hours }: { hours: number }) {
  const [status, setStatus] = useState<"" | "paid" | "pending" | "expired">("");

  const feed = useQuery({
    queryKey: ["payments", "recent", hours, status],
    queryFn: () =>
      endpoints.paymentsRecent({
        limit: 200,
        hours,
        status: status || undefined,
      }) as Promise<PaymentRow[]>,
    refetchInterval: 30_000,
  });

  return (
    <section className="card p-5">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Лента
          </div>
          <h2 className="text-lg font-semibold text-fg">
            Последние платежи
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {(["", "paid", "pending", "expired"] as const).map((s) => (
            <button
              key={s || "all"}
              type="button"
              onClick={() => setStatus(s)}
              className={
                status === s
                  ? "rounded-lg bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent"
                  : "rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted hover:bg-bg-elevated hover:text-fg"
              }
            >
              {s === "" ? "Все" : s === "paid" ? "Успех" : s === "pending" ? "В обработке" : "Истекли"}
            </button>
          ))}
        </div>
      </div>

      {feed.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю...
        </div>
      ) : !feed.data || feed.data.length === 0 ? (
        <EmptyState
          icon={Wallet}
          title="Пусто"
          description="Под текущие фильтры платежей нет."
        />
      ) : (
        <ul className="divide-y divide-border/60">
          {feed.data.map((p) => (
            <PaymentRowItem key={String(p.id ?? Math.random())} p={p} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PaymentRowItem({ p }: { p: PaymentRow }) {
  const [expanded, setExpanded] = useState(false);
  const tg = Number(p.telegram_id ?? 0);

  return (
    <li>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-3 py-3 text-left transition hover:bg-bg-elevated/30"
      >
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-bg-elevated text-fg-muted ring-1 ring-border">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-fg">
              {p.username ? `@${String(p.username)}` : `tg:${tg}`}
            </span>
            {p.username && tg > 0 && (
              <span className="font-mono text-[11px] text-fg-subtle">
                tg:{tg}
              </span>
            )}
            <StatusBadge status={String(p.status ?? "")} />
            <span className="badge-muted">
              {String(p.purchase_type ?? "—")}
              {p.tariff ? ` · ${String(p.tariff)}` : ""}
              {p.is_combo ? " · combo" : ""}
            </span>
            {p.payment_provider && p.payment_provider !== "unknown" && (
              <span className="badge-accent">
                {PROVIDER_LABELS[String(p.payment_provider)] ?? String(p.payment_provider)}
              </span>
            )}
            {p.promo_code && (
              <span className="badge-warning">promo: {String(p.promo_code)}</span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-fg-muted">
            <span>{fmtDate(String(p.created_at ?? ""))}</span>
            {p.period_days != null && (
              <span>· {fmtNum(asNum(p.period_days))} дн</span>
            )}
            {p.country && <span>· {String(p.country)}</span>}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold text-fg">
            {fmtRub(p.price_rubles)}
          </div>
          <div className="text-[11px] text-fg-subtle">
            {String(p.purchase_id ?? "").slice(0, 16) || "—"}
          </div>
        </div>
      </button>

      {expanded && tg > 0 && <ExpandedUser telegramId={tg} />}
    </li>
  );
}

function ExpandedUser({ telegramId }: { telegramId: number }) {
  const detail = useQuery({
    queryKey: ["users", "detail", telegramId],
    queryFn: () => endpoints.userDetail(telegramId),
    staleTime: 60_000,
  });

  if (detail.isLoading) {
    return (
      <div className="ml-12 mb-3 flex items-center gap-2 text-sm text-fg-muted">
        <Spinner /> Загружаю профиль...
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="ml-12 mb-3 flex items-center gap-2 text-sm text-danger">
        <AlertCircle className="h-3.5 w-3.5" /> Не удалось загрузить юзера
      </div>
    );
  }
  const d = detail.data;
  const u = d.user as Record<string, unknown>;
  const sub = d.subscription as Record<string, unknown> | null;

  return (
    <div className="ml-12 mb-3 rounded-xl border border-border bg-bg-subtle/40 p-3 text-sm">
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 md:grid-cols-4">
        <Pair label="Баланс" value={fmtRub(d.balance_rubles)} />
        <Pair label="VIP" value={d.is_vip ? "да" : "нет"} />
        <Pair
          label="Тариф"
          value={sub ? String(sub.subscription_type ?? "—") : "—"}
        />
        <Pair
          label="Истекает"
          value={sub ? fmtDate(String(sub.expires_at ?? "")) : "—"}
        />
        <Pair
          label="Зарегистрирован"
          value={
            typeof u.created_at === "string" ? fmtDate(u.created_at) : "—"
          }
        />
        <Pair
          label="Язык"
          value={(u.language as string | undefined) ?? "—"}
        />
        <Pair
          label="Триал"
          value={d.trial ? "использован" : "—"}
        />
        <Pair
          label="Скидка"
          value={
            d.discount
              ? `${String(
                  (d.discount as Record<string, unknown>).discount_percent ?? "—",
                )}%`
              : "—"
          }
        />
      </div>
      <div className="mt-3 flex justify-end">
        <Link
          to={`/users?tg=${telegramId}`}
          className="btn-ghost"
        >
          <ExternalLink className="h-3 w-3" /> Полная карточка
        </Link>
      </div>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-fg-subtle">
        {label}
      </div>
      <div className="font-medium text-fg">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s === "paid") return <span className="tag-green">оплачен</span>;
  if (s === "pending") return <span className="tag-amber">ожидает</span>;
  if (s === "expired") return <span className="badge-muted">истёк</span>;
  if (s === "failed") return <span className="tag-rose">ошибка</span>;
  return <span className="badge-muted">{status || "—"}</span>;
}

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}
