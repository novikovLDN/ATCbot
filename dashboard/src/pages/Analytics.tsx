import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, TrendingUp } from "lucide-react";
import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { StatCard } from "@/components/StatCard";
import { Spinner } from "@/components/Spinner";

const RANGES = [
  { label: "24ч", hours: 24 },
  { label: "7д", hours: 168 },
  { label: "30д", hours: 720 },
  { label: "180д", hours: 4320 },
  { label: "1г", hours: 8760 },
];

export function Analytics() {
  const [hours, setHours] = useState(720);

  const period = useQuery({
    queryKey: ["stats", "period", hours],
    queryFn: () => endpoints.statsPeriod(hours),
  });

  const revenue = useQuery({
    queryKey: ["stats", "revenue"],
    queryFn: endpoints.statsRevenue,
  });

  const breakdown = useQuery({
    queryKey: ["stats", "breakdown"],
    queryFn: endpoints.statsBreakdown,
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">
            Анализ
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg md:text-3xl">
            Метрики и доход
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
          label="Платежей"
          value={fmtNum(asNum(period.data?.payment_count ?? period.data?.payments))}
          icon={Clock}
          loading={period.isLoading}
        />
        <StatCard
          label="Доход за период"
          value={fmtRub(asNum(period.data?.revenue))}
          tone="success"
          loading={period.isLoading}
        />
        <StatCard
          label="Новых юзеров"
          value={fmtNum(asNum(period.data?.new_users))}
          tone="accent"
          loading={period.isLoading}
        />
        <StatCard
          label="Новых подписок"
          value={fmtNum(asNum(period.data?.new_subscriptions))}
          loading={period.isLoading}
        />
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatCard
          label="Доход всего"
          value={fmtRub(revenue.data?.total_revenue_rubles)}
          tone="accent"
          loading={revenue.isLoading}
        />
        <StatCard
          label="Платящих юзеров"
          value={fmtNum(revenue.data?.paying_users)}
          loading={revenue.isLoading}
        />
        <StatCard
          label="ARPU / средний LTV"
          value={
            revenue.data
              ? `${fmtRub(revenue.data.arpu_rubles)} / ${fmtRub(revenue.data.avg_ltv_rubles)}`
              : "—"
          }
          icon={TrendingUp}
          loading={revenue.isLoading}
        />
      </section>

      <section className="card p-5">
        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-fg-subtle">
          Разбивка покупок
        </div>
        {breakdown.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Spinner /> Загружаю...
          </div>
        ) : breakdown.data ? (
          <pre className="max-h-[500px] overflow-auto rounded-xl border border-border bg-bg-subtle/60 p-4 text-xs leading-relaxed text-fg-muted">
            {JSON.stringify(breakdown.data, null, 2)}
          </pre>
        ) : null}
      </section>
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
