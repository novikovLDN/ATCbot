import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import { Button, Modal } from "@/components/ui";
import {
  RECURRENCE_LABELS,
  WEEKDAYS,
  mskInputValue,
  snapToWeekday,
  toApiMsk,
  weekdayOf,
  type Recurrence,
} from "./msk";
import { groupSegments, segmentCount, useSegments } from "./useSegments";

/**
 * Отложить рассылку: та же самая, но в назначенное время.
 *
 * ЧТО СОЗДАЁТСЯ. Клон исходной рассылки — текст, медиа, кнопки, скидка.
 * Сегмент можно переназначить, и это главное поле окна: повторить
 * удачную рассылку обычно хотят на другой аудитории.
 *
 * ЧИСЛО ПОЛУЧАТЕЛЕЙ ЗДЕСЬ СПРАВОЧНОЕ, И ТАК И НАПИСАНО. Сервер считает
 * аудиторию в момент отправки, а не сейчас: за сутки до запуска состав
 * сегмента «истекает через 3 дня» поменяется целиком. Обещать точное
 * число за неделю вперёд было бы враньём, поэтому подтверждения с
 * вводом числа тут нет — оно есть там, где число настоящее, на самой
 * отправке.
 */

const MAX_WEEKS_AHEAD = 4;

export function ScheduleModal({
  broadcastId,
  open,
  onClose,
}: {
  broadcastId: number;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const segments = useSegments();

  // +1 час по умолчанию: «сейчас» для отложенной не имеет смысла, а
  // круглый час — самое частое, что выбирают руками.
  const [at, setAt] = useState(() => mskInputValue(60));
  const [recurrence, setRecurrence] = useState<Recurrence>("once");
  const [endEnabled, setEndEnabled] = useState(false);
  const [endAt, setEndAt] = useState(() => mskInputValue(60 * 24 * 7));
  /** null — оставить сегмент исходной рассылки. */
  const [segment, setSegment] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      endpoints.broadcastScheduleCreate({
        source_broadcast_id: broadcastId,
        scheduled_at_msk: toApiMsk(at),
        recurrence,
        recurrence_end_at_msk:
          recurrence !== "once" && endEnabled ? toApiMsk(endAt) : null,
        segment,
      }),
    onSuccess: (data) => {
      toast.success(
        `Запланировано на ${data.scheduled_at_msk.slice(0, 16).replace("T", " ")} МСК`,
      );
      qc.invalidateQueries({ queryKey: ["broadcasts", "scheduled"] });
      onClose();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось запланировать"),
  });

  const currentWeekday = weekdayOf(at);
  const chosenCount = segment ? segmentCount(segments.data, segment) : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Отложить рассылку №${broadcastId}`}
      description="Уйдёт копия: тот же текст, медиа, кнопки и скидка."
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={() => create.mutate()}
            disabled={!at}
            loading={create.isPending}
          >
            Запланировать
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="Когда, по Москве"
          hint={`Не раньше чем через минуту и не позже чем через ${MAX_WEEKS_AHEAD} недели.`}
        >
          <input
            type="datetime-local"
            value={at}
            min={mskInputValue(1)}
            max={mskInputValue(60 * 24 * 7 * MAX_WEEKS_AHEAD)}
            onChange={(e) => setAt(e.target.value)}
            className="h-9 w-full rounded-md border border-border-control bg-bg-card px-2 text-base text-fg outline-none focus-visible:border-accent-9"
          />
        </Field>

        <Field label="Повторять">
          <div className="grid gap-1.5 sm:grid-cols-2">
            {(Object.keys(RECURRENCE_LABELS) as Recurrence[]).map((r) => (
              <label
                key={r}
                className={cn(
                  "flex min-h-tap cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 text-base transition-colors",
                  recurrence === r
                    ? "border-accent-9 bg-accent-3 text-accent-12"
                    : "border-border-control bg-bg-card text-fg hover:bg-bg-subtle",
                )}
              >
                <input
                  type="radio"
                  name="recurrence"
                  checked={recurrence === r}
                  onChange={() => setRecurrence(r)}
                  className="accent-accent-9"
                />
                {RECURRENCE_LABELS[r]}
              </label>
            ))}
          </div>
        </Field>

        {recurrence === "weekly" && (
          <Field
            label="День недели"
            hint="Клик двигает первую отправку на ближайший этот день, время сохраняется. Дальше — каждые семь дней."
          >
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((w) => (
                <button
                  key={w.js}
                  type="button"
                  aria-pressed={currentWeekday === w.js}
                  onClick={() => setAt((cur) => snapToWeekday(cur, w.js))}
                  title={`Перенести на ближайшую ${w.long}`}
                  className={cn(
                    "min-h-tap rounded-md border px-3 py-1 text-base transition-colors",
                    currentWeekday === w.js
                      ? "border-accent-9 bg-accent-3 font-medium text-accent-12"
                      : "border-border-control bg-bg-card text-fg-muted hover:bg-bg-subtle hover:text-fg",
                  )}
                >
                  {w.short}
                </button>
              ))}
            </div>
          </Field>
        )}

        {recurrence !== "once" && (
          <Field label="Когда прекратить повторы">
            <label className="flex min-h-tap cursor-pointer items-center gap-2 text-base text-fg">
              <input
                type="checkbox"
                checked={endEnabled}
                onChange={(e) => setEndEnabled(e.target.checked)}
                className="accent-accent-9"
              />
              Остановить в назначенный день
            </label>
            {endEnabled ? (
              <input
                type="datetime-local"
                value={endAt}
                onChange={(e) => setEndAt(e.target.value)}
                className="mt-2 h-9 w-full rounded-md border border-border-control bg-bg-card px-2 text-base text-fg outline-none focus-visible:border-accent-9"
              />
            ) : (
              <div className="mt-1 text-xs text-fg-muted">
                Без даты окончания рассылка будет повторяться, пока её не
                отменят вручную.
              </div>
            )}
          </Field>
        )}

        <Field
          label="Кому"
          hint="Состав сегмента считается в момент отправки. Число рядом — сколько человек в нём сейчас, для прикидки."
        >
          <select
            value={segment ?? ""}
            onChange={(e) => setSegment(e.target.value || null)}
            disabled={segments.isLoading}
            className="h-9 w-full rounded-md border border-border-control bg-bg-card px-2 text-base text-fg outline-none focus-visible:border-accent-9"
          >
            <option value="">
              {segments.isLoading
                ? "Считаю сегменты…"
                : "Как в исходной рассылке"}
            </option>
            {groupSegments(segments.data).map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.items.map((s) => {
                  const n = segmentCount(segments.data, s.key);
                  return (
                    <option key={s.key} value={s.key}>
                      {s.label} — {n == null ? "размер неизвестен" : `${fmtNum(n)} чел.`}
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
          {segment && chosenCount === 0 && (
            <div className="mt-1.5 text-xs text-danger">
              Сейчас в этом сегменте никого. Если к моменту отправки никто не
              появится, задание отработает вхолостую.
            </div>
          )}
          {segments.isError && (
            <div className="mt-1.5 text-xs text-danger">
              Список сегментов не загрузился — можно оставить сегмент исходной
              рассылки.
            </div>
          )}
        </Field>
      </div>
    </Modal>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 text-base font-medium text-fg">{label}</div>
      {children}
      {hint && <div className="mt-1.5 text-xs text-fg-muted">{hint}</div>}
    </div>
  );
}
