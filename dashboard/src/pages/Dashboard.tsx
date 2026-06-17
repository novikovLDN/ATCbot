import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Megaphone,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { endpoints } from "@/lib/api";
import { useEventStream, type BusEvent } from "@/lib/ws";
import {
  fmtNum,
  fmtRub,
  fmtRelative,
  mskDayKey,
  mskTodayStartIso,
} from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";

// ─ Daily chart metric/range config ──────────────────────────────────

type MetricKey =
  | "revenue_rubles"
  | "new_users"
  | "payments_count"
  | "new_subscriptions"
  | "new_paid_subscriptions";

interface MetricDef {
  key: MetricKey;
  label: string;
  short: string;
  color: string;          // line/fill stroke
  fillId: string;          // <linearGradient id>
  valueFmt: (v: number) => string;
  axisFmt: (v: number) => string;
}

const fmtCompactInt = (v: number): string => {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(v >= 10_000 ? 0 : 1)}k`;
  return Math.round(v).toString();
};
const fmtCompactRub = (v: number): string => `${fmtCompactInt(v)} ₽`;

const METRICS: readonly MetricDef[] = [
  {
    key: "revenue_rubles",
    label: "Доход",
    short: "Доход",
    color: "#0EA5E9",
    fillId: "metric-revenue",
    valueFmt: (v) => fmtRub(v),
    axisFmt: fmtCompactRub,
  },
  {
    key: "new_users",
    label: "Новые юзеры",
    short: "Юзеры",
    color: "#8B5CF6",
    fillId: "metric-users",
    valueFmt: (v) => fmtNum(v),
    axisFmt: fmtCompactInt,
  },
  {
    key: "payments_count",
    label: "Платежи",
    short: "Платежи",
    color: "#10B981",
    fillId: "metric-payments",
    valueFmt: (v) => fmtNum(v),
    axisFmt: fmtCompactInt,
  },
  {
    key: "new_subscriptions",
    label: "Новые подписки",
    short: "Подписки",
    color: "#F59E0B",
    fillId: "metric-subs",
    valueFmt: (v) => fmtNum(v),
    axisFmt: fmtCompactInt,
  },
  {
    key: "new_paid_subscriptions",
    label: "Платные подписки",
    short: "Платные",
    color: "#EC4899",
    fillId: "metric-paidsubs",
    valueFmt: (v) => fmtNum(v),
    axisFmt: fmtCompactInt,
  },
] as const;

const RANGE_OPTIONS = [7, 30, 90, 180] as const;
type RangeDays = (typeof RANGE_OPTIONS)[number];

// ─ Live event ringbuffer ─────────────────────────────────────────────

interface LiveEntry {
  id: number;
  kind: BusEvent["type"];
  title: string;
  subtitle?: string;
  at: number;
}

let liveCounter = 0;

// ─ Main page ────────────────────────────────────────────────────────
//
// Светлая «AURA-style» главная: чистый фон, гигантская типографика
// для главной метрики, тонкие area-графики с soft-градиентом, мягкие
// shadow'ы. Остальные страницы пока в тёмной теме — этот компонент
// локально оборачивает себя в `light-canvas`, не трогая глобальные
// токены. Когда раскатим на остальные — заменим в tailwind.config.

export function Dashboard() {
  const qc = useQueryClient();

  const overview = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: endpoints.statsOverview,
    refetchInterval: 60_000,
  });
  const revenue = useQuery({
    queryKey: ["stats", "revenue"],
    queryFn: endpoints.statsRevenue,
    refetchInterval: 60_000,
  });
  // "Сегодня (МСК)" — calendar day from 00:00 to 23:59 Europe/Moscow.
  // Resets daily at MSK midnight via queryKey rotation.
  const [todayKey, setTodayKey] = useState(mskDayKey());
  useEffect(() => {
    const t = setInterval(() => {
      const k = mskDayKey();
      setTodayKey((prev) => (prev === k ? prev : k));
    }, 30_000);
    return () => clearInterval(t);
  }, []);
  const todaySince = mskTodayStartIso();
  const today = useQuery({
    queryKey: ["stats", "period", "msk-today", todayKey],
    queryFn: () => endpoints.statsPeriodSince(todaySince),
    refetchInterval: 60_000,
  });
  const today24Revenue = useQuery({
    queryKey: ["payments", "revenue", "msk-today", todayKey],
    queryFn: () => endpoints.paymentsRevenueSince(todaySince),
    refetchInterval: 60_000,
  });
  // Daily chart controls. Range — горизонт по дням (7/30/90/180).
  // Metric — какую серию рисовать (доход, юзеры, платежи, подписки).
  // Запрос един для всех метрик — переключатель только меняет ключ
  // в данных, без повторного fetch'а.
  const [days, setDays] = useState<7 | 30 | 90 | 180>(30);
  const [metric, setMetric] = useState<MetricKey>("revenue_rubles");
  const daily = useQuery({
    queryKey: ["stats", "daily", days],
    queryFn: () => endpoints.statsDaily(days),
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });
  // Segments — same queryKey as BroadcastCreate, кеш общий.
  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: endpoints.broadcastSegments,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });

  const [live, setLive] = useState<LiveEntry[]>([]);
  useEventStream((e) => {
    if (e.type === "ping") return;
    let entry: LiveEntry | null = null;
    if (e.type === "user:registered") {
      entry = {
        id: ++liveCounter,
        kind: e.type,
        title: "Новый пользователь",
        subtitle: `tg:${e.telegram_id}${e.username ? ` · @${e.username}` : ""}`,
        at: Date.now(),
      };
    } else if (e.type === "payment:approved") {
      const until = e.expires_at
        ? new Date(e.expires_at).toLocaleDateString("ru-RU")
        : "—";
      entry = {
        id: ++liveCounter,
        kind: e.type,
        title: e.is_renewal ? "Продление подписки" : "Новая подписка",
        subtitle: `tg:${e.telegram_id ?? "—"} · до ${until}`,
        at: Date.now(),
      };
    } else if (e.type === "admin:grant") {
      entry = {
        id: ++liveCounter,
        kind: e.type,
        title: "Админ выдал доступ",
        subtitle: `tg:${e.telegram_id} · +${e.days} дн (${e.tariff})`,
        at: Date.now(),
      };
    } else if (e.type === "admin:revoke") {
      entry = {
        id: ++liveCounter,
        kind: e.type,
        title: "Доступ отозван",
        subtitle: `tg:${e.telegram_id}`,
        at: Date.now(),
      };
    } else if (typeof e.type === "string" && e.type.startsWith("admin:")) {
      entry = {
        id: ++liveCounter,
        kind: e.type,
        title: e.type.replace("admin:", "Админ: "),
        subtitle:
          typeof e.telegram_id === "number" ? `tg:${e.telegram_id}` : undefined,
        at: Date.now(),
      };
    }
    if (entry) setLive((prev) => [entry!, ...prev].slice(0, 25));
    qc.invalidateQueries({ queryKey: ["stats"] });
  });

  // Дельта: вторая половина выбранного окна vs первая половина.
  // Работает для 7/30/90 — даёт «как изменилось за половину периода».
  const revenueDelta = useMemo(() => {
    const s = daily.data?.series ?? [];
    if (s.length < 4) return null;
    const half = Math.floor(s.length / 2);
    const prev = s.slice(0, half).reduce((a, r) => a + r.revenue_rubles, 0);
    const last = s.slice(-half).reduce((a, r) => a + r.revenue_rubles, 0);
    if (prev === 0) return null;
    return ((last - prev) / prev) * 100;
  }, [daily.data]);

  // Конверсия: started → triallers → payers. Берём из overview/revenue.
  const totalUsers = asNum(overview.data?.total_users);
  const activePaid = asNum(
    overview.data?.active_paid_subscriptions ?? overview.data?.active_subscriptions,
  );
  const payingUsers = asNum(revenue.data?.paying_users);

  return (
    <div className="text-fg">
      <div className="mx-auto max-w-[1400px] space-y-6">
        {/* Header */}
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-slate-400">
              Atlas Secure · overview
            </div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900 md:text-[40px] md:leading-[1.05]">
              Welcome back
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              Сводка по боту обновляется в реальном времени.
            </p>
          </div>
          <Link
            to="/broadcasts/new"
            className="inline-flex items-center gap-2 rounded-full bg-slate-900 px-5 py-2.5 text-sm font-medium text-white shadow-[0_8px_20px_-8px_rgba(15,23,42,0.45)] transition hover:bg-slate-800"
          >
            <Megaphone className="h-3.5 w-3.5" /> Новая рассылка
          </Link>
        </header>

        {/* Hero — revenue + active + paying */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <HeroCard
            label="Total revenue"
            value={fmtRub(revenue.data?.total_revenue_rubles)}
            subline={
              revenueDelta != null
                ? {
                    text: `${revenueDelta >= 0 ? "+" : ""}${revenueDelta.toFixed(1)}% vs prev 30d`,
                    positive: revenueDelta >= 0,
                  }
                : revenue.data
                ? { text: `ARPU ${fmtRub(revenue.data.arpu_rubles)}`, positive: true }
                : null
            }
            loading={revenue.isLoading || daily.isLoading}
            chart={
              <RevenueChart
                data={daily.data?.series ?? []}
                loading={daily.isLoading}
              />
            }
            className="lg:col-span-2"
          />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-1">
            <SmallMetric
              label="Active subs"
              value={fmtNum(
                asNum(
                  overview.data?.active_paid_subscriptions ??
                    overview.data?.active_subscriptions,
                ),
              )}
              hint={
                overview.data?.active_subscriptions != null &&
                overview.data?.active_paid_subscriptions != null &&
                overview.data.active_subscriptions !==
                  overview.data.active_paid_subscriptions
                  ? `с триалами ${fmtNum(asNum(overview.data.active_subscriptions))}`
                  : undefined
              }
              loading={overview.isLoading}
            />
            <SmallMetric
              label="Paying users"
              value={fmtNum(revenue.data?.paying_users)}
              hint={
                revenue.data ? `LTV ${fmtRub(revenue.data.avg_ltv_rubles)}` : undefined
              }
              loading={revenue.isLoading}
            />
          </div>
        </section>

        {/* Daily breakdown + KPI */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <SurfaceCard className="lg:col-span-2">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <SurfaceHeader
                eyebrow={`Daily · past ${days} days`}
                title={METRICS.find((m) => m.key === metric)?.label ?? "Daily"}
              />
              <div className="flex shrink-0 items-center gap-2">
                <RangePill value={days} onChange={setDays} />
              </div>
            </div>
            <MetricSwitcher value={metric} onChange={setMetric} />
            <DailyMetricChart
              data={daily.data?.series ?? []}
              loading={daily.isLoading}
              metric={metric}
            />
          </SurfaceCard>

          <SurfaceCard>
            <SurfaceHeader eyebrow="Бизнес-метрики" title="KPI" icon={<TrendingUp className="h-3.5 w-3.5 text-slate-400" />} />
            <dl className="mt-4 space-y-3.5">
              <KpiRow
                label="Approval rate"
                value={`${fmtNum(asNum(overview.data?.business_metrics?.approval_rate_percent))}%`}
              />
              <KpiRow
                label="Средний срок жизни"
                value={`${fmtNum(asNum(overview.data?.business_metrics?.avg_subscription_lifetime_days))} дн`}
              />
              <KpiRow
                label="Продлений на юзера"
                value={fmtNum(asNum(overview.data?.business_metrics?.avg_renewals_per_user))}
              />
              <KpiRow
                label="Время апрува"
                value={fmtSeconds(asNum(overview.data?.business_metrics?.avg_payment_approval_time_seconds))}
              />
              <KpiRow
                label="Всего юзеров"
                value={fmtNum(asNum(overview.data?.total_users))}
              />
            </dl>
          </SurfaceCard>
        </section>

        {/* Финансы — 6 ключевых денежных метрик */}
        <SurfaceCard>
          <SurfaceHeader
            eyebrow="Финансы"
            title="Денежные метрики"
            sub="всё время · обновляется каждую минуту"
          />
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            <KpiCard
              label="Доход всего"
              value={fmtRub(revenue.data?.total_revenue_rubles)}
              loading={revenue.isLoading}
              accent
            />
            <KpiCard
              label="ARPU"
              value={fmtRub(revenue.data?.arpu_rubles)}
              sub="на юзера"
              loading={revenue.isLoading}
            />
            <KpiCard
              label="LTV"
              value={fmtRub(revenue.data?.avg_ltv_rubles)}
              sub="средний"
              loading={revenue.isLoading}
            />
            <KpiCard
              label="Средний чек"
              value={fmtRub(today24Revenue.data?.avg_check_rubles)}
              sub="сегодня"
              loading={today24Revenue.isLoading}
            />
            <KpiCard
              label="Доход сегодня"
              value={fmtRub(today24Revenue.data?.revenue_rubles)}
              sub={`${fmtNum(today24Revenue.data?.payments_count)} платежей`}
              loading={today24Revenue.isLoading}
            />
            <KpiCard
              label="Approval rate"
              value={`${fmtNum(asNum(overview.data?.business_metrics?.approval_rate_percent))}%`}
              sub={`${fmtSeconds(asNum(overview.data?.business_metrics?.avg_payment_approval_time_seconds))} среднее`}
              loading={overview.isLoading}
            />
          </div>
        </SurfaceCard>

        {/* Подписки — health */}
        <SurfaceCard>
          <SurfaceHeader
            eyebrow="Подписки"
            title="Жизнь и health"
            sub="renewal, lifetime, retention"
          />
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            <KpiCard
              label="Активных (платные)"
              value={fmtNum(activePaid)}
              loading={overview.isLoading}
              accent
            />
            <KpiCard
              label="Активных с триалами"
              value={fmtNum(asNum(overview.data?.active_subscriptions))}
              loading={overview.isLoading}
            />
            <KpiCard
              label="Платящих юзеров"
              value={fmtNum(payingUsers)}
              loading={revenue.isLoading}
            />
            <KpiCard
              label="Средний lifetime"
              value={`${fmtNum(asNum(overview.data?.business_metrics?.avg_subscription_lifetime_days))} дн`}
              loading={overview.isLoading}
            />
            <KpiCard
              label="Продлений/юзер"
              value={fmtNum(asNum(overview.data?.business_metrics?.avg_renewals_per_user))}
              loading={overview.isLoading}
            />
            <KpiCard
              label="Conversion paid"
              value={
                totalUsers && totalUsers > 0 && payingUsers != null
                  ? `${((payingUsers / totalUsers) * 100).toFixed(2)}%`
                  : "—"
              }
              sub="платящие / всего"
              loading={overview.isLoading || revenue.isLoading}
            />
          </div>
        </SurfaceCard>

        {/* Conversion funnel — простой 3-этапный визуал */}
        <SurfaceCard>
          <SurfaceHeader
            eyebrow="Воронка"
            title="Конверсия пользователей"
            sub="всё время · база → платящие"
          />
          <ConversionFunnel
            total={totalUsers}
            active={activePaid}
            paying={payingUsers}
            loading={overview.isLoading || revenue.isLoading}
          />
        </SurfaceCard>

        {/* Today MSK */}
        <SurfaceCard>
          <SurfaceHeader
            eyebrow="Сегодня · МСК"
            title="Активность"
            sub="с 00:00 до 23:59 по Москве · сброс ежедневно"
          />
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Tile
              label="Платежей"
              value={fmtNum(today24Revenue.data?.payments_count)}
            />
            <Tile
              label="Доход"
              value={fmtRub(today24Revenue.data?.revenue_rubles)}
              tone="accent"
            />
            <Tile
              label="Средний чек"
              value={fmtRub(today24Revenue.data?.avg_check_rubles)}
            />
            <Tile
              label="Новых юзеров"
              value={fmtNum(asNum(today.data?.new_users))}
            />
            <Tile
              label="Новых подписок"
              value={fmtNum(asNum(today.data?.new_subscriptions))}
            />
          </div>
        </SurfaceCard>

        {/* Segments */}
        <SegmentsCard
          loading={segments.isLoading}
          error={segments.isError}
          data={segments.data}
        />

        {/* Live */}
        <SurfaceCard>
          <SurfaceHeader
            eyebrow="Live"
            title="Поток событий"
            icon={<Activity className="h-3.5 w-3.5 text-slate-400" />}
          />
          {live.length === 0 ? (
            <div className="mt-2">
              <EmptyState
                icon={Sparkles}
                title="Пока тихо"
                description="События появятся здесь по мере поступления — новые юзеры, платежи, действия админа."
              />
            </div>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100">
              {live.map((e) => (
                <li
                  key={e.id}
                  className="flex items-center gap-3 py-3 text-sm animate-slide-up"
                >
                  <span
                    className={
                      e.kind === "payment:approved"
                        ? "h-2 w-2 shrink-0 rounded-full bg-emerald-500"
                        : e.kind === "user:registered"
                        ? "h-2 w-2 shrink-0 rounded-full bg-sky-500"
                        : e.kind === "admin:revoke"
                        ? "h-2 w-2 shrink-0 rounded-full bg-rose-500"
                        : "h-2 w-2 shrink-0 rounded-full bg-amber-500"
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-slate-900">
                      {e.title}
                    </div>
                    {e.subtitle && (
                      <div className="truncate text-xs text-slate-500">
                        {e.subtitle}
                      </div>
                    )}
                  </div>
                  <div className="shrink-0 text-xs text-slate-400">
                    {fmtRelative(new Date(e.at).toISOString())}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>
      </div>
    </div>
  );
}

// ─ Cards / primitives ────────────────────────────────────────────────

function SurfaceCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_16px_-8px_rgba(15,23,42,0.06)] ${className}`}
    >
      {children}
    </section>
  );
}

function SurfaceHeader({
  eyebrow,
  title,
  sub,
  icon,
}: {
  eyebrow: string;
  title: string;
  sub?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-slate-400">
          {eyebrow}
        </div>
        <h2 className="mt-1 text-base font-semibold text-slate-900">{title}</h2>
        {sub && <div className="mt-0.5 text-[11px] text-slate-400">{sub}</div>}
      </div>
      {icon}
    </div>
  );
}

function HeroCard({
  label,
  value,
  subline,
  loading,
  chart,
  className = "",
}: {
  label: string;
  value: string;
  subline: { text: string; positive: boolean } | null;
  loading: boolean;
  chart?: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_16px_-8px_rgba(15,23,42,0.06)] ${className}`}
    >
      <div className="relative z-10">
        <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-slate-400">
          {label}
        </div>
        <div className="mt-2 text-[40px] font-semibold leading-none tracking-tight text-slate-900 tabular-nums md:text-[56px]">
          {loading ? "…" : value}
        </div>
        {subline && (
          <div
            className={
              "mt-2 inline-flex items-center gap-1 text-xs font-medium " +
              (subline.positive ? "text-emerald-600" : "text-rose-500")
            }
          >
            {subline.positive ? (
              <ArrowUpRight className="h-3.5 w-3.5" />
            ) : (
              <ArrowDownRight className="h-3.5 w-3.5" />
            )}
            {subline.text}
          </div>
        )}
      </div>
      {chart && <div className="mt-2 h-32 md:h-40">{chart}</div>}
    </section>
  );
}

function SmallMetric({
  label,
  value,
  hint,
  loading,
}: {
  label: string;
  value: string;
  hint?: string;
  loading: boolean;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-slate-400">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-900 tabular-nums md:text-3xl">
        {loading ? "…" : value}
      </div>
      {hint && <div className="mt-1 text-[11px] text-slate-400">{hint}</div>}
    </div>
  );
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "accent";
}) {
  const valueClass =
    tone === "accent"
      ? "text-sky-600"
      : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-200/70 bg-slate-50/60 px-4 py-3">
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-slate-400">
        {label}
      </div>
      <div className={`mt-1 text-xl font-semibold tracking-tight tabular-nums md:text-2xl ${valueClass}`}>
        {value}
      </div>
    </div>
  );
}

function KpiRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="font-semibold text-slate-900 tabular-nums">{value}</span>
    </div>
  );
}

// KpiCard — компактная stat-карточка с лейблом/значением/опц.под-текстом.
// Используется в финансах и подписках. accent=true → подсвечивает
// значение sky-цветом (когда метрика «главная» в группе).
function KpiCard({
  label,
  value,
  sub,
  loading,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  loading?: boolean;
  accent?: boolean;
}) {
  return (
    <div className="group rounded-xl border border-slate-200/70 bg-slate-50/40 p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:border-slate-300 hover:bg-white hover:shadow-[0_4px_14px_-6px_rgba(15,23,42,0.08)]">
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-slate-400">
        {label}
      </div>
      <div
        className={
          "mt-1 text-lg font-semibold tabular-nums tracking-tight md:text-xl " +
          (accent ? "text-sky-600" : "text-slate-900")
        }
      >
        {loading ? "…" : value}
      </div>
      {sub && (
        <div className="mt-0.5 truncate text-[11px] text-slate-500">{sub}</div>
      )}
    </div>
  );
}

// SegPill — переиспользуемый segmented control с animated indicator.
// indicator слайдится между опциями (translate-x + width), сама
// активная опция получает белый текст. Достижение «плавности» —
// одна абсолютно-позиционированная пилюля, без re-mount.
function SegPill<T extends string | number>({
  value,
  options,
  onChange,
  fmt = (v) => String(v),
}: {
  value: T;
  options: ReadonlyArray<T>;
  onChange: (v: T) => void;
  fmt?: (v: T) => string;
}) {
  const idx = Math.max(0, options.indexOf(value));
  const total = options.length;
  return (
    <div className="relative inline-flex shrink-0 items-stretch rounded-full border border-border bg-bg-card p-0.5 text-[11px] font-medium shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div
        aria-hidden
        className="absolute inset-y-0.5 rounded-full bg-fg shadow-cta transition-[transform,width] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
        style={{
          width: `calc((100% - 4px) / ${total})`,
          transform: `translateX(calc(${idx} * 100%))`,
          left: 2,
        }}
      />
      {options.map((o) => (
        <button
          key={String(o)}
          type="button"
          onClick={() => onChange(o)}
          className={
            "relative z-10 flex-1 rounded-full px-2.5 py-1 transition-colors duration-200 " +
            (o === value ? "text-bg-card" : "text-fg-muted hover:text-fg")
          }
        >
          {fmt(o)}
        </button>
      ))}
    </div>
  );
}

function RangePill({
  value,
  onChange,
}: {
  value: RangeDays;
  onChange: (v: RangeDays) => void;
}) {
  return (
    <SegPill
      value={value}
      options={RANGE_OPTIONS}
      onChange={onChange}
      fmt={(v) => `${v}д`}
    />
  );
}

// Метрика — горизонтальный скроллящийся ряд chip'ов с активным
// state. Использует тот же animated-indicator, но шире — для метрик
// делаем chip-row, не сегментированный pill (5 опций трудно влезают
// в пилюлю на мобайл).
function MetricSwitcher({
  value,
  onChange,
}: {
  value: MetricKey;
  onChange: (k: MetricKey) => void;
}) {
  return (
    <div className="mt-4 -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {METRICS.map((m) => {
        const active = m.key === value;
        return (
          <button
            key={m.key}
            type="button"
            onClick={() => onChange(m.key)}
            className={
              "group inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-all duration-200 " +
              (active
                ? "border-transparent bg-fg text-bg-card shadow-cta"
                : "border-border bg-bg-card text-fg-muted hover:border-fg-subtle/40 hover:text-fg")
            }
          >
            <span
              className="h-2 w-2 rounded-full transition-transform duration-200 group-hover:scale-110"
              style={{ background: active ? m.color : m.color + "BF" }}
            />
            {m.short}
          </button>
        );
      })}
    </div>
  );
}

function ConversionFunnel({
  total,
  active,
  paying,
  loading,
}: {
  total: number | undefined;
  active: number | undefined;
  paying: number | undefined;
  loading: boolean;
}) {
  const t = total ?? 0;
  const a = active ?? 0;
  const p = paying ?? 0;
  const aPct = t > 0 ? (a / t) * 100 : 0;
  const pPct = t > 0 ? (p / t) * 100 : 0;
  const aRel = t > 0 ? Math.max(2, (a / t) * 100) : 0;
  const pRel = t > 0 ? Math.max(2, (p / t) * 100) : 0;
  return (
    <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
      <FunnelStage
        label="Всего пользователей"
        value={loading ? "…" : fmtNum(t)}
        sub="нажали /start"
        pct={100}
        color="bg-sky-500"
      />
      <FunnelStage
        label="Активные подписки"
        value={loading ? "…" : fmtNum(a)}
        sub={t > 0 ? `${aPct.toFixed(1)}% от всех` : undefined}
        pct={aRel}
        color="bg-emerald-500"
      />
      <FunnelStage
        label="Платящие"
        value={loading ? "…" : fmtNum(p)}
        sub={t > 0 ? `${pPct.toFixed(1)}% от всех` : undefined}
        pct={pRel}
        color="bg-violet-500"
      />
    </div>
  );
}

function FunnelStage({
  label,
  value,
  sub,
  pct,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  pct: number;
  color: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200/70 bg-slate-50/40 p-4">
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">
        {value}
      </div>
      {sub && (
        <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>
      )}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full ${color} transition-[width] duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ─ Charts ───────────────────────────────────────────────────────────

const fmtShortDate = (iso: string) => {
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
};

function RevenueChart({
  data,
  loading,
}: {
  data: Array<{ date: string; revenue_rubles: number }>;
  loading: boolean;
}) {
  if (loading || data.length === 0) {
    return (
      <div className="flex h-full items-end gap-1">
        {Array.from({ length: 30 }).map((_, i) => (
          <div
            key={i}
            className="flex-1 rounded-sm bg-slate-100"
            style={{ height: `${20 + Math.random() * 60}%` }}
          />
        ))}
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 6, right: 4, left: 4, bottom: 4 }}>
        <defs>
          <linearGradient id="revGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0EA5E9" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#0EA5E9" stopOpacity={0} />
          </linearGradient>
        </defs>
        <Tooltip content={<ChartTooltip valueFmt={fmtRub} label="Доход" />} cursor={{ stroke: "#CBD5E1", strokeDasharray: "3 3" }} />
        <Area
          type="monotone"
          dataKey="revenue_rubles"
          stroke="#0EA5E9"
          strokeWidth={1.75}
          fill="url(#revGrad)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// DailyMetricChart — Area по выбранной метрике с плавной сменой
// формы (recharts animationDuration 500ms ease-out). При смене
// горизонта recharts перерисовывает плавно сам.
function DailyMetricChart({
  data,
  loading,
  metric,
}: {
  data: Array<Record<string, number | string>>;
  loading: boolean;
  metric: MetricKey;
}) {
  const def = METRICS.find((m) => m.key === metric) ?? METRICS[0];
  const total = data.reduce((a, r) => a + (Number(r[metric]) || 0), 0);

  if (loading || data.length === 0) {
    return (
      <div className="mt-4 h-56">
        <SkeletonBars />
      </div>
    );
  }
  return (
    <div className="mt-4 space-y-2">
      <div className="text-2xl font-semibold tabular-nums tracking-tight text-fg md:text-3xl">
        {def.valueFmt(total)}
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 4, left: 4, bottom: 4 }}>
            <defs>
              <linearGradient id={def.fillId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={def.color} stopOpacity={0.32} />
                <stop offset="100%" stopColor={def.color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#F1F5F9" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: "#94A3B8", fontSize: 10 }}
              tickFormatter={(v) => fmtShortDate(String(v))}
              tickLine={false}
              axisLine={{ stroke: "#E2E8F0" }}
              minTickGap={36}
            />
            <YAxis
              tick={{ fill: "#94A3B8", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={48}
              tickFormatter={def.axisFmt}
            />
            <Tooltip
              cursor={{ stroke: "#CBD5E1", strokeDasharray: "3 3" }}
              content={<ChartTooltip valueFmt={def.valueFmt} label={def.label} />}
            />
            <Area
              type="monotone"
              dataKey={metric}
              stroke={def.color}
              strokeWidth={1.75}
              fill={`url(#${def.fillId})`}
              animationDuration={500}
              animationEasing="ease-out"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function SkeletonBars() {
  return (
    <div className="flex h-full items-end gap-1">
      {Array.from({ length: 30 }).map((_, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm bg-slate-100"
          style={{ height: `${15 + ((i * 7) % 60)}%` }}
        />
      ))}
    </div>
  );
}

interface ChartTooltipProps {
  active?: boolean;
  // recharts передаёт payload — мы лениво типизируем как unknown[].
  payload?: Array<{ name?: string; value?: number; dataKey?: string; color?: string }>;
  label?: string | number;
  valueFmt?: (v: number) => string;
}

function ChartTooltip({ active, payload, label, valueFmt }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const fmt = valueFmt ?? ((v: number) => fmtNum(v));
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-md">
      <div className="text-[10px] uppercase tracking-wider text-slate-400">
        {typeof label === "string" ? fmtShortDate(label) : label}
      </div>
      {payload.map((p, i) => (
        <div key={i} className="mt-1 flex items-center gap-2 text-sm">
          <span
            className="h-2 w-2 rounded-full"
            style={{ background: p.color ?? "#0EA5E9" }}
          />
          <span className="text-slate-500">{labelForKey(p.dataKey, p.name)}</span>
          <span className="font-semibold tabular-nums text-slate-900">
            {p.value != null ? fmt(p.value) : "—"}
          </span>
        </div>
      ))}
    </div>
  );
}

function labelForKey(key?: string, fallback?: string): string {
  if (key === "revenue_rubles") return "Доход";
  if (key === "new_users") return "Юзеры";
  if (key === "payments_count") return "Платежи";
  return fallback ?? key ?? "";
}

// ─ Segments card (kept) ──────────────────────────────────────────────

const SEGMENT_GROUPS: { title: string; keys: string[] }[] = [
  {
    title: "База",
    keys: [
      "all_users",
      "active_subscriptions",
      "no_subscription",
      "no_remnawave",
      "started_7d_cold",
    ],
  },
  {
    title: "Истёкли (любая подписка)",
    keys: ["expired_1d", "expired_2d", "expired_3d"],
  },
  {
    title: "Триал-воронка",
    keys: [
      "trial_ends_in_1d",
      "trial_expired_6h",
      "trial_expired_1d",
      "trial_expired_2d",
      "trial_expired_3d",
    ],
  },
  {
    title: "Реактивация платных",
    keys: ["paid_expired_1d", "paid_expired_30d", "paid_lapsed_any"],
  },
];

function SegmentsCard({
  loading,
  error,
  data,
}: {
  loading: boolean;
  error: boolean;
  data: Array<{ key: string; label: string; count: number }> | undefined;
}) {
  const byKey = new Map(data?.map((s) => [s.key, s]));
  return (
    <SurfaceCard>
      <SurfaceHeader
        eyebrow="Сегменты"
        title="Аудитории для рассылок"
        sub="обновляется каждые 5 минут · клик → создать рассылку"
      />
      {error ? (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          Не удалось загрузить сегменты.
        </div>
      ) : (
        <div className="mt-4 space-y-5">
          {SEGMENT_GROUPS.map((group) => (
            <div key={group.title}>
              <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.12em] text-slate-400">
                {group.title}
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {group.keys.map((k) => {
                  const s = byKey.get(k);
                  return (
                    <SegmentRow
                      key={k}
                      label={s?.label ?? k}
                      count={s?.count}
                      loading={loading}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </SurfaceCard>
  );
}

function SegmentRow({
  label,
  count,
  loading,
}: {
  label: string;
  count: number | undefined;
  loading: boolean;
}) {
  const isMissing = count == null;
  const isEmpty = count === 0;
  return (
    <Link
      to="/broadcasts/new"
      className="group flex items-center justify-between gap-3 rounded-xl border border-slate-200/70 bg-slate-50/40 px-3 py-2.5 text-sm transition hover:border-sky-300 hover:bg-sky-50/60"
    >
      <span className="truncate text-slate-600 group-hover:text-slate-900">
        {label}
      </span>
      <span
        className={
          loading || isMissing
            ? "text-xs text-slate-400"
            : isEmpty
            ? "shrink-0 rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-400 tabular-nums"
            : "shrink-0 rounded-md bg-sky-100/80 px-2 py-0.5 text-xs font-semibold text-sky-700 tabular-nums"
        }
      >
        {loading ? "…" : isMissing ? "—" : fmtNum(count)}
      </span>
    </Link>
  );
}

// ─ utils ────────────────────────────────────────────────────────────

function asNum(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

function fmtSeconds(s: number | undefined): string {
  if (s == null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${Math.round(s)}с`;
  if (s < 3600) return `${Math.round(s / 60)}мин`;
  return `${Math.round((s / 3600) * 10) / 10}ч`;
}
