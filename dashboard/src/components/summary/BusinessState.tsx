import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, ChevronRight } from "lucide-react";

import { endpoints, type SummaryMetric } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Card, EmptyFailure, LoadingGate, Skeleton } from "@/components/ui";

/**
 * ЗОНА B — состояние бизнеса. Ровно четыре числа, ни одним больше.
 *
 * Отбор не про полноту, а про действие: у каждого числа есть ответ на
 * вопрос «и что мне с этим делать». Поэтому плитка — ссылка, а не текст:
 * клик ведёт в тот самый отфильтрованный список, из которого число
 * получено (research §9.3).
 *
 * ЧИСЛО И СПИСОК ОБЯЗАНЫ СОВПАДАТЬ. Плитки «активные» и «истекают» ведут
 * на /users?filter=…, и там работает тот же отбор, что на бэкенде считает
 * число (database/dashboard_summary.py). Поменяете условие в одном месте —
 * человек увидит 412 на плитке и 380 строк в списке.
 *
 * ОТКАЗ ОДНОГО ЧИСЛА НЕ ГАСИТ ОСТАЛЬНЫЕ ТРИ. Каждое приходит со своей
 * пометкой: value=null плюс error. Такая плитка рисует прочерк и слова
 * «не смогли посчитать», а не ноль — ноль читался бы как настоящий.
 */

interface TileSpec {
  key: keyof BusinessMetrics;
  label: string;
  /** Куда ведёт клик. Адрес обязан открывать отфильтрованный список. */
  to: string;
  /** Пояснение под числом, когда оно посчиталось. */
  hint: string;
  /** Значение, начиная с которого число само по себе — повод посмотреть. */
  alarmAbove?: number;
}

interface BusinessMetrics {
  active_subscriptions: SummaryMetric;
  expiring_7d: SummaryMetric;
  failed_payments_24h: SummaryMetric;
  panel_mismatches: SummaryMetric;
}

const TILES: TileSpec[] = [
  {
    key: "active_subscriptions",
    label: "Активные подписки",
    to: "/users?filter=active",
    hint: "платные, без триалов",
  },
  {
    key: "expiring_7d",
    label: "Истекают за 7 дней",
    to: "/users?filter=expiring_7d",
    hint: "успеть продлить",
  },
  {
    key: "failed_payments_24h",
    label: "Сорвалось оплат за 24 ч",
    to: "/payments?focus=errors",
    hint: "вебхуки и отказы провайдеров",
    alarmAbove: 0,
  },
  {
    key: "panel_mismatches",
    label: "Расхождений с панелью",
    to: "/service?focus=reconciliation",
    hint: "срок в Remnawave больше выданного",
    alarmAbove: 0,
  },
];

export function BusinessState() {
  const business = useQuery({
    queryKey: ["summary", "business"],
    queryFn: endpoints.summaryBusiness,
    refetchInterval: 60_000,
  });

  if (business.isError) {
    return (
      <EmptyFailure
        what="состояние бизнеса"
        reason="Ни одно из четырёх чисел не пришло. Похоже на обрыв связи с сервером, а не на пустую базу."
        onRetry={() => business.refetch()}
      />
    );
  }

  const metrics = business.data?.metrics;

  return (
    <LoadingGate
      loading={business.isLoading}
      skeleton={
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {TILES.map((t) => (
            <Card key={t.key} className="p-4">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="mt-2.5 h-7 w-16" />
              <Skeleton className="mt-2.5 h-3 w-20" />
            </Card>
          ))}
        </div>
      }
      message="Считаю подписки и сбои"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {TILES.map((spec) => (
          <MetricTile
            key={spec.key}
            spec={spec}
            metric={metrics?.[spec.key]}
            onRetry={() => business.refetch()}
          />
        ))}
      </div>
    </LoadingGate>
  );
}

function MetricTile({
  spec,
  metric,
  onRetry,
}: {
  spec: TileSpec;
  metric?: SummaryMetric;
  onRetry: () => void;
}) {
  // Число не посчиталось. Показываем это словами и даём повтор прямо
  // здесь: остальные плитки при этом остаются рабочими.
  if (metric && metric.value === null) {
    return (
      <Card className="border-risk/40 p-4">
        <div className="flex items-center gap-1.5 text-xs font-medium text-fg-subtle">
          <AlertTriangle className="h-3 w-3 shrink-0 text-risk" aria-hidden />
          {spec.label}
        </div>
        <div className="mt-1 text-2xl font-semibold text-fg-subtle">—</div>
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 text-xs text-accent-text underline-offset-2 hover:underline"
        >
          Не смогли посчитать. Повторить
        </button>
      </Card>
    );
  }

  const value = metric?.value ?? null;
  const alarm =
    spec.alarmAbove !== undefined && value !== null && value > spec.alarmAbove;

  return (
    <Link
      to={spec.to}
      className={cn(
        "group block rounded-lg border bg-bg-card p-4 transition-colors",
        alarm ? "border-risk/40 hover:border-risk" : "border-border hover:border-border-strong",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-fg-subtle">
          {spec.label}
        </span>
        <ChevronRight
          className="h-3.5 w-3.5 shrink-0 text-fg-subtle transition-transform group-hover:translate-x-0.5"
          aria-hidden
        />
      </div>
      <div className={cn("mt-1 text-2xl font-semibold", alarm ? "text-risk" : "text-fg")}>
        {fmtNum(value)}
      </div>
      <div className="mt-1 truncate text-xs text-fg-muted">{spec.hint}</div>
    </Link>
  );
}
