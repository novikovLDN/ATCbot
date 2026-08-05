import { useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Card, CardHeader, EmptyFailure, LoadingGate, Skeleton } from "@/components/ui";

/**
 * Воронка: база → активные подписки → платящие.
 *
 * ПЕРЕЕХАЛА СЮДА СО СВОДКИ вместе с остальным «уровнем 2». На главной
 * из неё не следовало действия: она отвечает на вопрос «как у нас в
 * целом», а не «что делать сейчас».
 *
 * Числа берутся из тех же двух эндпоинтов, что и раньше (/stats/overview
 * и /stats/revenue). «Активные» — платные без триалов, если бэкенд отдал
 * active_paid_subscriptions; иначе честно падаем на общее число и это
 * видно в подписи.
 *
 * Отказ запроса рисуется отказом, а не нулевой воронкой: три нуля тут
 * выглядели бы как «бизнеса нет».
 */
export function ConversionFunnel() {
  const overview = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: endpoints.statsOverview,
    staleTime: 60_000,
  });
  const revenue = useQuery({
    queryKey: ["stats", "revenue"],
    queryFn: endpoints.statsRevenue,
    staleTime: 60_000,
  });

  const failed = overview.isError || revenue.isError;
  const total = num(overview.data?.total_users);
  const paidOnly = num(overview.data?.active_paid_subscriptions);
  const active = paidOnly ?? num(overview.data?.active_subscriptions);
  const paying = num(revenue.data?.paying_users);

  return (
    <Card>
      <CardHeader
        title="Воронка"
        subtitle={
          paidOnly === undefined
            ? "за всё время · «активные» здесь включают триалы"
            : "за всё время · активные — платные, без триалов"
        }
      />
      <div className="p-4">
        {failed ? (
          <EmptyFailure
            what="воронку конверсии"
            reason="Один из двух запросов не вернулся. Нули здесь читались бы как отсутствие клиентов."
            onRetry={() => {
              overview.refetch();
              revenue.refetch();
            }}
          />
        ) : (
          <LoadingGate
            loading={overview.isLoading || revenue.isLoading}
            skeleton={
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-24" />
                ))}
              </div>
            }
            message="Считаю воронку"
          >
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Stage
                label="Всего пользователей"
                value={total}
                sub="нажали /start"
                share={100}
                tone="bg-n-9"
              />
              <Stage
                label="Активные подписки"
                value={active}
                sub={share(active, total)}
                share={ratio(active, total)}
                tone="bg-accent-9"
              />
              <Stage
                label="Платящие"
                value={paying}
                sub={share(paying, total)}
                share={ratio(paying, total)}
                tone="bg-success-solid"
              />
            </div>
          </LoadingGate>
        )}
      </div>
    </Card>
  );
}

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return undefined;
}

function ratio(part?: number, whole?: number): number {
  if (!whole || whole <= 0 || part === undefined) return 0;
  // Не меньше 2% ширины, иначе живая, но маленькая доля выглядит как ноль.
  return Math.max(2, (part / whole) * 100);
}

function share(part?: number, whole?: number): string | undefined {
  if (!whole || whole <= 0 || part === undefined) return undefined;
  return `${((part / whole) * 100).toFixed(1)}% от всех`;
}

function Stage({
  label,
  value,
  sub,
  share: pct,
  tone,
}: {
  label: string;
  value?: number;
  sub?: string;
  share: number;
  tone: string;
}) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="text-xs font-medium text-fg-subtle">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-fg">{fmtNum(value)}</div>
      {sub && <div className="mt-0.5 text-xs text-fg-muted">{sub}</div>}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-bg-subtle">
        <div className={cn("h-full", tone)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
