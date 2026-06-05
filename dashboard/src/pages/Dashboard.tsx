import { useState } from "react";
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
import { fmtNum, fmtRub, fmtRelative } from "@/lib/format";
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
  const today = useQuery({
    queryKey: ["stats", "period", 24],
    queryFn: () => endpoints.statsPeriod(24),
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
      entry = {
        id: ++liveCounter,
        kind: e.type,
        title: e.is_renewal ? "Продление подписки" : "Новая подписка",
        subtitle: `tg:${e.telegram_id} · до ${new Date(e.expires_at).toLocaleDateString("ru-RU")}`,
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
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Дашборд
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Atlas Secure
          </h1>
        </div>
        <div className="hidden text-right text-xs text-fg-muted md:block">
          Данные обновляются автоматически
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
          value={fmtNum(asNum(overview.data?.active_subscriptions))}
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
                За 24 часа
              </div>
              <h2 className="text-lg font-semibold text-fg">Активность</h2>
            </div>
            <Clock className="h-4 w-4 text-fg-subtle" />
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile
              label="Платежей"
              value={fmtNum(asNum(today.data?.payment_count ?? today.data?.payments))}
            />
            <Tile
              label="Доход"
              value={fmtRub(asNum(today.data?.revenue))}
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

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-bg-subtle/60 px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className="mt-1 text-lg font-semibold text-fg">{value}</div>
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
