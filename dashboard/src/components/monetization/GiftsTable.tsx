import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";

import { endpoints, type GiftLinkRow } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  Card,
  CardHeader,
  EmptyFailure,
  EmptyFilter,
  EmptyFirstRun,
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
import { UsageMeter } from "./UsageMeter";
import { giftState } from "./labels";

/**
 * Список подарочных ссылок на ГБ обхода.
 *
 * ССЫЛКА — ЭТО РАСХОД. Каждая активация выдаёт гигабайты, за которые уже
 * заплачено провайдеру. Поэтому в строке стоит не «максимум 100», а
 * «активаций 97 из 100» и «истекает через два дня»: из первого не следует
 * ничего, из второго — «пора продлевать или пора закрывать».
 *
 * УДАЛЁННЫЕ ССЫЛКИ ПОКАЗЫВАЮТСЯ ОТДЕЛЬНЫМ ОТБОРОМ. Удаление здесь мягкое:
 * ссылка перестаёт работать, а выданные по ней гигабайты остаются у людей
 * и в статистике. Прятать такие строки совсем — значит терять след.
 */

export type GiftFilter = "active" | "all";

export function GiftsTable({
  filter,
  selected,
  onSelect,
  onResetFilters,
  onCreate,
  density,
}: {
  filter: GiftFilter;
  selected: number | null;
  onSelect: (id: number) => void;
  onResetFilters: () => void;
  onCreate: () => void;
  density: Density;
}) {
  // include_deleted переключает сам запрос, а не фильтрацию на клиенте:
  // отсутствующие в ответе строки нечем отфильтровать.
  const list = useQuery({
    queryKey: ["bgift", "list", filter],
    queryFn: () => endpoints.bgiftList(0, 100, filter === "all"),
    refetchInterval: 60_000,
  });

  const rows = list.data ?? [];

  return (
    <Card>
      <CardHeader
        title={filter === "all" ? "Все ссылки" : "Действующие ссылки"}
        subtitle={
          list.data
            ? `${fmtNum(rows.length)} · новые сверху`
            : "новые сверху"
        }
      />

      {list.isError ? (
        <div className="p-4">
          <EmptyFailure
            what="список подарочных ссылок"
            reason="Список не пришёл. Пустая таблица здесь читалась бы как «ссылок нет» — это не так, это отказ запроса."
            onRetry={() => list.refetch()}
          />
        </div>
      ) : (
        <LoadingGate
          loading={list.isLoading}
          skeleton={<SkeletonTable rows={6} cols={5} className="m-4 border-0" />}
          message="Считаю активации подарочных ссылок"
        >
          {/* `list.data &&` обязателен: первую секунду рисуются дети, а не
              скелетон, и без проверки мелькало бы «ссылок ещё нет». */}
          {list.data && rows.length === 0 ? (
            <div className="p-4">
              {filter === "active" ? (
                <EmptyFilter query="действующие" onReset={onResetFilters} />
              ) : (
                <EmptyFirstRun
                  title="Подарочных ссылок ещё нет"
                  description="По такой ссылке человек получает гигабайты обхода — их можно раздать в поддержке, в рассылке или за отзыв. У ссылки есть срок и предел активаций, оба видны в списке."
                  actionLabel="Создать первую ссылку"
                  onAction={onCreate}
                />
              )}
            </div>
          ) : (
            <>
              <TableScroll className="hidden max-h-[calc(100vh-320px)] rounded-none border-0 md:block">
                <Table density={density}>
                  <THead>
                    <tr>
                      <TH>Код</TH>
                      <TH numeric>Выдаёт</TH>
                      <TH>Активаций и срок</TH>
                      <TH>Состояние</TH>
                      <TH className="w-8" aria-label="Открыть" />
                    </tr>
                  </THead>
                  <TBody>
                    {rows.map((row, i) => (
                      <GiftTableRow
                        key={row.id}
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
                {rows.map((row) => (
                  <li key={row.id}>
                    <GiftCardRow
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

function GiftTableRow({
  row,
  first,
  selected,
  onSelect,
}: {
  row: GiftLinkRow;
  first: boolean;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const state = giftState(row);
  return (
    <TR
      interactive
      first={first}
      tone={state.kind === "risk" ? "risk" : "none"}
      onActivate={() => onSelect(row.id)}
      className={cn(selected && "bg-accent-3")}
      aria-current={selected ? "true" : undefined}
    >
      <TD>
        <span className="font-mono text-base text-fg">{row.code}</span>
        <div className="text-2xs text-fg-subtle">создана {fmtDate(row.created_at)}</div>
      </TD>
      <TD numeric>
        <span className="font-semibold tabular-nums text-fg">{fmtNum(row.gb_amount)} ГБ</span>
      </TD>
      <TD>
        <UsageMeter
          used={row.redemption_count}
          max={row.max_uses}
          noun="активаций"
          expiresAt={row.expires_at}
          className="min-w-[190px]"
        />
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

function GiftCardRow({
  row,
  selected,
  onSelect,
}: {
  row: GiftLinkRow;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const state = giftState(row);
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
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-base text-fg">{row.code}</span>
          <span className="font-semibold tabular-nums text-fg">{fmtNum(row.gb_amount)} ГБ</span>
          <StatusBadge kind={state.kind}>{state.label}</StatusBadge>
        </div>
        <div className="mt-1">
          <UsageMeter
            used={row.redemption_count}
            max={row.max_uses}
            noun="активаций"
            expiresAt={row.expires_at}
          />
        </div>
      </div>
      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
    </button>
  );
}
