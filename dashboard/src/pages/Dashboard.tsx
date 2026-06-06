import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Users as UsersIcon,
  Wallet,
  ShieldCheck,
  Clock,
  TrendingUp,
  CreditCard,
  Activity,
  Sparkles,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { useEventStream, type BusEvent } from "@/lib/ws";
import {
  fmtNum,
  fmtRub,
  fmtRelative,
  mskDayKey,
  mskTodayStartIso,
} from "@/lib/format";
import { StatCard } from "@/components/StatCard";
import { EmptyState } from "@/components/EmptyState";

interface LiveEntry {
  id: number;
  kind: BusEvent["type"];
  title: string;
  subtitle?: string;
  at: number;
}

let liveCounter = 0;

export function Dashboard() {
  const qc = useQueryClient();
  const overview = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: endpoints.statsOverview,
    refetchInterval: 60000,
  });
  const revenue = useQuery({
    queryKey: ["stats", "revenue"],
    queryFn: endpoints.statsRevenue,
    refetchInterval: 60000,
  });
  // "Сегодня (МСК)" — calendar day window from 00:00 to 23:59 Europe/Moscow.
  // Resets daily at MSK midnight: the queryKey segment flips when the
  // MSK day changes, which forces a fresh fetch even if the user keeps
  // the tab open overnight.
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
    refetchInterval: 60000,
  });
  // get_analytics_by_period doesn't compute revenue / payments — pull
  // those from /payments/revenue so the "today" tile matches what's on
  // the Payments page rather than reading missing fields.
  const today24Revenue = useQuery({
    queryKey: ["payments", "revenue", "msk-today", todayKey],
    queryFn: () => endpoints.paymentsRevenueSince(todaySince),
    refetchInterval: 60000,
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
        subtitle: typeof e.telegram_id === "number" ? `tg:${e.telegram_id}` : undefined,
        at: Date.now(),
      };
    }

    if (entry) setLive((prev) => [entry!, ...prev].slice(0, 25));

    // Invalidate so cards update soon after the event (debounced by
    // React Query's stale time + a fresh poll).
    qc.invalidateQueries({ queryKey: ["stats"] });
  });

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-fg-subtle">
            Atlas Secure
          </div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-fg md:text-[40px] md:leading-[1.05]">
            Добро пожаловать
          </h1>
          <p className="mt-2 text-sm text-fg-muted">
            Сводка по боту обновляется в реальном времени.
          </p>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4 md:gap-4">
        <StatCard
          label="Всего юзеров"
          value={fmtNum(asNum(overview.data?.total_users))}
          icon={UsersIcon}
          tone="accent"
          loading={overview.isLoading}
        />
        <StatCard
          label="Активные подписки"
          value={fmtNum(
            asNum(
              overview.data?.active_paid_subscriptions ??
                overview.data?.active_subscriptions,
            ),
          )}
          hint={
            overview.data?.active_subscriptions != null &&
            overview.data?.active_paid_subscriptions != null &&
            overview.data.active_subscriptions !== overview.data.active_paid_subscriptions
              ? `всего с триалами ${fmtNum(asNum(overview.data.active_subscriptions))}`
              : undefined
          }
          icon={ShieldCheck}
          tone="success"
          loading={overview.isLoading}
        />
        <StatCard
          label="Доход всего"
          value={fmtRub(revenue.data?.total_revenue_rubles)}
          hint={revenue.data ? `ARPU ${fmtRub(revenue.data.arpu_rubles)}` : undefined}
          icon={Wallet}
          loading={revenue.isLoading}
        />
        <StatCard
          label="Платящие"
          value={fmtNum(revenue.data?.paying_users)}
          hint={revenue.data ? `LTV ${fmtRub(revenue.data.avg_ltv_rubles)}` : undefined}
          icon={CreditCard}
          loading={revenue.isLoading}
        />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card p-5 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
                Сегодня · МСК
              </div>
              <h2 className="text-lg font-semibold text-fg">Активность</h2>
              <div className="mt-0.5 text-[11px] text-fg-subtle">
                с 00:00 до 23:59 по Москве · сброс ежедневно в 00:00 МСК
              </div>
            </div>
            <Clock className="h-4 w-4 text-fg-subtle" />
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Tile
              label="Платежей"
              value={fmtNum(today24Revenue.data?.payments_count)}
            />
            <Tile
              label="Доход"
              tone="success"
              value={fmtRub(today24Revenue.data?.revenue_rubles)}
            />
            <Tile
              label="Средний чек"
              tone="accent"
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
        </div>

        <div className="card relative overflow-hidden p-5">
          <div className="pointer-events-none absolute -right-12 -top-12 h-40 w-40 rounded-full bg-accent/15 blur-3xl" />
          <div className="relative">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
                  Бизнес-метрики
                </div>
                <h2 className="text-lg font-semibold text-fg">KPI</h2>
              </div>
              <TrendingUp className="h-4 w-4 text-fg-subtle" />
            </div>
            <div className="space-y-3">
              <Row
                label="Среднее время апрува"
                value={fmtSeconds(
                  asNum(overview.data?.business_metrics?.avg_payment_approval_time_seconds),
                )}
              />
              <Row
                label="Средний срок жизни"
                value={`${fmtNum(
                  asNum(overview.data?.business_metrics?.avg_subscription_lifetime_days),
                )} дн`}
              />
              <Row
                label="Продлений на юзера"
                value={fmtNum(
                  asNum(overview.data?.business_metrics?.avg_renewals_per_user),
                )}
              />
              <Row
                label="Approval rate"
                value={`${fmtNum(
                  asNum(overview.data?.business_metrics?.approval_rate_percent),
                )}%`}
              />
            </div>
          </div>
        </div>
      </section>

      <section className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
              Live
            </div>
            <h2 className="text-lg font-semibold text-fg">Поток событий</h2>
          </div>
          <Activity className="h-4 w-4 text-fg-subtle" />
        </div>

        {live.length === 0 ? (
          <EmptyState
            icon={Sparkles}
            title="Пока тихо"
            description="События появятся здесь по мере поступления — новые юзеры, платежи, действия админа."
          />
        ) : (
          <ul className="divide-y divide-border/60">
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
                  <div className="truncate font-medium text-fg">{e.title}</div>
                  {e.subtitle && (
                    <div className="truncate text-xs text-fg-muted">{e.subtitle}</div>
                  )}
                </div>
                <div className="shrink-0 text-xs text-fg-subtle">
                  {fmtRelative(new Date(e.at).toISOString())}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
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
  tone?: "accent" | "success";
}) {
  const wrapClass =
    tone === "accent"
      ? "rounded-xl border border-accent/20 bg-accent/5 px-4 py-3 transition-colors hover:border-accent/40"
      : tone === "success"
      ? "rounded-xl border border-success/20 bg-success/5 px-4 py-3 transition-colors hover:border-success/40"
      : "rounded-xl border border-border bg-bg-subtle/60 px-4 py-3 transition-colors hover:border-fg-subtle";
  const numClass =
    tone === "accent"
      ? "mt-1 text-xl font-semibold tracking-tight text-accent md:text-2xl"
      : tone === "success"
      ? "mt-1 text-xl font-semibold tracking-tight text-success md:text-2xl"
      : "mt-1 text-xl font-semibold tracking-tight text-fg md:text-2xl";
  return (
    <div className={wrapClass}>
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
        {label}
      </div>
      <div className={numClass}>{value}</div>
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

function fmtSeconds(s: number | undefined): string {
  if (s == null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${Math.round(s)}с`;
  if (s < 3600) return `${Math.round(s / 60)}мин`;
  return `${Math.round((s / 3600) * 10) / 10}ч`;
}
