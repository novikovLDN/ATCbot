import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";

import { endpoints, type ReferrerRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Card,
  CardHeader,
  EmptyFailure,
  EmptyFilter,
  LoadingGate,
  SkeletonTable,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
  type Density,
} from "@/components/ui";

/**
 * Лидерборд партнёров.
 *
 * СОРТИРОВКА СЧИТАЕТСЯ НА СЕРВЕРЕ. Клиент получает не больше пятидесяти
 * строк; отсортируй он их у себя — «топ по доходу» означал бы «топ среди
 * первых пятидесяти по другому признаку», то есть неправду.
 *
 * ДЕНЬГИ ПРИХОДЯТ В РУБЛЯХ. Слой базы делит копейки на сто на выходе
 * (database/referral_analytics.py). Второй делёж здесь дал бы сотую долю
 * настоящей суммы, и заметить это по экрану невозможно — число просто
 * другое.
 */

export type ReferrerSort = "total_revenue" | "invited_count" | "cashback_paid";

export const SORT_LABEL: Record<ReferrerSort, string> = {
  total_revenue: "по доходу",
  invited_count: "по числу приглашённых",
  cashback_paid: "по выплаченному кешбэку",
};

export function ReferrersTable({
  sort,
  query,
  selected,
  onSelect,
  onResetFilters,
  density,
}: {
  sort: ReferrerSort;
  query: string;
  selected: number | null;
  onSelect: (id: number) => void;
  onResetFilters: () => void;
  density: Density;
}) {
  const list = useQuery({
    queryKey: ["referrals", "top", sort, query],
    queryFn: () =>
      endpoints.referralsTop({
        sort_by: sort,
        sort_order: "DESC",
        limit: 50,
        q: query || undefined,
      }),
    staleTime: 30_000,
  });

  const rows = list.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Партнёры"
        subtitle={`первые ${rows.length || 50} ${SORT_LABEL[sort]}`}
      />

      {list.isError ? (
        <div className="p-4">
          <EmptyFailure
            what="список партнёров"
            reason="Список не пришёл. Пустая таблица здесь читалась бы как «партнёров нет» — это не так, это отказ запроса."
            onRetry={() => list.refetch()}
          />
        </div>
      ) : (
        <LoadingGate
          loading={list.isLoading}
          skeleton={<SkeletonTable rows={8} cols={5} className="m-4 border-0" />}
          message="Считаю доход по партнёрам"
        >
          {/* `list.data &&` обязателен: первую секунду рисуются дети, а не
              скелетон, и без проверки мелькало бы «партнёров нет». */}
          {list.data && rows.length === 0 ? (
            <div className="p-4">
              {query ? (
                <EmptyFilter query={query} onReset={onResetFilters} />
              ) : (
                <div className="py-8 text-center text-base text-fg-muted">
                  Никто ещё никого не пригласил.
                </div>
              )}
            </div>
          ) : (
            <>
              <TableScroll className="hidden max-h-[calc(100vh-300px)] rounded-none border-0 md:block">
                <Table density={density}>
                  <THead>
                    <tr>
                      <TH className="w-10">№</TH>
                      <TH>Партнёр</TH>
                      <TH numeric>Пригласил</TH>
                      <TH numeric>Купили</TH>
                      <TH numeric>Доход</TH>
                      <TH numeric>Кешбэк</TH>
                      <TH className="w-8" aria-label="Открыть" />
                    </tr>
                  </THead>
                  <TBody>
                    {rows.map((row, i) => (
                      <ReferrerTableRow
                        key={row.referrer_id}
                        row={row}
                        place={i + 1}
                        first={i === 0}
                        selected={selected === row.referrer_id}
                        onSelect={onSelect}
                      />
                    ))}
                  </TBody>
                </Table>
              </TableScroll>

              <ul className="divide-y divide-border-subtle md:hidden">
                {rows.map((row, i) => (
                  <li key={row.referrer_id}>
                    <ReferrerCardRow
                      row={row}
                      place={i + 1}
                      selected={selected === row.referrer_id}
                      onSelect={onSelect}
                    />
                  </li>
                ))}
              </ul>
            </>
          )}
        </LoadingGate>
      )}
    </Card>
  );
}

function name(row: ReferrerRow): string {
  return row.username ? `@${row.username}` : `tg:${row.referrer_id}`;
}

function ReferrerTableRow({
  row,
  place,
  first,
  selected,
  onSelect,
}: {
  row: ReferrerRow;
  place: number;
  first: boolean;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  return (
    <TR
      interactive
      first={first}
      onActivate={() => onSelect(row.referrer_id)}
      className={cn(selected && "bg-accent-3")}
      aria-current={selected ? "true" : undefined}
    >
      <TD className="tabular-nums text-fg-subtle">{place}</TD>
      <TD>
        <div className="truncate font-medium text-fg">{name(row)}</div>
        <div className="text-2xs text-fg-subtle">
          {row.first_referral_date ? `с ${fmtDate(row.first_referral_date)}` : "—"}
        </div>
      </TD>
      <TD numeric>{fmtNum(row.invited_count)}</TD>
      <TD numeric>{fmtNum(row.paid_count)}</TD>
      <TD numeric>{fmtRub(row.total_invited_revenue)}</TD>
      <TD numeric>{fmtRub(row.total_cashback_paid)}</TD>
      <TD>
        <ChevronRight className="h-3.5 w-3.5 text-fg-subtle" aria-hidden />
      </TD>
    </TR>
  );
}

function ReferrerCardRow({
  row,
  place,
  selected,
  onSelect,
}: {
  row: ReferrerRow;
  place: number;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(row.referrer_id)}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle",
        selected && "bg-accent-3",
      )}
    >
      <span className="mt-0.5 w-5 shrink-0 tabular-nums text-xs text-fg-subtle">{place}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-fg">{name(row)}</div>
        <div className="mt-0.5 text-xs text-fg-muted">
          пригласил {fmtNum(row.invited_count)} · купили {fmtNum(row.paid_count)}
        </div>
        <div className="mt-0.5 text-2xs text-fg-subtle">
          доход {fmtRub(row.total_invited_revenue)} · кешбэк{" "}
          {fmtRub(row.total_cashback_paid)}
        </div>
      </div>
      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
    </button>
  );
}
