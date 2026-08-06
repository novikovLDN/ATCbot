import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";

import { ApiError, endpoints } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { toast } from "@/store/toast";
import {
  Button,
  ConfirmDialog,
  Input,
  LoadingGate,
  Modal,
  Skeleton,
  StatTile,
} from "@/components/ui";
import { MessagePreview } from "./MessagePreview";
import { groupSegments, segmentCount, useSegments } from "./useSegments";
import type { NotifRow } from "./NotificationRow";

/**
 * Правка автоуведомления: текст, окно отправки, дополнительный фильтр.
 *
 * ПОЧЕМУ ЗДЕСЬ ЕСТЬ ПРЕДПРОСМОТР. Автоуведомление уходит без участия
 * человека и по многу раз в сутки — то есть ошибка в разметке
 * тиражируется молча, пока кто-нибудь не пожалуется. Экран правки
 * обязан показывать то же, что увидит получатель, ровно по той же
 * причине, что и мастер рассылки.
 *
 * «ОТПРАВИТЬ СЕБЕ» БЕРЁТ СОХРАНЁННЫЙ ТЕКСТ, А НЕ ТОТ, ЧТО В ПОЛЕ.
 * Сервер читает уведомление из базы (`test_send_notification`). Прежняя
 * подсказка обещала обратное — «отправить текущий текст в TEXTAREA», —
 * и человек проверял не то, что правил. Теперь кнопка недоступна, пока
 * есть несохранённые правки, и об этом написано.
 *
 * УДАЛЕНИЕ ЕСТЬ ТОЛЬКО У СОЗДАННЫХ РУКАМИ. Зашитые в код сервер удалять
 * не даёт (вернёт 400), поэтому кнопки у них нет вовсе — недоступная
 * кнопка без объяснения хуже отсутствующей.
 */

export function NotificationEditor({
  row,
  onClose,
}: {
  row: NotifRow;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const segments = useSegments();

  const saved = row.custom_text_ru ?? row.default_text_ru;
  const [text, setText] = useState(saved);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const cfg = row.trigger_config as {
    before_expiry_hours?: number;
    tolerance_hours?: number;
    segment_filter?: string;
  };
  const isReminder = typeof cfg?.before_expiry_hours === "number";

  const [beforeH, setBeforeH] = useState(
    cfg?.before_expiry_hours != null ? String(cfg.before_expiry_hours) : "",
  );
  const [tolH, setTolH] = useState(
    cfg?.tolerance_hours != null ? String(cfg.tolerance_hours) : "",
  );
  const [segmentFilter, setSegmentFilter] = useState(cfg?.segment_filter ?? "");

  const textChanged = text !== saved;
  const windowChanged =
    isReminder &&
    (beforeH !== String(cfg.before_expiry_hours ?? "") ||
      tolH !== String(cfg.tolerance_hours ?? "") ||
      segmentFilter !== (cfg.segment_filter ?? ""));
  const dirty = textChanged || windowChanged;

  const stats = useQuery({
    queryKey: ["automated-notifications", "stats", row.key],
    queryFn: () => endpoints.automatedNotificationStats(row.key, 168),
    refetchInterval: 60_000,
  });

  const save = useMutation({
    mutationFn: () => {
      const body: {
        custom_text_ru?: string;
        trigger_config?: Record<string, unknown>;
      } = {};
      if (textChanged) body.custom_text_ru = text;

      if (isReminder && windowChanged) {
        const before = Number.parseFloat(beforeH);
        if (!Number.isFinite(before) || before <= 0) {
          throw new Error("Часы до истечения должны быть положительным числом");
        }
        const tolerance = Number.parseFloat(tolH);
        body.trigger_config = {
          before_expiry_hours: before,
          tolerance_hours: Number.isFinite(tolerance) ? tolerance : 1,
          // Пустая строка — снять фильтр; сервер приравнивает её к «нет».
          segment_filter: segmentFilter,
        };
      }
      return endpoints.automatedNotificationPatch(row.key, body);
    },
    onSuccess: () => {
      toast.success("Сохранено");
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
      onClose();
    },
    onError: (e: unknown) =>
      toast.error(
        (e as ApiError)?.detail ??
          (e instanceof Error ? e.message : "Не удалось сохранить"),
      ),
  });

  const reset = useMutation({
    mutationFn: () => endpoints.automatedNotificationReset(row.key),
    onSuccess: () => {
      toast.success("Вернули текст из кода");
      setText(row.default_text_ru);
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось вернуть текст"),
  });

  const testSend = useMutation({
    mutationFn: () => endpoints.automatedNotificationTestSend(row.key),
    onSuccess: () => toast.success("Отправлено вам — посмотрите свой чат"),
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось отправить"),
  });

  const del = useMutation({
    mutationFn: () => endpoints.automatedNotificationDelete(row.key),
    onSuccess: () => {
      toast.success(`«${row.title}» удалено`);
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
      onClose();
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось удалить"),
    onSettled: () => setConfirmDelete(false),
  });

  return (
    <>
      <Modal
        open
        onClose={onClose}
        title={row.title}
        description={row.description ?? undefined}
        size="lg"
        footer={
          <>
            {!row.is_code_registered && (
              <Button
                variant="danger"
                onClick={() => setConfirmDelete(true)}
                className="mr-auto"
              >
                Удалить
              </Button>
            )}
            <Button onClick={onClose} disabled={save.isPending}>
              Отмена
            </Button>
            <Button
              variant="primary"
              onClick={() => save.mutate()}
              disabled={!dirty}
              loading={save.isPending}
            >
              Сохранить
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <code className="block text-xs text-fg-subtle">{row.key}</code>

          {/* Сколько раз это уведомление отработало за неделю. */}
          <section>
            <h3 className="mb-1.5 text-base font-medium text-fg">За последние 7 дней</h3>
            <LoadingGate
              loading={stats.isLoading}
              skeleton={
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-16" />
                  ))}
                </div>
              }
              message="Считаю отправки"
            >
              {stats.isError ? (
                <p className="text-base text-danger">
                  Статистика не загрузилась — сколько раз это ушло, сейчас
                  неизвестно.
                </p>
              ) : (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <StatTile label="Ушло" value={fmtNum(stats.data?.sent ?? 0)} />
                  <StatTile label="Не дошло" value={fmtNum(stats.data?.failed ?? 0)} />
                  <StatTile
                    label="Заблокировали"
                    value={fmtNum(stats.data?.blocked ?? 0)}
                  />
                  <StatTile
                    label="Пропущено"
                    value={fmtNum(stats.data?.skipped ?? 0)}
                    hint="было выключено в момент срабатывания"
                  />
                </div>
              )}
            </LoadingGate>
          </section>

          {isReminder && (
            <section className="rounded-md border border-border bg-bg-subtle p-3">
              <h3 className="text-base font-medium text-fg">Когда отправлять</h3>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Input
                  label="За сколько часов до конца"
                  type="number"
                  min={0.1}
                  max={720}
                  step={0.5}
                  value={beforeH}
                  onChange={(e) => setBeforeH(e.target.value)}
                />
                <Input
                  label="Допуск, ± часов"
                  type="number"
                  min={0}
                  max={48}
                  step={0.5}
                  value={tolH}
                  onChange={(e) => setTolH(e.target.value)}
                  hint="Пусто — час"
                />
              </div>
              <p className="mt-2 text-xs text-fg-muted">
                Планировщик проверяет раз в минуту. Узкий допуск — точнее
                попадание, но выше шанс промахнуться, если планировщик задержится.
              </p>

              <div className="mt-3 border-t border-border-subtle pt-3">
                <label
                  htmlFor="notif-segment"
                  className="mb-1.5 block text-base font-medium text-fg"
                >
                  Сузить круг получателей
                </label>
                <select
                  id="notif-segment"
                  value={segmentFilter}
                  onChange={(e) => setSegmentFilter(e.target.value)}
                  disabled={segments.isLoading}
                  className="h-9 w-full rounded-md border border-border-control bg-bg-card px-2 text-base text-fg outline-none focus-visible:border-accent-9"
                >
                  <option value="">Всем, кто попал в окно</option>
                  {groupSegments(segments.data).map((g) => (
                    <optgroup key={g.group} label={g.group}>
                      {g.items.map((s) => {
                        const n = segmentCount(segments.data, s.key);
                        return (
                          <option key={s.key} value={s.key}>
                            {s.label}
                            {n != null && ` — ${fmtNum(n)} чел.`}
                          </option>
                        );
                      })}
                    </optgroup>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-fg-muted">
                  Уведомление уйдёт только тем, кто и попал в окно, и входит в
                  этот сегмент. Например: «за 7 дней до конца, но только тем, кто
                  ни разу не платил».
                </p>
              </div>
            </section>
          )}

          <section>
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
              <label htmlFor="notif-text" className="text-base font-medium text-fg">
                Текст
              </label>
              <Button
                size="sm"
                onClick={() => reset.mutate()}
                disabled={!row.has_custom_text}
                loading={reset.isPending}
                title={
                  row.has_custom_text
                    ? "Вернуть текст, зашитый в коде"
                    : "Текст и так заводской — возвращать нечего"
                }
              >
                Вернуть заводской
              </Button>
            </div>
            <textarea
              id="notif-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={10}
              className="w-full resize-y rounded-md border border-border-control bg-bg-card p-3 font-mono text-xs leading-relaxed text-fg outline-none focus-visible:border-accent-9"
            />
            {row.template_vars.length > 0 && (
              <p className="mt-1.5 text-xs text-fg-muted">
                Подставляются:{" "}
                {row.template_vars.map((v, i) => (
                  <span key={v}>
                    <code className="rounded bg-bg-subtle px-1 font-mono">{`{${v}}`}</code>
                    {i < row.template_vars.length - 1 && ", "}
                  </span>
                ))}
              </p>
            )}
          </section>

          <section>
            <h3 className="mb-1.5 text-base font-medium text-fg">
              Так это увидит человек
            </h3>
            {/* Плейсхолдеры остаются фигурными скобками: подставить их
                нечем, и притворяться, что там будет имя, не надо. */}
            <MessagePreview message={text} />
          </section>

          <section className="rounded-md border border-border bg-bg-subtle p-3">
            <h3 className="text-base font-medium text-fg">Проверить в Telegram</h3>
            <p className="mt-0.5 text-xs text-fg-muted">
              Придёт вам. Отправляется сохранённый текст, поэтому правки надо
              сначала сохранить.
            </p>
            <div className="mt-2">
              <Button
                icon={<Send className="h-3.5 w-3.5" />}
                onClick={() => testSend.mutate()}
                loading={testSend.isPending}
                disabled={dirty}
                title={
                  dirty
                    ? "Сначала сохраните — уйдёт сохранённый текст, а не тот, что в поле"
                    : "Отправить себе"
                }
              >
                Отправить себе
              </Button>
              {dirty && (
                <span className="ml-3 text-xs text-fg-muted">
                  Есть несохранённые правки
                </span>
              )}
            </div>
          </section>

          {row.has_custom_text && (
            <details className="rounded-md border border-border-subtle p-3">
              <summary className="cursor-pointer text-base text-fg-muted">
                Заводской текст из кода
              </summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-xs leading-relaxed text-fg-muted">
                {row.default_text_ru}
              </pre>
            </details>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => del.mutate()}
        title="Удалить уведомление"
        body={
          <>
            «{row.title}» и его текст исчезнут насовсем. Это заготовка, созданная
            руками, — в коде её нет, восстановить будет неоткуда.
          </>
        }
        confirmLabel="Удалить"
        cancelLabel="Оставить"
        destructive
        requireText={row.key}
        requireHint={`Наберите ключ уведомления — ${row.key}`}
        loading={del.isPending}
      />
    </>
  );
}
