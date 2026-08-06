import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Card,
  CardHeader,
  EmptyAllClear,
  EmptyFailure,
  LoadingGate,
  Skeleton,
} from "@/components/ui";

/**
 * Активность по часам суток — «когда покупают».
 *
 * ПЕРЕЕХАЛА СЮДА СО СВОДКИ. На главном экране почасовому разрезу не место:
 * из него не следует действие в ближайший час, а место он занимал наравне
 * с деньгами (research §9.3). Данные не выброшены — они на уровень глубже
 * (NN/g «Reduce Clutter Without Reducing Capability»).
 *
 * ЧТО ИЗМЕНИЛОСЬ ПРИ ПЕРЕЕЗДЕ. Убраны свечение под столбцами, пунктирная
 * линия из белого по белому (её просто не было видно на светлой теме) и
 * пять захардкоженных хексов мимо палитры. Столбцы теперь красятся
 * токенами, а пик выделен не только цветом, но и подписью — цвет не может
 * быть единственным носителем смысла (research §4.11).
 *
 * Столбцы рисуются div'ами, а не recharts: двадцать четыре прямоугольника
 * не стоят инициализации библиотеки. Recharts на этом экране больше не
 * загружается вовсе — из src/ ушёл последний импорт.
 *
 * АНИМАЦИИ СЕРИИ ЗДЕСЬ НЕТ, И ДОБАВЛЯТЬ ЕЁ НЕЛЬЗЯ. Требование research §7.3
 * («isAnimationActive={false}») в коде без recharts выглядит как отсутствие
 * transition на высоте столбца. Раньше стояло `transition-[height]
 * duration-300`: запрос обновляется по staleTime и при смене периода, и на
 * каждое обновление все 24 столбца ехали по высоте треть секунды. На рабочей
 * панели это мешает читать — глаз ловит движение вместо числа, а пик
 * «переползает» на соседний час прямо во время чтения. Вернёте transition —
 * вернёте дефект.
 */

const METRICS = [
  { key: "payments_count", label: "Платежи", fmt: (v: number) => fmtNum(v) },
  { key: "revenue_rubles", label: "Выручка", fmt: (v: number) => fmtRub(v) },
  { key: "new_users", label: "Регистрации", fmt: (v: number) => fmtNum(v) },
  {
    key: "new_paid_subscriptions",
    label: "Платные подписки",
    fmt: (v: number) => fmtNum(v),
  },
] as const;

type MetricKey = (typeof METRICS)[number]["key"];

const RANGES = [1, 7, 30] as const;

export function HourlyActivity() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(7);
  const [metric, setMetric] = useState<MetricKey>("payments_count");

  const hourly = useQuery({
    queryKey: ["analytics", "hourly", days],
    queryFn: () => endpoints.statsHourly(days),
    staleTime: 60_000,
  });

  const def = METRICS.find((m) => m.key === metric) ?? METRICS[0];
  const series = hourly.data?.series ?? [];
  const values = series.map((d) => Number(d[metric]) || 0);
  const max = Math.max(1, ...values);
  const total = values.reduce((a, v) => a + v, 0);
  const peakIdx = values.length ? values.indexOf(Math.max(...values)) : -1;

  return (
    <Card>
      <CardHeader
        title="Когда покупают"
        subtitle={`распределение по часам, Москва · окно ${days} дн`}
        actions={
          <div className="pill-tabs">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setDays(r)}
                className={days === r ? "pill-tab-active" : "pill-tab"}
              >
                {r}д
              </button>
            ))}
          </div>
        }
      />
      <div className="p-4">
        {hourly.isError ? (
          <EmptyFailure
            what="активность по часам"
            reason="Ряд не пришёл. Пустой график здесь означал бы, что покупок не было ни в один час."
            onRetry={() => hourly.refetch()}
          />
        ) : (
          <LoadingGate
            loading={hourly.isLoading}
            skeleton={<Skeleton className="h-44" />}
            message="Считаю активность по часам"
          >
            {series.length === 0 ? (
              // Ряд пуст — двадцать четыре нулевых столбика выглядели бы как
              // график, у которого просто маленький масштаб. Пишем словами.
              <EmptyAllClear
                title="За это окно активности не было"
                description="Запрос прошёл, но ни в один час не случилось ни одной покупки. Возьмите окно шире."
              />
            ) : (
            <>
            <div className="pill-tabs mb-4">
              {METRICS.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => setMetric(m.key)}
                  className={metric === m.key ? "pill-tab-active" : "pill-tab"}
                >
                  {m.label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
              <div className="text-2xl font-semibold text-fg">{def.fmt(total)}</div>
              {peakIdx >= 0 && (
                <div className="text-base text-fg-muted">
                  пик в{" "}
                  <span className="font-medium text-fg">
                    {String(peakIdx).padStart(2, "0")}:00
                  </span>{" "}
                  — {def.fmt(values[peakIdx])}
                </div>
              )}
            </div>

            <div className="mt-4 flex h-44 items-end gap-1">
              {series.map((d, i) => {
                const v = values[i];
                const isPeak = i === peakIdx;
                return (
                  <div
                    key={d.hour}
                    className="group relative flex h-full flex-1 flex-col justify-end"
                    title={`${String(d.hour).padStart(2, "0")}:00 — ${def.fmt(v)}`}
                  >
                    <div
                      className={cn(
                        "w-full rounded-t-sm",
                        isPeak ? "bg-accent-9" : "bg-accent-6",
                      )}
                      style={{ height: `${Math.max(2, (v / max) * 100)}%` }}
                    />
                  </div>
                );
              })}
            </div>
            <div className="mt-2 flex justify-between text-2xs tabular-nums text-fg-subtle">
              {["00", "06", "12", "18", "23"].map((h) => (
                <span key={h}>{h}</span>
              ))}
            </div>
            </>
            )}
          </LoadingGate>
        )}
      </div>
    </Card>
  );
}
