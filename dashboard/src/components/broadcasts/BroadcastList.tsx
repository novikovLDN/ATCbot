import type { UseQueryResult } from "@tanstack/react-query";
import { ChevronRight, Megaphone } from "lucide-react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/cn";
import { fmtDate, fmtNum, truncate } from "@/lib/format";
import {
  Card,
  CardHeader,
  EmptyFailure,
  LoadingGate,
  Skeleton,
  Spinner,
} from "@/components/ui";
import { SendProgressBar } from "./SendProgressBar";
import type { SendProgress } from "./useSendProgress";
import { segmentLabel, useSegments } from "./useSegments";

/**
 * Список отправленных рассылок.
 *
 * ПОКАЗЫВАЕМ РОВНО ТО, ЧТО ПРИСЫЛАЕТ СЕРВЕР. `GET /broadcasts/recent`
 * отдаёт шесть полей: id, title, segment, created_at, sent_count,
 * has_msg_ids. Прежняя версия этого списка рисовала ещё бейдж «A/B»,
 * тип рассылки, отрывок текста и число ошибок — ни одного из этих полей
 * в ответе нет, и все они молча превращались в пустоту. Полю, которого
 * нет в контракте, здесь не место: пустой бейдж читается как «A/B-теста
 * не было», хотя на самом деле «мы не спрашивали».
 *
 * Подробности — в карточке справа, она ходит за ними отдельным запросом.
 */

export interface BroadcastRow {
  id: number;
  title: string;
  segment: string;
  created_at: string;
  sent_count: number;
  /** Сколько сообщений сохранили message_id — столько можно удалить из
   *  чатов. Ноль означает «удалять нечего», и кнопка удаления в карточке
   *  на это опирается. */
  has_msg_ids: number;
}

/**
 * Сырая строка от сервера → строка списка.
 *
 * Сервер отдаёт `Record<string, unknown>`: колонки собираются из
 * `SELECT` и сериализуются без схемы. Приводим здесь, один раз, вместо
 * того чтобы разбирать `unknown` в каждой ячейке разметки — иначе
 * `String(b.title ?? "…")` расползается по файлу и любое переименование
 * колонки на сервере проходит незамеченным до самого экрана.
 */
export function toBroadcastRow(raw: Record<string, unknown>): BroadcastRow {
  const num = (v: unknown): number => {
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  return {
    id: num(raw.id),
    title: typeof raw.title === "string" && raw.title ? raw.title : "Без названия",
    segment: typeof raw.segment === "string" ? raw.segment : "",
    created_at: typeof raw.created_at === "string" ? raw.created_at : "",
    sent_count: num(raw.sent_count),
    has_msg_ids: num(raw.has_msg_ids),
  };
}

export function BroadcastList({
  query,
  selected,
  onSelect,
  progress,
}: {
  query: UseQueryResult<BroadcastRow[]>;
  selected: number | null;
  onSelect: (id: number) => void;
  progress: Record<number, SendProgress>;
}) {
  const segments = useSegments();
  const rows = query.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Отправленные"
        subtitle={
          query.isError ? "список не загрузился" : `${fmtNum(rows.length)} шт., новые сверху`
        }
        actions={query.isFetching && !query.isLoading ? <Spinner /> : undefined}
      />

      {query.isError ? (
        <div className="p-4">
          <EmptyFailure
            what="список рассылок"
            reason="Список не ответил. Отправленные рассылки никуда не делись — мы просто не смогли их прочитать."
            onRetry={() => query.refetch()}
          />
        </div>
      ) : (
        <LoadingGate
          loading={query.isLoading}
          skeleton={
            <div className="space-y-2 p-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          }
          message="Читаю список рассылок"
        >
          {rows.length === 0 ? (
            // Первый запуск, а не «ничего не нашлось»: фильтров на этом
            // экране нет, поэтому пустота значит ровно одно.
            <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
              <div className="grid h-10 w-10 place-items-center rounded-lg bg-bg-subtle text-fg-subtle">
                <Megaphone className="h-5 w-5" aria-hidden />
              </div>
              <div className="max-w-sm">
                <div className="text-base font-medium text-fg">
                  Рассылок ещё не было
                </div>
                <div className="mt-1 text-base text-fg-muted">
                  Первая появится здесь сразу после отправки — вместе с тем,
                  скольким людям она дошла.
                </div>
              </div>
              <Link
                to="/broadcasts/new"
                className="inline-flex min-h-tap items-center rounded-md bg-accent-9 px-3 text-base font-medium text-white transition-colors hover:bg-accent-10"
              >
                Собрать рассылку
              </Link>
            </div>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {rows.map((b) => (
                <li key={b.id}>
                  <Row
                    row={b}
                    segmentName={segmentLabel(segments.data, b.segment)}
                    selected={selected === b.id}
                    onSelect={() => onSelect(b.id)}
                    progress={progress[b.id]}
                  />
                </li>
              ))}
            </ul>
          )}
        </LoadingGate>
      )}
    </Card>
  );
}

function Row({
  row,
  segmentName,
  selected,
  onSelect,
  progress,
}: {
  row: BroadcastRow;
  segmentName: string;
  selected: boolean;
  onSelect: () => void;
  progress?: SendProgress;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors",
        selected ? "bg-accent-3" : "hover:bg-bg-subtle",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-medium text-fg">{truncate(row.title, 60)}</span>
          <span className="text-xs text-fg-subtle">№{row.id}</span>
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-fg-muted">
          <span>{fmtDate(row.created_at)}</span>
          <span>кому: {segmentName}</span>
          {/* «Дошло» — не то же, что «получателей»: сервер в этом списке
              считает только успешные доставки. Точный состав — в карточке. */}
          <span className="tabular-nums">дошло {fmtNum(row.sent_count)}</span>
        </div>

        {progress && (
          <div className="mt-2">
            <SendProgressBar progress={progress} compact />
          </div>
        )}
      </div>

      <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
    </button>
  );
}
