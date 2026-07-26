import { useEffect, useMemo, useRef, useState } from "react";
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
import { Collapsible } from "@/components/Collapsible";
import { useEventStream, type BusEvent } from "@/lib/ws";
import { LivePaymentTicker } from "@/components/LivePaymentTicker";
import {
  fmtNum,
  fmtRub,
  fmtRelative,
  mskDayKey,
  mskTodayStartIso,
} from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";
import { ReconciliationSection } from "@/components/ReconciliationSection";

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
    color: "#0F1720",
    fillId: "metric-revenue",
    valueFmt: (v) => fmtRub(v),
    axisFmt: fmtCompactRub,
  },
  {
    key: "new_users",
    label: "Новые юзеры",
    short: "Юзеры",
    color: "#2563EB",
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
    refetchInterval: 20_000,
  });
  const revenue = useQuery({
    queryKey: ["stats", "revenue"],
    queryFn: endpoints.statsRevenue,
    refetchInterval: 20_000,
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
    refetchInterval: 20_000,
  });
  const today24Revenue = useQuery({
    queryKey: ["payments", "revenue", "msk-today", todayKey],
    queryFn: () => endpoints.paymentsRevenueSince(todaySince),
    refetchInterval: 20_000,
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
    refetchInterval: 90_000,
    staleTime: 60_000,
  });
  // Segments — same queryKey as BroadcastCreate, кеш общий.
  const segments = useQuery({
    queryKey: ["broadcasts", "segments"],
    queryFn: endpoints.broadcastSegments,
    refetchInterval: 90_000,
    staleTime: 60_000,
  });

  // Расширенная аналитика — реферальная, тарифы, провайдеры платежей.
  const referrals = useQuery({
    queryKey: ["referrals", "overall"],
    queryFn: endpoints.referralsOverall,
    refetchInterval: 90_000,
    staleTime: 60_000,
  });
  const topReferrers = useQuery({
    queryKey: ["referrals", "top", "revenue", 5],
    queryFn: () =>
      endpoints.referralsTop({
        sort_by: "total_revenue",
        sort_order: "DESC",
        limit: 5,
        offset: 0,
      }),
    refetchInterval: 90_000,
    staleTime: 60_000,
  });
  const breakdown = useQuery({
    queryKey: ["stats", "breakdown"],
    queryFn: endpoints.statsBreakdown,
    refetchInterval: 90_000,
    staleTime: 60_000,
  });
  // Провайдеры платежей: переключатель окна 24h / 7d / 30d (8760h max).
  const [providerHours, setProviderHours] = useState<24 | 168 | 720>(720);
  const providers = useQuery({
    queryKey: ["payments", "by-provider", providerHours],
    queryFn: () => endpoints.paymentsByProvider(providerHours),
    refetchInterval: 90_000,
    staleTime: 60_000,
  });
  // Payments breakdown (тип / провайдер / тариф / apple-номинал) —
  // отдельный переключатель окна.
  const [breakdownHours, setBreakdownHours] = useState<24 | 168 | 720>(24);
  const paymentsBreakdown = useQuery({
    queryKey: ["payments", "breakdown", breakdownHours],
    queryFn: () => endpoints.paymentsBreakdown(breakdownHours),
    refetchInterval: 120_000,
    staleTime: 60_000,
  });
  // Hourly breakdown: окно 1д / 7д / 30д, тот же metric switcher.
  const [hourlyDays, setHourlyDays] = useState<1 | 7 | 30>(7);
  const [hourlyMetric, setHourlyMetric] = useState<MetricKey>("payments_count");
  const hourly = useQuery({
    queryKey: ["stats", "hourly", hourlyDays],
    queryFn: () => endpoints.statsHourly(hourlyDays),
    refetchInterval: 90_000,
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
      <div className="stagger-children mx-auto max-w-[1400px] space-y-6">
        {/* Header */}
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
              Atlas Secure · overview
            </div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-fg md:text-[40px] md:leading-[1.05]">
              Welcome back
            </h1>
            <p className="mt-2 text-sm text-fg-muted">
              Сводка по боту обновляется в реальном времени.
            </p>
          </div>
          <Link
            to="/broadcasts/new"
            className="group inline-flex items-center gap-2 rounded-full bg-accent px-5 py-2.5 text-sm font-semibold text-bg shadow-glow transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:bg-accent-hover hover:shadow-[0_14px_28px_-10px_rgba(245,245,245,0.35)] active:translate-y-0"
          >
            <Megaphone className="h-3.5 w-3.5 transition-transform duration-300 group-hover:rotate-[-8deg]" />
            Новая рассылка
          </Link>
        </header>

        {/* LIVE ticker — real-time платежи бегущей строкой. */}
        <LivePaymentTicker />

        {/* Hero — revenue + active + paying */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <HeroCard
            label="Total revenue"
            rawValue={revenue.data?.total_revenue_rubles}
            fmt={fmtRub}
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
              rawValue={asNum(
                overview.data?.active_paid_subscriptions ??
                  overview.data?.active_subscriptions,
              )}
              fmt={fmtNum}
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
              rawValue={revenue.data?.paying_users}
              fmt={fmtNum}
              hint={
                revenue.data ? `LTV ${fmtRub(revenue.data.avg_ltv_rubles)}` : undefined
              }
              loading={revenue.isLoading}
            />
          </div>
        </section>

        {/* Сегодня · МСК — оперативная сводка сразу под hero */}
        <TodayBar
          revenue={today24Revenue.data?.revenue_rubles}
          payments={today24Revenue.data?.payments_count}
          avgCheck={today24Revenue.data?.avg_check_rubles}
          newUsers={asNum(today.data?.new_users)}
          trialActivated={asNum(today.data?.trial_activated)}
          newSubscriptions={asNum(today.data?.new_subscriptions)}
          loading={today24Revenue.isLoading || today.isLoading}
        />

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
            <SurfaceHeader eyebrow="Бизнес-метрики" title="KPI" icon={<TrendingUp className="h-3.5 w-3.5 text-fg-subtle" />} />
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

        {/* Подписки — health. Collapsible: свернутая по умолчанию, если
            админу нужны быстрые KPI — hero + Финансы выше уже дают всё. */}
        <Collapsible
          title="Подписки · health"
          subtitle="renewal, lifetime, retention — расширенные метрики"
          remember="dash-subs-health"
        >
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
        </Collapsible>

        {/* Маркетинг: реферальная программа. Collapsed по умолчанию. */}
        <Collapsible
          title="Маркетинг · реферальная программа"
          subtitle="доход от рефералов, топ-амбассадоры, breakdown тарифов и провайдеров"
          remember="dash-marketing"
        >
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <SurfaceCard className="lg:col-span-1">
            <SurfaceHeader
              eyebrow="Реферальная программа"
              title="Маркетинг"
              sub="доход от приглашений и выплаченный кэшбэк"
            />
            <ReferralBlock
              data={referrals.data}
              loading={referrals.isLoading}
            />
          </SurfaceCard>
          <SurfaceCard className="lg:col-span-2">
            <SurfaceHeader
              eyebrow="Топ-5 партнёров"
              title="По выручке от приглашений"
              sub="клик по строке — в раздел Referrals"
            />
            <TopReferrersList
              data={topReferrers.data}
              loading={topReferrers.isLoading}
            />
          </SurfaceCard>
        </section>

        {/* Продукт: тарифы + провайдеры платежей */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <SurfaceCard className="lg:col-span-2">
            <SurfaceHeader
              eyebrow="Продукт"
              title="Тарифы и продажи"
              sub="за всё время · по категориям"
            />
            <TariffsBlock
              data={breakdown.data}
              loading={breakdown.isLoading}
            />
          </SurfaceCard>
          <SurfaceCard className="lg:col-span-1">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <SurfaceHeader
                eyebrow={`Провайдеры · ${providerHoursLabel(providerHours)}`}
                title="Платежи по каналам"
              />
              <SegPill<24 | 168 | 720>
                value={providerHours}
                options={[24, 168, 720]}
                onChange={setProviderHours}
                fmt={providerHoursLabel}
              />
            </div>
            <ProvidersBlock
              data={providers.data}
              loading={providers.isLoading}
            />
          </SurfaceCard>
        </section>
        </Collapsible>

        {/* Payments breakdown — что и как оплатили за последние 24ч/7д/30д. */}
        <Collapsible
          title="Оплаты · разбивка по продукту и провайдеру"
          subtitle="что купили + как заплатили + Apple-номиналы"
          remember="dash-payments-breakdown"
        >
          <PaymentsBreakdownCard
            data={paymentsBreakdown.data}
            loading={paymentsBreakdown.isLoading}
            hours={breakdownHours}
            onHoursChange={setBreakdownHours}
          />
        </Collapsible>

        {/* Активность по часам + funnel. Collapsed по умолчанию. */}
        <Collapsible
          title="Активность и воронка"
          subtitle="hourly-график покупок + conversion-funnel"
          remember="dash-activity"
        >
        <SurfaceCard>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <SurfaceHeader
              eyebrow={`Активность по часам · ${hourlyDays}д · МСК`}
              title="Когда юзеры покупают"
              sub="распределение по часам — найди пик и слабый момент"
            />
            <SegPill<1 | 7 | 30>
              value={hourlyDays}
              options={[1, 7, 30]}
              onChange={setHourlyDays}
              fmt={(v) => `${v}д`}
            />
          </div>
          <MetricSwitcher value={hourlyMetric} onChange={setHourlyMetric} />
          <HourlyChart
            data={hourly.data?.series ?? []}
            loading={hourly.isLoading}
            metric={hourlyMetric}
          />
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
        </Collapsible>

        {/* Сегменты рассылки. Collapsed по умолчанию —
            служебная информация, не критичная для «одного взгляда». */}
        <Collapsible
          title="Сегменты рассылки"
          subtitle="кто и сколько по каждой когорте (для планирования кампаний)"
          remember="dash-segments"
        >
        <SegmentsCard
          loading={segments.isLoading}
          error={segments.isError}
          data={segments.data}
        />
        </Collapsible>

        {/* Live-поток событий. Collapsed по умолчанию — live-ticker
            уже висит наверху; тут детальный лог для расследований. */}
        <Collapsible
          title="Live · поток событий"
          subtitle="детальный лог WS — регистрации, платежи, админ-действия"
          remember="dash-live"
        >
        <SurfaceCard>
          <SurfaceHeader
            eyebrow="Live"
            title="Поток событий"
            icon={<Activity className="h-3.5 w-3.5 text-fg-subtle" />}
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
            <ul className="mt-3 divide-y divide-border">
              {live.map((e) => (
                <li
                  key={e.id}
                  className="flex items-center gap-3 py-3 text-sm animate-slide-up"
                >
                  <span
                    className={
                      e.kind === "payment:approved"
                        ? "h-2 w-2 shrink-0 rounded-full bg-success"
                        : e.kind === "user:registered"
                        ? "h-2 w-2 shrink-0 rounded-full bg-accent"
                        : e.kind === "admin:revoke"
                        ? "h-2 w-2 shrink-0 rounded-full bg-danger"
                        : "h-2 w-2 shrink-0 rounded-full bg-warning"
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-fg">
                      {e.title}
                    </div>
                    {e.subtitle && (
                      <div className="truncate text-xs text-fg-muted">
                        {e.subtitle}
                      </div>
                    )}
                  </div>
                  <div className="shrink-0 text-xs text-fg-subtle">
                    {fmtRelative(new Date(e.at).toISOString())}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </SurfaceCard>
        </Collapsible>

        {/* «Сверка» — reconciliation of premium subscriptions vs. paid history.
            Lives at the very bottom of the main dashboard per product spec.
            Скрыто по умолчанию — используется редко, только когда есть подозрение
            на рассинхрон Remnawave ↔ БД. */}
        <Collapsible
          title="Сверка · Remnawave ↔ БД"
          subtitle="разница между panel-подписками и локальной историей"
          remember="dash-reconciliation"
        >
          <ReconciliationSection />
        </Collapsible>
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
      className={`rounded-2xl border border-border bg-bg-card p-5 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_4px_16px_-8px_rgba(0,0,0,0.06)] ${className}`}
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
        <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
          {eyebrow}
        </div>
        <h2 className="mt-1 text-base font-semibold text-fg">{title}</h2>
        {sub && <div className="mt-0.5 text-[11px] text-fg-subtle">{sub}</div>}
      </div>
      {icon}
    </div>
  );
}

function HeroCard({
  label,
  rawValue,
  fmt,
  subline,
  loading,
  chart,
  className = "",
}: {
  label: string;
  rawValue: number | undefined;
  fmt: (v: number) => string;
  subline: { text: string; positive: boolean } | null;
  loading: boolean;
  chart?: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`relative overflow-hidden rounded-2xl border border-border bg-bg-card p-5 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_4px_16px_-8px_rgba(0,0,0,0.06)] ${className}`}
    >
      {/* Subtle conic glow вращается медленно — премиум-ощущение, не
          отвлекает: opacity 0.5, прозрачный через mask. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-32 -top-32 h-72 w-72 opacity-50 animate-glow-rotate"
        style={{
          background:
            "conic-gradient(from 0deg, rgba(245,245,245,0.12), rgba(215,215,215,0.10), rgba(180,180,180,0.08), rgba(245,245,245,0.12))",
          filter: "blur(40px)",
        }}
      />
      <div className="relative z-10">
        <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
          {label}
        </div>
        <div className="mt-2 text-[40px] font-semibold leading-none tracking-tight text-fg tabular-nums md:text-[56px]">
          <AnimatedNum value={rawValue} fmt={fmt} loading={loading} />
        </div>
        {subline && (
          <div
            className={
              "mt-2 inline-flex items-center gap-1 text-xs font-medium " +
              (subline.positive ? "text-success" : "text-danger")
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
  rawValue,
  fmt,
  hint,
  loading,
}: {
  label: string;
  rawValue: number | undefined;
  fmt: (v: number) => string;
  hint?: string;
  loading: boolean;
}) {
  return (
    <div className="hover-lift rounded-2xl border border-border bg-bg-card p-5 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-fg tabular-nums md:text-3xl">
        <AnimatedNum value={rawValue} fmt={fmt} loading={loading} />
      </div>
      {hint && <div className="mt-1 text-[11px] text-fg-subtle">{hint}</div>}
    </div>
  );
}

// TodayBar — оперативная сводка сразу под Hero. 6 точечных метрик за
// день по МСК (00:00→23:59), на белом фоне в одной горизонтальной
// карточке с тонкими разделителями между ячейками. Иконки слева для
// мгновенной идентификации, цифры жирно с tabular-nums.
function TodayBar({
  revenue,
  payments,
  avgCheck,
  newUsers,
  trialActivated,
  newSubscriptions,
  loading,
}: {
  revenue: number | undefined;
  payments: number | undefined;
  avgCheck: number | undefined;
  newUsers: number | undefined;
  trialActivated: number | undefined;
  newSubscriptions: number | undefined;
  loading: boolean;
}) {
  // «Без триала» = новые юзеры, не активировавшие пробник.
  // Может быть отрицательным если activations > new_users (старые
  // юзеры активировали триал сегодня) — обрезаем до 0.
  const noTrial =
    typeof newUsers === "number" && typeof trialActivated === "number"
      ? Math.max(0, newUsers - trialActivated)
      : undefined;
  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-border/60 px-5 py-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-subtle">
            Сегодня · МСК
          </div>
          <div className="mt-0.5 text-sm font-medium text-fg">
            Оперативная сводка
          </div>
        </div>
        <div className="text-[11px] text-fg-subtle">
          с 00:00 · сброс ежедневно
        </div>
      </div>
      <div className="grid grid-cols-2 divide-y divide-border/40 sm:grid-cols-3 sm:divide-y-0 sm:divide-x lg:grid-cols-6">
        <TodayCell label="Доход" rawValue={revenue} fmt={fmtRub} accent loading={loading} />
        <TodayCell label="Платежей" rawValue={payments} fmt={fmtNum} loading={loading} />
        <TodayCell label="Средний чек" rawValue={avgCheck} fmt={fmtRub} loading={loading} />
        <TodayCell label="Новых юзеров" rawValue={newUsers} fmt={fmtNum} loading={loading} />
        <TodayCell
          label="Взяли триал"
          rawValue={trialActivated}
          fmt={fmtNum}
          sub={
            typeof newUsers === "number" && newUsers > 0 && trialActivated != null
              ? `${((trialActivated / newUsers) * 100).toFixed(0)}% от новых`
              : undefined
          }
          loading={loading}
        />
        <TodayCell
          label="Без триала"
          rawValue={noTrial}
          fmt={fmtNum}
          sub={
            typeof newUsers === "number" && newUsers > 0 && noTrial != null
              ? `${((noTrial / newUsers) * 100).toFixed(0)}% от новых`
              : undefined
          }
          loading={loading}
        />
      </div>
    </section>
  );
}

function TodayCell({
  label,
  rawValue,
  fmt,
  sub,
  accent,
  loading,
}: {
  label: string;
  rawValue: number | undefined;
  fmt: (v: number) => string;
  sub?: string;
  accent?: boolean;
  loading?: boolean;
}) {
  return (
    <div className="px-4 py-4 transition-colors hover:bg-bg-subtle/60">
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {label}
      </div>
      <div
        className={
          "mt-1 text-xl font-semibold tabular-nums tracking-tight md:text-2xl " +
          (accent ? "text-accent" : "text-fg")
        }
      >
        <AnimatedNum value={rawValue} fmt={fmt} loading={loading} />
      </div>
      {sub && (
        <div className="mt-0.5 truncate text-[11px] text-fg-subtle">{sub}</div>
      )}
    </div>
  );
}

function KpiRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="font-semibold text-fg tabular-nums">{value}</span>
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
    <div className="group rounded-xl border border-border bg-bg-subtle/40 p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:bg-bg-card hover:shadow-[0_4px_14px_-6px_rgba(0,0,0,0.08)]">
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {label}
      </div>
      <div
        className={
          "mt-1 text-lg font-semibold tabular-nums tracking-tight md:text-xl " +
          (accent ? "text-accent" : "text-fg")
        }
      >
        {loading ? "…" : value}
      </div>
      {sub && (
        <div className="mt-0.5 truncate text-[11px] text-fg-muted">{sub}</div>
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
    <div className="relative inline-flex shrink-0 items-stretch rounded-full border border-border bg-bg-elevated p-0.5 text-[11px] font-medium">
      <div
        aria-hidden
        className="absolute inset-y-0.5 rounded-full bg-accent shadow-glow-sm transition-[transform,width] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
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
            (o === value ? "font-semibold text-bg" : "text-fg-muted hover:text-fg")
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
                ? "border-transparent bg-accent text-bg shadow-glow-sm font-semibold"
                : "border-border bg-bg-card text-fg-muted hover:border-accent/40 hover:text-fg")
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
        color="bg-accent"
      />
      <FunnelStage
        label="Активные подписки"
        value={loading ? "…" : fmtNum(a)}
        sub={t > 0 ? `${aPct.toFixed(1)}% от всех` : undefined}
        pct={aRel}
        color="bg-success"
      />
      <FunnelStage
        label="Платящие"
        value={loading ? "…" : fmtNum(p)}
        sub={t > 0 ? `${pPct.toFixed(1)}% от всех` : undefined}
        pct={pRel}
        color="bg-tagpurple"
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
    <div className="rounded-xl border border-border bg-bg-subtle/40 p-4">
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-fg">
        {value}
      </div>
      {sub && (
        <div className="mt-0.5 text-[11px] text-fg-muted">{sub}</div>
      )}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-bg-elevated">
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
            className="flex-1 rounded-sm bg-bg-elevated"
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
            <stop offset="0%" stopColor="#0F1720" stopOpacity={0.22} />
            <stop offset="100%" stopColor="#0F1720" stopOpacity={0} />
          </linearGradient>
        </defs>
        <Tooltip content={<ChartTooltip valueFmt={fmtRub} label="Доход" />} cursor={{ stroke: "#CBD5E1", strokeDasharray: "3 3" }} />
        <Area
          type="monotone"
          dataKey="revenue_rubles"
          stroke="#0F1720"
          strokeWidth={2}
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

// HourlyChart — кастомный 24-bar visual без recharts. recharts даёт
// ровные тонкие столбики, но мы хотим pixel-perfect ширину для
// всех 24 часов + плавную смену метрики. Делаем вручную: каждая колонка
// инит-стартует с height=0 и анимируется до своего pct (CSS transition).
function HourlyChart({
  data,
  loading,
  metric,
}: {
  data: Array<{ hour: number } & Record<string, number>>;
  loading: boolean;
  metric: MetricKey;
}) {
  const def = METRICS.find((m) => m.key === metric) ?? METRICS[0];
  if (loading || data.length === 0) {
    return (
      <div className="mt-6 h-56">
        <SkeletonBars />
      </div>
    );
  }
  const values = data.map((d) => Number(d[metric]) || 0);
  const max = Math.max(1, ...values);
  const total = values.reduce((a, v) => a + v, 0);
  const peakIdx = values.indexOf(Math.max(...values));
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <div className="text-2xl font-semibold tabular-nums tracking-tight text-fg md:text-3xl">
          {def.valueFmt(total)}
        </div>
        <div className="text-sm text-fg-muted">
          пик в{" "}
          <span
            className="rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums"
            style={{ background: def.color + "22", color: def.color }}
          >
            {String(peakIdx).padStart(2, "0")}:00
          </span>
          <span className="ml-2 text-fg-subtle">
            ({def.valueFmt(values[peakIdx])})
          </span>
        </div>
      </div>
      <div className="relative mt-4 flex h-44 items-end gap-1">
        {data.map((d) => {
          const v = Number(d[metric]) || 0;
          const pct = (v / max) * 100;
          const isPeak = d.hour === peakIdx;
          return (
            <div
              key={d.hour}
              className="group relative flex h-full flex-1 flex-col items-center justify-end"
            >
              {/* Бар. Peak — solid accent-цвет метрики (виден на white bg),
                  non-peak — тот же цвет но с 22% opacity (муute-tone).
                  Так «один яркий» бар читается как фокус-точка. */}
              <div
                className="w-full rounded-t-md transition-[height,background-color] duration-500 ease-out"
                style={{
                  height: `${Math.max(2, pct)}%`,
                  background: isPeak ? def.color : def.color + "33",
                  boxShadow: isPeak
                    ? `0 8px 22px -10px ${def.color}66`
                    : undefined,
                }}
              />
              {/* Постоянный tooltip-pill над peak-баром (вне hover). */}
              {isPeak && (
                <>
                  <div className="pointer-events-none absolute bottom-full mb-2 whitespace-nowrap rounded-full bg-accent px-2.5 py-1 text-[10px] font-semibold tabular-nums text-bg shadow-glow-sm">
                    {def.valueFmt(v)}
                    <span
                      aria-hidden
                      className="absolute -bottom-0.5 left-1/2 h-1.5 w-1.5 -translate-x-1/2 rotate-45 bg-accent"
                    />
                  </div>
                  {/* Пунктирная вертикальная линия от вершины
                      peak-бара к базе — отметка «фокус-точки». */}
                  <span
                    aria-hidden
                    className="pointer-events-none absolute inset-y-0 left-1/2 -z-10 w-px -translate-x-1/2"
                    style={{
                      backgroundImage:
                        "linear-gradient(to bottom, rgba(252,252,252,0.35) 50%, transparent 0%)",
                      backgroundSize: "1px 4px",
                      backgroundRepeat: "repeat-y",
                    }}
                  />
                </>
              )}
              {/* Hover-tooltip для non-peak — даём контекст. */}
              {!isPeak && (
                <div className="pointer-events-none absolute bottom-full mb-1 hidden whitespace-nowrap rounded-md border border-border bg-bg-card px-2 py-1 text-[10px] font-medium shadow-md group-hover:block">
                  <span className="tabular-nums">{String(d.hour).padStart(2, "0")}:00</span>
                  <span className="ml-1.5 text-fg-subtle">·</span>
                  <span className="ml-1.5 tabular-nums text-fg">
                    {def.valueFmt(v)}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {/* Hour axis — 0/6/12/18/24 ticks для краткости */}
      <div className="mt-2 flex justify-between text-[10px] font-medium tabular-nums text-fg-subtle">
        {["00", "06", "12", "18", "24"].map((h) => (
          <span key={h}>{h}</span>
        ))}
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
          className="flex-1 rounded-sm bg-bg-elevated"
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
    <div className="rounded-lg border border-border bg-bg-card px-3 py-2 shadow-md">
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
        {typeof label === "string" ? fmtShortDate(label) : label}
      </div>
      {payload.map((p, i) => (
        <div key={i} className="mt-1 flex items-center gap-2 text-sm">
          <span
            className="h-2 w-2 rounded-full"
            style={{ background: p.color ?? "#F5F5F5" }}
          />
          <span className="text-fg-muted">{labelForKey(p.dataKey, p.name)}</span>
          <span className="font-semibold tabular-nums text-fg">
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

// Группы + краткое описание для plashka-header. Каждая группа
// сворачивается отдельно (Collapsible), список плашек по вертикали.
// Все свёрнуты по умолчанию — иначе экран сегментов растягивается
// на 500+ px.
const SEGMENT_GROUPS: { title: string; sub: string; keys: string[] }[] = [
  {
    title: "База",
    sub: "все юзеры · активные · без подписки · без Remnawave · холодные",
    keys: [
      "all_users",
      "active_subscriptions",
      "no_subscription",
      "no_remnawave",
      "started_1d_cold",
      "started_3d_cold",
      "started_7d_cold",
      "started_14d_cold",
      "started_30d_cold",
    ],
  },
  {
    title: "Активный триал",
    sub: "сейчас идут · день 1/2/3 · активирован за 24ч",
    keys: [
      "trial_active_any",
      "trial_activated_today",
      "trial_active_day1",
      "trial_active_day2",
      "trial_active_day3",
      "trial_ends_in_1d",
    ],
  },
  {
    title: "Триал истёк · реактивация",
    sub: "6ч / 1д / 2д / 3д / 7д / 14д / 30д / 60д / 90д / 180д / 365д",
    keys: [
      "trial_expired_6h",
      "trial_expired_1d",
      "trial_expired_2d",
      "trial_expired_3d",
      "trial_expired_7d",
      "trial_expired_14d",
      "trial_expired_30d",
      "trial_expired_60d",
      "trial_expired_90d",
      "trial_expired_180d",
      "trial_expired_365d",
      "trial_expired_within_6m",
    ],
  },
  {
    title: "Платные — скоро истекут",
    sub: "1д / 3д / 7д / 14д — pre-churn напоминания",
    keys: [
      "paid_expires_in_1d",
      "paid_expires_in_3d",
      "paid_expires_in_7d",
      "paid_expires_in_14d",
    ],
  },
  {
    title: "Платные истекли · реактивация",
    sub: "1д / 7д / 14д / 30д / 60д / 90д / 180д / 365д / 2 года",
    keys: [
      "paid_expired_1d",
      "paid_expired_7d",
      "paid_expired_14d",
      "paid_expired_30d",
      "paid_expired_60d",
      "paid_expired_90d",
      "paid_expired_180d",
      "paid_expired_365d",
      "paid_expired_730d",
      "paid_lapsed_any",
    ],
  },
  {
    title: "Недавно купили платную",
    sub: "7д / 14д / 30д — благодарности, upsell, feedback",
    keys: [
      "paid_bought_within_7d",
      "paid_bought_within_14d",
      "paid_bought_within_30d",
    ],
  },
  {
    title: "Истёкла любая подписка",
    sub: "агрегированные окна (триал ∪ платная)",
    keys: ["expired_1d", "expired_2d", "expired_3d", "expires_in_3d"],
  },
  {
    title: "Апселл · особые",
    sub: "VIP · Basic · Plus · Combo · со скидкой · с балансом",
    keys: [
      "vip_active",
      "basic_active",
      "plus_active",
      "combo_active",
      "discount_active",
      "has_balance_50plus",
    ],
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
        sub="обновляется каждые 5 мин · клик по строке → создать рассылку"
      />
      {error ? (
        <div className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          Не удалось загрузить сегменты.
        </div>
      ) : (
        <div className="mt-4 space-y-2.5">
          {SEGMENT_GROUPS.map((group) => {
            // Total-count группы (сумма всех сегментов) для badge в header.
            // count === -1 значит ошибка счётчика — пропускаем.
            const total = group.keys.reduce((a, k) => {
              const s = byKey.get(k);
              return s && s.count >= 0 ? a + s.count : a;
            }, 0);
            const badge = loading
              ? "…"
              : total > 0
              ? fmtNum(total)
              : "0";
            return (
              <Collapsible
                key={group.title}
                title={group.title}
                subtitle={group.sub}
                remember={`segments-${group.title}`}
                badge={
                  <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent tabular-nums">
                    {badge}
                  </span>
                }
              >
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
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
              </Collapsible>
            );
          })}
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
      className="group flex items-center justify-between gap-3 rounded-xl border border-border bg-bg-subtle/40 px-3 py-2.5 text-sm transition hover:border-accent/40 hover:bg-accent/10"
    >
      <span className="truncate text-fg-muted group-hover:text-fg">
        {label}
      </span>
      <span
        className={
          loading || isMissing
            ? "text-xs text-fg-subtle"
            : isEmpty
            ? "shrink-0 rounded-md bg-bg-elevated px-2 py-0.5 text-xs font-medium text-fg-subtle tabular-nums"
            : "shrink-0 rounded-md bg-accent/20 px-2 py-0.5 text-xs font-semibold text-accent tabular-nums"
        }
      >
        {loading ? "…" : isMissing ? "—" : fmtNum(count)}
      </span>
    </Link>
  );
}

// ─ Referral / tariffs / providers blocks ────────────────────────────

function ReferralBlock({
  data,
  loading,
}: {
  data: Record<string, unknown> | undefined;
  loading: boolean;
}) {
  const num = (k: string) => asNum(data?.[k]) ?? 0;
  const revenue = num("total_revenue");
  const cashback = num("total_cashback_paid");
  const net = revenue - cashback;
  return (
    <div className="mt-4 space-y-3">
      <div className="rounded-xl border border-border bg-gradient-to-br from-accent/5 to-white p-4">
        <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
          Чистая прибыль
        </div>
        <div className="mt-1 text-2xl font-semibold tabular-nums text-fg md:text-3xl">
          {loading ? "…" : fmtRub(net)}
        </div>
        <div className="mt-1 text-[11px] text-fg-muted">
          выручка − кэшбэк
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <MiniStat label="Выручка" value={fmtRub(revenue)} loading={loading} />
        <MiniStat label="Кэшбэк выплачен" value={fmtRub(cashback)} loading={loading} />
        <MiniStat label="Рефереров" value={fmtNum(num("total_referrers"))} loading={loading} />
        <MiniStat label="Приглашённых" value={fmtNum(num("total_referrals"))} loading={loading} />
      </div>
    </div>
  );
}

function MiniStat({
  label,
  value,
  loading,
}: {
  label: string;
  value: string;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-subtle/40 px-3 py-2">
      <div className="text-[9px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {label}
      </div>
      <div className="mt-0.5 text-sm font-semibold tabular-nums text-fg">
        {loading ? "…" : value}
      </div>
    </div>
  );
}

function TopReferrersList({
  data,
  loading,
}: {
  data: Array<Record<string, unknown>> | undefined;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="mt-4 space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton h-12" />
        ))}
      </div>
    );
  }
  if (!data?.length) {
    return (
      <div className="mt-6 text-sm text-fg-subtle">
        Пока нет данных по партнёрам.
      </div>
    );
  }
  return (
    <ol className="mt-4 space-y-1.5">
      {data.slice(0, 5).map((r, i) => {
        // Backend возвращает referrer_id/total_invited_revenue/
        // total_cashback_paid — раньше был баг: читали telegram_id/
        // total_revenue/cashback_paid → всё показывалось как «—».
        const id = asNum(r.referrer_id) ?? asNum(r.telegram_id) ?? 0;
        const username = (r.username as string) || "—";
        const invited = asNum(r.invited_count) ?? 0;
        const trials = asNum(r.trial_count) ?? 0;
        const paid = asNum(r.paid_count) ?? 0;
        const trialPct = asNum(r.trial_percent) ?? 0;
        const paidPct = asNum(r.conversion_percent) ?? 0;
        const revenue = asNum(r.total_invited_revenue) ?? asNum(r.total_revenue) ?? 0;
        const cashback = asNum(r.total_cashback_paid) ?? asNum(r.cashback_paid) ?? 0;
        return (
          <Link
            key={String(id) + "_" + i}
            to={`/referrals`}
            className="group flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 transition hover:border-border hover:bg-bg-subtle/60"
          >
            <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-bg-elevated text-[11px] font-semibold tabular-nums text-fg-muted">
              {i + 1}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-fg">
                {username !== "—" ? `@${username}` : `tg:${id}`}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] tabular-nums">
                <span className="rounded-md bg-fg/5 px-1.5 py-0.5 text-fg-muted">
                  👥 {fmtNum(invited)}
                </span>
                <span
                  className="rounded-md bg-info/10 px-1.5 py-0.5 text-info"
                  title="сколько из приглашённых активировали пробный период"
                >
                  🎁 {fmtNum(trials)}
                  {invited > 0 && (
                    <span className="ml-0.5 text-info/70">
                      · {trialPct.toFixed(0)}%
                    </span>
                  )}
                </span>
                <span
                  className="rounded-md bg-success/10 px-1.5 py-0.5 text-success"
                  title="сколько из приглашённых сделали хотя бы одну оплату"
                >
                  💳 {fmtNum(paid)}
                  {invited > 0 && (
                    <span className="ml-0.5 text-success/70">
                      · {paidPct.toFixed(0)}%
                    </span>
                  )}
                </span>
                <span className="text-fg-subtle">
                  кэшбэк {fmtRub(cashback)}
                </span>
              </div>
            </div>
            <div className="shrink-0 text-right">
              <div className="text-sm font-semibold tabular-nums text-fg">
                {fmtRub(revenue)}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
                выручка
              </div>
            </div>
          </Link>
        );
      })}
    </ol>
  );
}

const TARIFF_DEFS = [
  { key: "basic", label: "Basic", color: "#0F1720" },
  { key: "plus", label: "Plus", color: "#2563EB" },
  { key: "basic_combo", label: "Basic + Combo", color: "#10B981" },
  { key: "plus_combo", label: "Plus + Combo", color: "#EC4899" },
  { key: "proxy", label: "Прокси", color: "#F59E0B" },
] as const;

function TariffsBlock({
  data,
  loading,
}: {
  data: Record<string, unknown> | undefined;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="mt-4 space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton h-10" />
        ))}
      </div>
    );
  }
  const totals = TARIFF_DEFS.map((t) => {
    const cat = (data?.[t.key] as Record<string, unknown> | undefined) ?? {};
    const all = (cat["all"] as { count?: number; revenue?: number } | undefined) ?? {};
    return {
      ...t,
      count: Number(all.count ?? 0),
      revenueKop: Number(all.revenue ?? 0),
    };
  });
  const totalRevenue = totals.reduce((a, t) => a + t.revenueKop, 0);
  return (
    <div className="mt-4 space-y-2.5">
      {totals.map((t) => {
        const rub = t.revenueKop / 100;
        const pct = totalRevenue > 0 ? (t.revenueKop / totalRevenue) * 100 : 0;
        return (
          <div key={t.key} className="rounded-xl border border-border bg-bg-subtle/40 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ background: t.color }}
                />
                <span className="text-sm font-medium text-fg">
                  {t.label}
                </span>
              </div>
              <div className="text-sm font-semibold tabular-nums text-fg">
                {fmtRub(rub)}
                <span className="ml-2 text-[11px] font-normal text-fg-subtle tabular-nums">
                  {pct.toFixed(1)}%
                </span>
              </div>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-bg-elevated">
              <div
                className="h-full transition-[width] duration-700"
                style={{ width: `${Math.max(2, pct)}%`, background: t.color }}
              />
            </div>
            <div className="mt-1.5 text-[11px] text-fg-muted tabular-nums">
              {fmtNum(t.count)} продаж
            </div>
          </div>
        );
      })}
    </div>
  );
}

const PROVIDER_LABELS: Record<string, string> = {
  platega: "Platega",
  cryptobot: "CryptoBot",
  telegram_stars: "Telegram Stars",
  lava: "Lava",
  balance: "С баланса",
  unknown: "Прочее",
};
const PROVIDER_COLORS: Record<string, string> = {
  platega: "#0F1720",
  cryptobot: "#F59E0B",
  telegram_stars: "#2563EB",
  lava: "#10B981",
  balance: "#7C3AED",
  unknown: "#94A3B8",
};

// Локализуем ключи для читаемого label в PaymentsBreakdownCard.
const _PT_LABEL: Record<string, string> = {
  subscription: "Подписка",
  balance_topup: "Пополнение баланса",
  gift: "Подарок",
  telegram_premium: "Telegram Premium",
  telegram_stars: "Telegram Stars",
  traffic_pack: "Пакет ГБ",
  apple_id: "Apple ID",
  steam: "Steam",
  spotify: "Spotify Premium",
  proxy: "MTProxy",
  unknown: "Прочее",
};
const _APPLE_REGION_LABEL: Record<string, string> = {
  usa: "🇺🇸 USA",
  turkey: "🇹🇷 Turkey",
  russia: "🇷🇺 Russia",
  india: "🇮🇳 India",
};
const _APPLE_CUR: Record<string, string> = {
  usa: "$",
  turkey: "TL",
  russia: "₽",
  india: "INR",
};

function PaymentsBreakdownCard({
  data,
  loading,
  hours,
  onHoursChange,
}: {
  data:
    | {
        total: { count: number; revenue_rubles: number };
        by_provider: Array<{ provider: string; count: number; revenue_rubles: number }>;
        by_type: Array<{ purchase_type: string; count: number; revenue_rubles: number }>;
        by_tariff: Array<{ tariff: string; count: number; revenue_rubles: number }>;
        by_apple_nominal: Array<{
          region: string;
          nominal: number;
          count: number;
          revenue_rubles: number;
        }>;
      }
    | undefined;
  loading: boolean;
  hours: 24 | 168 | 720;
  onHoursChange: (h: 24 | 168 | 720) => void;
}) {
  const label = (h: number) => (h === 24 ? "24ч" : h === 168 ? "7д" : "30д");
  return (
    <SurfaceCard className="mt-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SurfaceHeader
          eyebrow={`Оплаты · ${label(hours)}`}
          title="Разбивка по продукту и провайдеру"
          sub={
            data
              ? `${fmtNum(data.total.count)} шт · ${fmtRub(data.total.revenue_rubles)}`
              : undefined
          }
        />
        <SegPill<24 | 168 | 720>
          value={hours}
          options={[24, 168, 720]}
          onChange={onHoursChange}
          fmt={label}
        />
      </div>
      {loading ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="skeleton h-32" />
          ))}
        </div>
      ) : !data || data.total.count === 0 ? (
        <div className="mt-6 text-sm text-fg-subtle">
          За выбранный период оплат не было.
        </div>
      ) : (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <BreakdownList
            title="По продукту"
            rows={data.by_type.map((r) => ({
              label: _PT_LABEL[r.purchase_type] ?? r.purchase_type,
              count: r.count,
              revenue: r.revenue_rubles,
            }))}
          />
          <BreakdownList
            title="По провайдеру оплаты"
            rows={data.by_provider.map((r) => ({
              label: PROVIDER_LABELS[r.provider] ?? r.provider,
              count: r.count,
              revenue: r.revenue_rubles,
              color: PROVIDER_COLORS[r.provider],
            }))}
          />
          <BreakdownList
            title="Топ-15 тарифов"
            rows={data.by_tariff.map((r) => ({
              label: r.tariff,
              count: r.count,
              revenue: r.revenue_rubles,
            }))}
          />
          {data.by_apple_nominal.length > 0 && (
            <BreakdownList
              title="Apple ID · по номиналу"
              rows={data.by_apple_nominal.map((r) => ({
                label: `${_APPLE_REGION_LABEL[r.region] ?? r.region} · ${r.nominal}${_APPLE_CUR[r.region] ?? "$"}`,
                count: r.count,
                revenue: r.revenue_rubles,
              }))}
            />
          )}
        </div>
      )}
    </SurfaceCard>
  );
}

function BreakdownList({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ label: string; count: number; revenue: number; color?: string }>;
}) {
  const totalRev = rows.reduce((a, r) => a + r.revenue, 0);
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/40 p-3">
      <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {title}
      </div>
      <div className="space-y-1.5">
        {rows.length === 0 && (
          <div className="text-xs text-fg-subtle">Нет данных</div>
        )}
        {rows.map((r) => {
          const pct = totalRev > 0 ? (r.revenue / totalRev) * 100 : 0;
          return (
            <div key={r.label} className="text-xs">
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  {r.color && (
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: r.color }}
                    />
                  )}
                  <span className="truncate text-fg-muted">{r.label}</span>
                </div>
                <div className="shrink-0 tabular-nums text-fg">
                  {fmtRub(r.revenue)}
                  <span className="ml-1.5 text-[10px] text-fg-subtle">
                    {fmtNum(r.count)}
                  </span>
                </div>
              </div>
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-bg-elevated">
                <div
                  className="h-full bg-accent/70 transition-[width] duration-500"
                  style={{ width: `${Math.max(2, pct)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProvidersBlock({
  data,
  loading,
}: {
  data: Array<{ provider: string; count: number; revenue_rubles: number }> | undefined;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="mt-4 space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="skeleton h-10" />
        ))}
      </div>
    );
  }
  if (!data?.length) {
    return (
      <div className="mt-6 text-sm text-fg-subtle">
        За выбранный период нет платежей.
      </div>
    );
  }
  const totalRev = data.reduce((a, r) => a + r.revenue_rubles, 0);
  const sorted = [...data].sort((a, b) => b.revenue_rubles - a.revenue_rubles);
  return (
    <div className="mt-4 space-y-2">
      {sorted.map((r) => {
        const pct = totalRev > 0 ? (r.revenue_rubles / totalRev) * 100 : 0;
        const color = PROVIDER_COLORS[r.provider] ?? "#94A3B8";
        return (
          <div key={r.provider} className="rounded-lg border border-border bg-bg-subtle/30 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full" style={{ background: color }} />
                <span className="text-sm font-medium text-fg">
                  {PROVIDER_LABELS[r.provider] ?? r.provider}
                </span>
              </div>
              <div className="text-sm font-semibold tabular-nums text-fg">
                {fmtRub(r.revenue_rubles)}
              </div>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-bg-elevated">
              <div
                className="h-full transition-[width] duration-700"
                style={{ width: `${Math.max(2, pct)}%`, background: color }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-[11px] text-fg-muted tabular-nums">
              <span>{fmtNum(r.count)} платежей</span>
              <span>{pct.toFixed(1)}%</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function providerHoursLabel(h: 24 | 168 | 720): string {
  return h === 24 ? "24ч" : h === 168 ? "7д" : "30д";
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

// useCountUp — анимация цифры от предыдущего значения к новому через
// requestAnimationFrame. Ease-out cubic — быстрый старт, мягкий
// финиш, ощущается «дорого». Не запускается, если target null/NaN
// или совпадает с текущим.
function useCountUp(target: number | undefined, duration = 900): number {
  const [value, setValue] = useState<number>(target ?? 0);
  const rafRef = useRef<number | null>(null);
  const fromRef = useRef<number>(target ?? 0);
  const lastTargetRef = useRef<number | undefined>(target);

  useEffect(() => {
    if (target == null || !Number.isFinite(target)) return;
    if (target === lastTargetRef.current) return;
    fromRef.current = value;
    lastTargetRef.current = target;
    const startedAt = performance.now();
    const from = fromRef.current;
    const to = target;
    const step = (now: number) => {
      const t = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (to - from) * eased);
      if (t < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration]);

  return value;
}

// Stagger — обёртка с CSS animation-delay для последовательного
// fade-up появления секций. index — порядок в layout.
function Stagger({
  index,
  children,
}: {
  index: number;
  children: React.ReactNode;
}) {
  return (
    <div
      className="animate-fade-up"
      style={{ animationDelay: `${index * 60}ms` }}
    >
      {children}
    </div>
  );
}

// AnimatedNum — обёртка вокруг useCountUp, форматирует число через
// переданный formatter. Если loading или target пустой — показывает
// placeholder без анимации.
function AnimatedNum({
  value,
  fmt,
  loading,
  duration,
}: {
  value: number | undefined;
  fmt: (v: number) => string;
  loading?: boolean;
  duration?: number;
}) {
  const animated = useCountUp(value, duration);
  if (loading || value == null) return <span>…</span>;
  return <span>{fmt(animated)}</span>;
}
