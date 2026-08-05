import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { Card, EmptyFailure, LoadingGate, SkeletonTile } from "@/components/ui";
import { Sparkline } from "./Sparkline";

/**
 * ЗОНА A — деньги. Единственное крупное число на экране.
 *
 * Показываем выручку за сегодня, а не «доход за всё время»: сумма с начала
 * времён только растёт, из неё ничего не следует, и место она занимает
 * главное (research §8.6). «Всего юзеров» убрано отсюда по той же причине.
 *
 * СРАВНЕНИЕ ЧЕСТНОЕ ПО ВРЕМЕНИ СУТОК. Вчерашний день берётся не целиком, а
 * ровно до того же часа. Иначе в одиннадцать утра сравнение показывало бы
 * падение всегда, и на него перестали бы смотреть. Подпись под процентом
 * прямо называет час, чтобы это не приходилось угадывать.
 *
 * ОШИБКА НЕ ВЫГЛЯДИТ КАК НОЛЬ. Зона A — одно число, показать его частично
 * нечем, поэтому при отказе здесь не «0 ₽», а текст «не смогли посчитать»
 * и кнопка «Повторить». Виджет держит свои габариты, остальной экран
 * работает (ux-patterns §3.5).
 */

/** Разница в процентах. null, когда вчера в это время не было ничего:
 *  делить на ноль нельзя, а «+∞ %» — не число, из которого что-то следует. */
function deltaPercent(today: number, yesterday: number): number | null {
  if (yesterday <= 0) return null;
  return ((today - yesterday) / yesterday) * 100;
}

/** «к 11:12» — час, до которого досчитан вчерашний день. */
function elapsedClock(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function MoneyToday() {
  const money = useQuery({
    queryKey: ["summary", "money"],
    queryFn: () => endpoints.summaryMoney(30),
    refetchInterval: 30_000,
  });

  if (money.isError) {
    return (
      <EmptyFailure
        what="выручку за сегодня"
        reason="Сервер не отдал деньги за сутки. Это отказ запроса, а не нулевые продажи."
        onRetry={() => money.refetch()}
      />
    );
  }

  const d = money.data;

  return (
    <LoadingGate
      loading={money.isLoading}
      skeleton={<SkeletonTile className="h-[168px]" />}
      message="Считаю выручку за сегодня"
    >
      <Card className="p-5 md:p-6">
        <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <div className="text-2xs font-medium uppercase tracking-[0.08em] text-fg-subtle">
              Выручка сегодня · Москва
            </div>
            <div className="mt-2 text-3xl font-semibold text-fg md:text-4xl">
              {fmtRub(d?.today_rubles)}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              {d && <DeltaChip data={d} />}
              <span className="text-base text-fg-muted">
                {fmtNum(d?.today_payments)} оплат
              </span>
            </div>
          </div>

          <div className="w-full md:w-[280px] md:shrink-0">
            <Sparkline
              points={d?.sparkline ?? []}
              label={
                d
                  ? `Выручка по дням за ${d.sparkline.length} суток, последнее значение ${fmtRub(d.today_rubles)}`
                  : "Выручка по дням"
              }
            />
            <div className="mt-1.5 flex justify-between text-2xs text-fg-subtle">
              <span>{d ? `${d.sparkline.length} дней` : "—"}</span>
              <span>сегодня</span>
            </div>
          </div>
        </div>
      </Card>
    </LoadingGate>
  );
}

/**
 * Сравнение со вчерашним днём. Цвет здесь не единственный носитель смысла:
 * рядом всегда стоит стрелка и слово (research §4.11) — на монохромном
 * экране и при красно-зелёной дальтонизации значок читается так же.
 */
function DeltaChip({
  data,
}: {
  data: {
    today_rubles: number;
    yesterday_same_time_rubles: number;
    elapsed_minutes: number;
  };
}) {
  const pct = deltaPercent(data.today_rubles, data.yesterday_same_time_rubles);
  const clock = elapsedClock(data.elapsed_minutes);
  const yesterday = fmtRub(data.yesterday_same_time_rubles);

  if (pct === null) {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm bg-bg-subtle px-1.5 py-0.5 text-xs font-medium text-fg-muted">
        <Minus className="h-3 w-3 shrink-0" aria-hidden />
        Вчера к {clock} не было оплат
      </span>
    );
  }

  const up = pct >= 0;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={
        up
          ? "inline-flex items-center gap-1 rounded-sm bg-success/12 px-1.5 py-0.5 text-xs font-medium text-success"
          : "inline-flex items-center gap-1 rounded-sm bg-risk/12 px-1.5 py-0.5 text-xs font-medium text-risk"
      }
      title={`Вчера к ${clock} было ${yesterday}`}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      {up ? "+" : "−"}
      {Math.abs(pct).toFixed(1)}% к вчерашнему на {clock}
    </span>
  );
}
