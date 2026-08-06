import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";

import { endpoints, type PurchaseRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtRelative, fmtRub } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Button,
  Card,
  CardHeader,
  Dash,
  EmptyFailure,
  LoadingGate,
  SkeletonTable,
  StatusBadge,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableScroll,
  type Density,
} from "@/components/ui";
import { isExternalMoney, providerLabel, purchaseLabel, statusMeta } from "./labels";

/**
 * Лента платежей — плотная таблица всех покупок за окно.
 *
 * ЗДЕСЬ ВИДНЫ ВСЕ СОСТОЯНИЯ, А НЕ ТОЛЬКО ОПЛАЧЕННЫЕ. Зависший «ждёт
 * оплаты» рядом с успешными — это и есть повод открыть экран: значит,
 * счёт выставлен, а подтверждение не доехало.
 *
 * БАЛАНСОВЫЕ СТРОКИ ПОМЕЧЕНЫ. Их сумма в выручку не входит, и без
 * пометки итог по ленте не сошёлся бы с числом наверху. Это не
 * оформление, это правило учёта.
 *
 * ПУСТО И ОШИБКА — РАЗНОЕ. Пустая лента говорит «за окно оплат не было»
 * и предлагает расширить окно; упавший запрос говорит, что это отказ
 * запроса, и даёт «Повторить». Раньше оба случая выглядели одинаково.
 */

export type FeedStatus = "" | "paid" | "pending" | "expired";

export function PaymentsFeed({
  hours,
  status,
  provider,
  selected,
  onSelect,
  onResetFilters,
  onWiden,
  density,
}: {
  hours: number;
  status: FeedStatus;
  provider: string;
  selected: number | null;
  onSelect: (id: number) => void;
  onResetFilters: () => void;
  onWiden: () => void;
  density: Density;
}) {
  const feed = useQuery({
    queryKey: ["payments", "feed", hours, status, provider],
    queryFn: () =>
      endpoints.paymentsRecent({
        limit: 200,
        hours,
        status: status || undefined,
        provider: provider || undefined,
      }),
    refetchInterval: 30_000,
  });

  const rows: PurchaseRow[] = feed.data ?? [];
  const filtered = status !== "" || provider !== "";

  return (
    <Card>
      <CardHeader
        title="Лента платежей"
        subtitle={
          feed.data
            ? `${fmtNum(rows.length)} строк за окно · свежие сверху`
            : "все покупки за окно, свежие сверху"
        }
        actions={
          filtered ? (
            <Button size="sm" onClick={onResetFilters}>
              Снять фильтры
            </Button>
          ) : undefined
        }
      />

      {feed.isError ? (
        <div className="p-4">
          <EmptyFailure
            what="платежи за период"
            reason="Не смогли загрузить платежи. Это отказ запроса, а не отсутствие оплат."
            onRetry={() => feed.refetch()}
          />
        </div>
      ) : (
        <LoadingGate
          loading={feed.isLoading}
          skeleton={<SkeletonTable rows={8} cols={5} className="m-4 border-0" />}
          message="Собираю платежи за период"
        >
          {/* `feed.data &&` обязателен: первую секунду LoadingGate рисует
              детей, и без проверки на ней мелькало бы «оплат не было». */}
          {feed.data && rows.length === 0 ? (
            <div className="px-4 py-10 text-center">
              <div className="text-base font-medium text-fg">
                {filtered ? "Под выбранные фильтры ничего не попало" : "За это окно оплат не было"}
              </div>
              <div className="mx-auto mt-1 max-w-sm text-base text-fg-muted">
                Запрос отработал — платежей действительно нет. Это не отказ
                загрузки: тогда на этом месте была бы красная плашка.
              </div>
              <div className="mt-3 flex justify-center gap-2">
                {filtered && <Button onClick={onResetFilters}>Снять фильтры</Button>}
                <Button onClick={onWiden}>Расширить окно</Button>
              </div>
            </div>
          ) : (
            <>
              <TableScroll className="hidden max-h-[calc(100vh-320px)] rounded-none border-0 md:block">
                <Table density={density}>
                  <THead>
                    <tr>
                      <TH>Когда</TH>
                      <TH>Кто</TH>
                      <TH>Что куплено</TH>
                      <TH numeric>Сумма</TH>
                      <TH>Оплата</TH>
                      <TH>Статус</TH>
                      <TH className="w-8" aria-label="Открыть разбор" />
                    </tr>
                  </THead>
                  <TBody>
                    {rows.map((row, i) => (
                      <FeedRow
                        key={row.id ?? i}
                        row={row}
                        first={i === 0}
                        selected={selected === row.id}
                        onSelect={onSelect}
                      />
                    ))}
                  </TBody>
                </Table>
              </TableScroll>

              <ul className="divide-y divide-border-subtle md:hidden">
                {rows.map((row, i) => (
                  <li key={row.id ?? i}>
                    <FeedCard
                      row={row}
                      selected={selected === row.id}
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

function FeedRow({
  row,
  first,
  selected,
  onSelect,
}: {
  row: PurchaseRow;
  first: boolean;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const state = statusMeta(row.status);
  const internal = !isExternalMoney(row.payment_provider);

  return (
    <TR
      interactive
      first={first}
      tone={row.status === "pending" ? "risk" : "none"}
      onActivate={() => onSelect(row.id)}
      className={cn(selected && "bg-accent-3")}
      aria-current={selected ? "true" : undefined}
    >
      <TD className="whitespace-nowrap">
        <div className="text-fg">{fmtRelative(row.created_at)}</div>
        <div className="text-2xs text-fg-subtle">{fmtDate(row.created_at)}</div>
      </TD>
      <TD>
        <div className="truncate text-fg">
          {row.username ? `@${row.username}` : `tg:${row.telegram_id ?? "—"}`}
        </div>
      </TD>
      <TD>
        <div className="truncate text-fg">{purchaseLabel(row)}</div>
        {row.promo_code && (
          <div className="truncate font-mono text-2xs text-fg-subtle">
            промокод {row.promo_code}
          </div>
        )}
      </TD>
      <TD numeric>
        <div className={internal ? "text-fg-muted" : "text-fg"}>
          {row.price_rubles == null ? <Dash /> : fmtRub(row.price_rubles)}
        </div>
        {internal && <div className="text-2xs text-fg-subtle">не выручка</div>}
      </TD>
      <TD className="whitespace-nowrap text-fg-muted">
        {providerLabel(row.payment_provider)}
      </TD>
      <TD>
        <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
      </TD>
      <TD>
        <ChevronRight className="h-3.5 w-3.5 text-fg-subtle" aria-hidden />
      </TD>
    </TR>
  );
}

function FeedCard({
  row,
  selected,
  onSelect,
}: {
  row: PurchaseRow;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const state = statusMeta(row.status);
  const internal = !isExternalMoney(row.payment_provider);
  return (
    <button
      type="button"
      onClick={() => onSelect(row.id)}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle",
        selected && "bg-accent-3",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate font-medium text-fg">
            {row.username ? `@${row.username}` : `tg:${row.telegram_id ?? "—"}`}
          </span>
          <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
        </div>
        <div className="mt-0.5 truncate text-xs text-fg-muted">{purchaseLabel(row)}</div>
        <div className="mt-0.5 text-2xs text-fg-subtle">
          {fmtDate(row.created_at)} · {providerLabel(row.payment_provider)}
          {internal && " · не выручка"}
        </div>
      </div>
      <div className="shrink-0 text-right text-base font-medium tabular-nums text-fg">
        {row.price_rubles == null ? <Dash /> : fmtRub(row.price_rubles)}
      </div>
    </button>
  );
}
