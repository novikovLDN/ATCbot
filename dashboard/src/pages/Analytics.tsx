import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Clock,
  TrendingUp,
  Download,
  FileText,
  Gauge,
  Wallet,
  CreditCard,
  Users as UsersIcon,
} from "lucide-react";
import { ApiError, downloadCsv, endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { StatCard } from "@/components/StatCard";
import { Spinner } from "@/components/Spinner";
import { toast } from "@/store/toast";

const RANGES = [
  { label: "24ч", hours: 24 },
  { label: "7д", hours: 168 },
  { label: "30д", hours: 720 },
  { label: "180д", hours: 4320 },
  { label: "1г", hours: 8760 },
];

const TARIFF_LABELS: Record<string, string> = {
  basic: "Basic",
  plus: "Plus",
  basic_combo: "Basic + Combo",
  plus_combo: "Plus + Combo",
  combo_basic: "Basic + Combo",
  combo_plus: "Plus + Combo",
  proxy: "MTProxy",
  trial: "Триал",
  subscription: "Подписки",
  traffic: "Трафик ГБ",
  balance_topup: "Пополнение",
  farm: "Ферма",
};

const WINDOW_LABELS: Record<string, string> = {
  "24h": "24ч",
  "7d": "7д",
  "30d": "30д",
  "180d": "180д",
  "365d": "1г",
  "1y": "1г",
  all: "Всё время",
};

export function Analytics() {
  const [hours, setHours] = useState(720);

  const period = useQuery({
    queryKey: ["payments", "revenue", hours],
    queryFn: () => endpoints.paymentsRevenue(hours),
    refetchInterval: 60_000,
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
          value={fmtNum(period.data?.payments_count)}
          icon={Clock}
          loading={period.isLoading}
        />
        <StatCard
          label="Доход"
          value={fmtRub(period.data?.revenue_rubles)}
          tone="success"
          icon={Wallet}
          loading={period.isLoading}
        />
        <StatCard
          label="Средний чек"
          value={fmtRub(period.data?.avg_check_rubles)}
          tone="accent"
          icon={Gauge}
          loading={period.isLoading}
        />
        <StatCard
          label="Типов покупок"
          value={fmtNum(Object.keys(period.data?.by_type ?? {}).length)}
          icon={CreditCard}
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
          icon={UsersIcon}
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

      <BreakdownTable data={breakdown.data} loading={breakdown.isLoading} />

      <ExportSection />
    </div>
  );
}

interface BreakdownPayload {
  [category: string]: {
    [window: string]: { count?: number; revenue?: number } | undefined;
  };
}

function BreakdownTable({
  data,
  loading,
}: {
  data: unknown;
  loading: boolean;
}) {
  // Normalise the back-end shape (per-tariff dict of per-window dicts)
  // to a 2-D table. We don't trust the field name for the inner
  // revenue (kopecks vs rubles vary across legacy callers) — we
  // detect by magnitude.
  const { categories, windows, rows } = useMemo(() => {
    const empty = { categories: [] as string[], windows: [] as string[], rows: [] as Array<{ category: string; cells: Array<{ window: string; count: number; revenue: number }> }> };
    if (!data || typeof data !== "object") return empty;
    const payload = data as BreakdownPayload;
    const cats = Object.keys(payload).filter(
      (c) => payload[c] && typeof payload[c] === "object",
    );
    const winsSet = new Set<string>();
    for (const c of cats) {
      const inner = payload[c] || {};
      Object.keys(inner).forEach((w) => winsSet.add(w));
    }
    const winOrder = ["24h", "7d", "30d", "180d", "365d", "1y", "all"];
    const wins = Array.from(winsSet).sort(
      (a, b) => winOrder.indexOf(a) - winOrder.indexOf(b),
    );
    const rows = cats.map((cat) => {
      const inner = payload[cat] || {};
      return {
        category: cat,
        cells: wins.map((w) => {
          const v = inner[w] ?? {};
          const rawRev = typeof v.revenue === "number" ? v.revenue : 0;
          const revenue = rawRev >= 100_000 ? rawRev / 100 : rawRev;
          return {
            window: w,
            count: typeof v.count === "number" ? v.count : 0,
            revenue,
          };
        }),
      };
    });
    return { categories: cats, windows: wins, rows };
  }, [data]);

  const totalsPerWindow = useMemo(() => {
    const t = new Map<string, { count: number; revenue: number }>();
    for (const w of windows) t.set(w, { count: 0, revenue: 0 });
    for (const row of rows) {
      for (const c of row.cells) {
        const cur = t.get(c.window) ?? { count: 0, revenue: 0 };
        cur.count += c.count;
        cur.revenue += c.revenue;
        t.set(c.window, cur);
      }
    }
    return t;
  }, [rows, windows]);

  return (
    <section className="card p-5">
      <div className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-fg-subtle">
        <CreditCard className="h-3 w-3" /> Покупки по тарифам
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-fg-muted">
          <Spinner /> Загружаю...
        </div>
      ) : categories.length === 0 || windows.length === 0 ? (
        <div className="text-sm text-fg-muted">Нет данных</div>
      ) : (
        <div className="overflow-x-auto -mx-2">
          <table className="w-full min-w-[600px] text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                <th className="px-2 py-2 font-medium">Тариф</th>
                {windows.map((w) => (
                  <th key={w} className="px-2 py-2 font-medium text-right">
                    {WINDOW_LABELS[w] ?? w}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {rows.map((row) => (
                <tr key={row.category} className="hover:bg-accent/[0.04]">
                  <td className="px-2 py-2.5 text-fg">
                    {TARIFF_LABELS[row.category] ?? row.category}
                  </td>
                  {row.cells.map((c) => (
                    <td
                      key={c.window}
                      className="px-2 py-2.5 text-right align-top"
                    >
                      <div className="font-medium text-fg">
                        {fmtRub(c.revenue)}
                      </div>
                      <div className="text-[11px] text-fg-subtle">
                        {fmtNum(c.count)} шт
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="bg-bg-subtle/40 font-medium">
                <td className="px-2 py-2.5 text-fg">Итого</td>
                {windows.map((w) => {
                  const t = totalsPerWindow.get(w) ?? { count: 0, revenue: 0 };
                  return (
                    <td key={w} className="px-2 py-2.5 text-right">
                      <div className="text-success">{fmtRub(t.revenue)}</div>
                      <div className="text-[11px] text-fg-muted">
                        {fmtNum(t.count)} шт
                      </div>
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ExportSection() {
  const [busy, setBusy] = useState<string | null>(null);

  const download = async (path: string, filename: string, label: string) => {
    setBusy(label);
    try {
      await downloadCsv(path, filename);
      toast.success(`Скачан ${filename}`);
    } catch (e: unknown) {
      toast.error((e as ApiError)?.detail ?? "Не удалось скачать");
    } finally {
      setBusy(null);
    }
  };

  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");

  return (
    <section className="card p-5">
      <div className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-fg-subtle">
        <FileText className="h-3 w-3" /> Экспорт данных
      </div>
      <p className="mb-4 text-sm text-fg-muted">
        Стримит CSV прямо из базы с авторизованным запросом. Файл качается на
        твою машину без захода токена в URL.
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={() =>
            download("/export/users.csv", `users_${stamp}.csv`, "users")
          }
          disabled={busy !== null}
          className="btn-secondary justify-start"
        >
          {busy === "users" ? <Spinner /> : <Download className="h-3.5 w-3.5" />}
          Все пользователи (users.csv)
        </button>
        <button
          type="button"
          onClick={() =>
            download(
              "/export/subscriptions.csv",
              `subscriptions_${stamp}.csv`,
              "subscriptions",
            )
          }
          disabled={busy !== null}
          className="btn-secondary justify-start"
        >
          {busy === "subscriptions" ? (
            <Spinner />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          Активные подписки (subscriptions.csv)
        </button>
      </div>
    </section>
  );
}
