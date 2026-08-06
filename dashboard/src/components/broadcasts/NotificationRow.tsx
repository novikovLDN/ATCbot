import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate } from "@/lib/format";
import { toast } from "@/store/toast";
import { Button, StatusBadge } from "@/components/ui";

/**
 * Строка автоуведомления в списке.
 *
 * ТУМБЛЕР ДЕЙСТВУЕТ СРАЗУ И БЕЗ ВОПРОСОВ — и это правильно: выключить
 * уведомление обратимо одним нажатием, а диалог на каждое переключение
 * приучил бы жать «да» не глядя (research §6.6 про «волки»). Но
 * состояние обязано читаться без цвета, поэтому выключенное подписано
 * словом, а не только приглушено.
 *
 * ЭТО КНОПКА, А НЕ CHECKBOX: она выполняет действие немедленно, а не
 * копит значение до кнопки «сохранить». `aria-pressed` сообщает
 * состояние тем, кто не видит подписи.
 */

export interface NotifRow {
  key: string;
  title: string;
  description: string | null;
  category: string;
  is_enabled: boolean;
  has_custom_text: boolean;
  default_text_ru: string;
  custom_text_ru: string | null;
  trigger_config: Record<string, unknown>;
  template_vars: string[];
  updated_at: string | null;
  last_edited_by: number | null;
  is_code_registered: boolean;
}

/** Окно срабатывания напоминания словами. Пусто — уведомление не
 *  привязано ко времени истечения подписки. */
export function triggerWindow(cfg: Record<string, unknown>): string | null {
  const before = cfg?.before_expiry_hours;
  if (typeof before !== "number") return null;
  const tolerance = typeof cfg?.tolerance_hours === "number" ? cfg.tolerance_hours : 1;
  return `за ${before} ч до конца подписки, ±${tolerance} ч`;
}

export function NotificationRow({
  row,
  onEdit,
}: {
  row: NotifRow;
  onEdit: () => void;
}) {
  const qc = useQueryClient();

  const toggle = useMutation({
    mutationFn: () =>
      endpoints.automatedNotificationPatch(row.key, { is_enabled: !row.is_enabled }),
    onSuccess: () => {
      toast.success(
        row.is_enabled
          ? `«${row.title}» больше не отправляется`
          : `«${row.title}» снова отправляется`,
      );
      qc.invalidateQueries({ queryKey: ["automated-notifications"] });
    },
    onError: (e: unknown) =>
      toast.error((e as ApiError)?.detail ?? "Не удалось переключить"),
  });

  const window = triggerWindow(row.trigger_config);

  return (
    <div
      className={cn(
        "flex flex-wrap items-start gap-3 rounded-md border p-3",
        row.is_enabled ? "border-border bg-bg-card" : "border-border-subtle bg-bg-subtle",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-fg">{row.title}</span>
          {!row.is_enabled && <StatusBadge kind="neutral">Не отправляется</StatusBadge>}
          {row.has_custom_text && <StatusBadge kind="info">Текст изменён</StatusBadge>}
          {!row.is_code_registered && (
            <StatusBadge kind="pending">Только вручную</StatusBadge>
          )}
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-fg-muted">
          <code className="rounded bg-bg-subtle px-1 py-0.5 font-mono">{row.key}</code>
          {window && <span>{window}</span>}
          {row.updated_at && <span>правили {fmtDate(row.updated_at)}</span>}
        </div>

        {row.description && (
          <p className="mt-1 text-xs leading-snug text-fg-subtle">{row.description}</p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <Button
          size="sm"
          aria-pressed={row.is_enabled}
          onClick={() => toggle.mutate()}
          loading={toggle.isPending}
          title={
            row.is_enabled
              ? "Перестать отправлять это уведомление"
              : "Снова отправлять это уведомление"
          }
        >
          {row.is_enabled ? "Выключить" : "Включить"}
        </Button>
        <Button size="sm" variant="primary" onClick={onEdit}>
          Открыть
        </Button>
      </div>
    </div>
  );
}
