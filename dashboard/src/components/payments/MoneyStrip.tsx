import { useQuery } from "@tanstack/react-query";

import { endpoints } from "@/lib/api";
import { fmtNum, fmtRub } from "@/lib/format";
import { EmptyFailure, LoadingGate, SkeletonTile, StatTile } from "@/components/ui";

/**
 * Деньги за выбранное окно: выручка, число оплат, средний чек.
 *
 * ВЫРУЧКА — ТОЛЬКО ВНЕШНИЕ ПОСТУПЛЕНИЯ. Покупка с баланса и
 * автопродление с баланса сюда не входят: деньги посчитаны в момент
 * пополнения, второй раз их считать нельзя. Это написано прямо под
 * числом, потому что ниже в ленте балансовые строки видны, и без
 * подписи их сумма не сходится с этой.
 *
 * ОТКАЗ НЕ РИСУЕТ НОЛЬ. «0 ₽» и «не смогли посчитать» — разные вещи, и
 * первое читается как «сегодня никто не платил». При ошибке здесь
 * красная плашка и «Повторить» (ux-patterns §3.5).
 */
export function MoneyStrip({ hours }: { hours: number }) {
  const revenue = useQuery({
    queryKey: ["payments", "revenue", hours],
    queryFn: () => endpoints.paymentsRevenue(hours),
    refetchInterval: 30_000,
  });

  if (revenue.isError) {
    return (
      <EmptyFailure
        what="выручку за период"
        reason="Не смогли загрузить платежи. Это отказ запроса, а не отсутствие оплат."
        onRetry={() => revenue.refetch()}
      />
    );
  }

  const d = revenue.data;

  return (
    <LoadingGate
      loading={revenue.isLoading}
      skeleton={
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <SkeletonTile key={i} />
          ))}
        </div>
      }
      message="Считаю выручку за период"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <StatTile
          label="Выручка"
          value={fmtRub(d?.revenue_rubles)}
          tone="money-in"
          hint="внешние поступления, без покупок с баланса"
        />
        <StatTile
          label="Оплат"
          value={fmtNum(d?.payments_count)}
          hint="успешные покупки за окно"
        />
        <StatTile
          label="Средний чек"
          value={fmtRub(d?.avg_check_rubles)}
          hint="выручка, делённая на число оплат"
          className="col-span-2 lg:col-span-1"
        />
      </div>
    </LoadingGate>
  );
}
