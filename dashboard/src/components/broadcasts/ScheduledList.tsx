import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Repeat } from "lucide-react";

import { ApiError, endpoints } from "@/lib/api";
import { fmtNum, truncate } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  EmptyFailure,
  LoadingGate,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import { RECURRENCE_LABELS, fmtMsk, type Recurrence } from "./msk";
import { segmentLabel, segmentCount, useSegments } from "./useSegments";

/**
 * Отложенные рассылки.
 *
 * ЭТОТ СПИСОК УЖЕ БЫЛ НЕДОСТИЖИМ, И СЛОМАТЬ ЕГО ЛЕГКО СНОВА. На сервере
 * `GET /broadcasts/scheduled` какое-то время объявлялся ниже
 * `GET /{broadcast_id}`, и FastAPI отдавал слово «scheduled» в числовой
 * параметр — экран получал 422 и всегда выглядел пустым. Сейчас
 * литеральные пути включаются раньше (`routes/broadcasts/__init__.py`),
 * и за этим следит `tests/services/test_broadcasts_route_split.py`.
 * Отсюда правило экрана: пустой ответ и отказ запроса обязаны выглядеть
 * по-разному. Пока они выглядели одинаково, поломка маршрута читалась
 * как «ничего не запланировано» и жила незамеченной.
 *
 * ОТМЕНА СПРАШИВАЕТ ПОДТВЕРЖДЕНИЕ, НО БЕЗ НАБОРА ЧИСЛА. Отменённое
 * задание можно создать заново из той же исходной рассылки — потери
 * нет. Набор числа приберегаем для того, что не отыграть: самой
 * отправки.
 */

interface ScheduledRow {
  id: number;
  title: string;
  segment: string;
  scheduled_at: string;
  recurrence: string;
  recurrence_end_at: string | null;
  is_active: boolean;
  run_count: number;
  last_error: string | null;
}

/** Сырая строка от сервера → строка списка. Сервер сериализует
 *  `SELECT *` без схемы, поэтому приведение делается один раз здесь. */
function toScheduledRow(raw: Record<string, unknown>): ScheduledRow {
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  const strOrNull = (v: unknown): string | null =>
    typeof v === "string" && v ? v : null;
  return {
    id: Number(raw.id) || 0,
    title: str(raw.title) || "Без названия",
    segment: str(raw.segment),
    scheduled_at: str(raw.scheduled_at),
    recurrence: str(raw.recurrence) || "once",
    recurrence_end_at: strOrNull(raw.recurrence_end_at),
    is_active: Boolean(raw.is_active),
    run_count: Number(raw.run_count) || 0,
    last_error: strOrNull(raw.last_error),
  };
}

export function ScheduledList() {
  const qc = useQueryClient();
  const [showHistory, setShowHistory] = useState(false);
  const [cancelling, setCancelling] = useState<ScheduledRow | null>(null);

  const list = useQuery({
    queryKey: ["broadcasts", "scheduled", showHistory],
    queryFn: () => endpoints.broadcastScheduleList(!showHistory, 200),
    select: (rows) => rows.map(toScheduledRow),
    refetchInterval: 30_000,
  });
  const segments = useSegments();

  const cancel = useMutation({
    mutationFn: (id: number) => endpoints.broadcastScheduleCancel(id),
    onSuccess: () => {
      toast.success("Задание отменено — рассылка не уйдёт");
      qc.invalidateQueries({ queryKey: ["broadcasts", "scheduled"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось отменить задание"),
    onSettled: () => setCancelling(null),
  });

  const rows = list.data ?? [];

  return (
    <>
      <Card>
        <CardHeader
          title="Отложенные"
          subtitle={
            list.isError
              ? "список не загрузился"
              : `${fmtNum(rows.length)} ${showHistory ? "за всё время" : "ждут отправки"} · время московское`
          }
          actions={
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-fg-muted">
              <input
                type="checkbox"
                checked={showHistory}
                onChange={(e) => setShowHistory(e.target.checked)}
                className="accent-accent-9"
              />
              Показать отработавшие
            </label>
          }
        />

        {list.isError ? (
          <div className="p-4">
            {/* «Ничего не запланировано» на упавшем запросе — та самая
                неправда, из-за которой сломанный маршрут никто не замечал. */}
            <EmptyFailure
              what="список отложенных рассылок"
              reason="Список не ответил. Это не значит, что отложенных рассылок нет, — значит, что мы не смогли их увидеть. Запланированное продолжает работать по расписанию."
              onRetry={() => list.refetch()}
            />
          </div>
        ) : (
          <LoadingGate
            loading={list.isLoading}
            skeleton={
              <div className="space-y-2 p-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-14" />
                ))}
              </div>
            }
            message="Смотрю расписание"
          >
            {rows.length === 0 ? (
              <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
                <div className="grid h-10 w-10 place-items-center rounded-lg bg-bg-subtle text-fg-subtle">
                  <CalendarClock className="h-5 w-5" aria-hidden />
                </div>
                <div className="max-w-sm">
                  <div className="text-base font-medium text-fg">
                    {showHistory
                      ? "Отложенных рассылок ещё не было"
                      : "Ничего не ждёт отправки"}
                  </div>
                  <div className="mt-1 text-base text-fg-muted">
                    Откройте любую отправленную рассылку и нажмите «Отложить» —
                    её копия уйдёт в назначенное время.
                  </div>
                </div>
              </div>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {rows.map((r) => (
                  <ScheduledRowView
                    key={r.id}
                    row={r}
                    segmentName={segmentLabel(segments.data, r.segment)}
                    segmentSize={segmentCount(segments.data, r.segment)}
                    onCancel={() => setCancelling(r)}
                    cancelling={cancel.isPending && cancel.variables === r.id}
                  />
                ))}
              </ul>
            )}
          </LoadingGate>
        )}
      </Card>

      <ConfirmDialog
        open={cancelling !== null}
        onCancel={() => setCancelling(null)}
        onConfirm={() => cancelling && cancel.mutate(cancelling.id)}
        title="Отменить отложенную рассылку"
        body={
          cancelling ? (
            <>
              «{truncate(cancelling.title, 60)}» не уйдёт{" "}
              <b className="text-fg">{fmtMsk(cancelling.scheduled_at)} МСК</b>
              {cancelling.recurrence !== "once" && <> и больше не повторится</>}
              . Ничего уже отправленного это не трогает — а задание можно
              создать заново из той же рассылки.
            </>
          ) : null
        }
        confirmLabel="Отменить задание"
        cancelLabel="Оставить"
        loading={cancel.isPending}
      />
    </>
  );
}

function ScheduledRowView({
  row,
  segmentName,
  segmentSize,
  onCancel,
  cancelling,
}: {
  row: ScheduledRow;
  segmentName: string;
  segmentSize: number | null;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const isActive = row.is_active;
  const repeats = row.recurrence !== "once";
  const runCount = row.run_count;

  return (
    <li className="flex flex-wrap items-start gap-3 px-4 py-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-bg-subtle text-fg-muted">
        {repeats ? (
          <Repeat className="h-4 w-4" aria-hidden />
        ) : (
          <CalendarClock className="h-4 w-4" aria-hidden />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-fg">{truncate(row.title, 60)}</span>
          {!isActive && (
            <StatusBadge kind="neutral">Отработала</StatusBadge>
          )}
          {repeats && (
            <StatusBadge kind="info">
              {RECURRENCE_LABELS[row.recurrence as Recurrence] ?? String(row.recurrence)}
            </StatusBadge>
          )}
          {row.last_error && <StatusBadge kind="failure">Была ошибка</StatusBadge>}
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-fg-muted">
          <span className="font-medium text-fg">{fmtMsk(row.scheduled_at)} МСК</span>
          <span>
            кому: {segmentName}
            {segmentSize != null && ` · сейчас ${fmtNum(segmentSize)} чел.`}
          </span>
          {runCount > 0 && <span>отправлено раз: {fmtNum(runCount)}</span>}
          {row.recurrence_end_at && <span>до {fmtMsk(row.recurrence_end_at)} МСК</span>}
        </div>

        {/* Текст ошибки последнего запуска — единственное место, где его
            вообще видно. Прятать его под иконку значит потерять. */}
        {row.last_error && (
          <div className="mt-1 break-words text-xs text-danger">{row.last_error}</div>
        )}
      </div>

      {isActive && (
        <Button size="sm" onClick={onCancel} loading={cancelling}>
          Отменить
        </Button>
      )}
    </li>
  );
}
