import type { LucideIcon } from "lucide-react";
import { Inbox, SearchX, Lock, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

/**
 * Пустые состояния. Их ЧЕТЫРЕ, и это разные компоненты, а не один с разным
 * текстом (ux-patterns §3.4).
 *
 *   EmptyFirstRun  — сущностей ещё нет вообще. Объясняем, зачем раздел, и даём
 *                    кнопку создания.
 *   EmptyFilter    — данные есть, но фильтр не совпал. Кнопка «создать» здесь
 *                    ЗАПРЕЩЕНА: это классическая ошибка — человек искал
 *                    существующее, а ему предлагают завести новое. Правильное
 *                    действие — сбросить фильтр.
 *   EmptyNoAccess  — прав не хватает. Без обвинения, с указанием, к кому идти.
 *   EmptyFailure   — не загрузилось. Говорим, ЧТО именно не загрузилось, и
 *                    даём «Повторить» на месте — виджет держит свои габариты и
 *                    не блокирует остальную панель (§3.5).
 *
 * Тон везде один: не заставлять чувствовать себя виноватым (Polaris).
 *
 * «Повторить» имеет смысл только при временных отказах — таймаут, обрыв, 5xx.
 * При ошибке клиента (422, 403) повтор бессмыслен и вводит в заблуждение
 * (§3.6, пункт 5), поэтому у EmptyFailure отдельный флаг retryable.
 */

function Shell({
  icon: Icon,
  title,
  description,
  action,
  tone = "neutral",
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  tone?: "neutral" | "danger";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-border px-6 py-10 text-center",
        className,
      )}
    >
      <div
        className={cn(
          "grid h-10 w-10 place-items-center rounded-lg",
          tone === "danger" ? "bg-danger/12 text-danger" : "bg-bg-subtle text-fg-subtle",
        )}
      >
        <Icon className="h-5 w-5" aria-hidden />
      </div>
      <div className="max-w-sm">
        <div className="text-base font-medium text-fg">{title}</div>
        {description && <div className="mt-1 text-base text-fg-muted">{description}</div>}
      </div>
      {action}
    </div>
  );
}

/** Случай 1: раздел пуст, потому что в нём ещё ничего не заводили. */
export function EmptyFirstRun({
  title,
  description,
  actionLabel,
  onAction,
  icon = Inbox,
  className,
}: {
  title: string;
  /** Зачем нужен раздел и что даст первая запись. */
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <Shell
      icon={icon}
      title={title}
      description={description}
      className={className}
      action={
        actionLabel && onAction ? (
          <Button variant="primary" onClick={onAction}>
            {actionLabel}
          </Button>
        ) : undefined
      }
    />
  );
}

/** Случай 2: не совпал фильтр или поиск. Никаких «создать». */
export function EmptyFilter({
  /** Что именно искали — строка запроса или перечисление активных фильтров. */
  query,
  onReset,
  className,
}: {
  query?: string;
  onReset: () => void;
  className?: string;
}) {
  return (
    <Shell
      icon={SearchX}
      title="Ничего не нашлось"
      description={
        query
          ? `По условию «${query}» записей нет. Проверьте написание или снимите часть фильтров.`
          : "Под выбранные фильтры не попала ни одна запись. Снимите часть условий."
      }
      className={className}
      action={<Button onClick={onReset}>Сбросить фильтры</Button>}
    />
  );
}

/** Случай 3: доступа нет. Без вины и без предложения «попробовать ещё раз». */
export function EmptyNoAccess({
  what = "этому разделу",
  contact,
  className,
}: {
  /** «этому разделу», «журналу действий», «настройкам выплат». */
  what?: string;
  /** Кто выдаёт доступ. Без этого сообщение — тупик. */
  contact?: string;
  className?: string;
}) {
  return (
    <Shell
      icon={Lock}
      title={`Нет доступа к ${what}`}
      description={
        contact
          ? `Доступ выдаёт ${contact}. Напишите ему, чтобы открыть раздел.`
          : "Доступ выдаёт владелец панели."
      }
      className={className}
    />
  );
}

/** Случай 4: отказ загрузки. Виджет держит габариты, панель не блокируется. */
export function EmptyFailure({
  /** Что именно не загрузилось: «платежи за 30 дней», «список клиентов». */
  what,
  /** Причина человеческим языком, без кода ошибки. */
  reason,
  onRetry,
  /** Повтор имеет смысл только при временном отказе. */
  retryable = true,
  className,
}: {
  what: string;
  reason?: string;
  onRetry?: () => void;
  retryable?: boolean;
  className?: string;
}) {
  return (
    <Shell
      icon={AlertTriangle}
      tone="danger"
      title={`Не удалось загрузить: ${what}`}
      description={reason ?? "Сервер не ответил вовремя. Остальная панель работает."}
      className={className}
      action={
        retryable && onRetry ? <Button onClick={onRetry}>Повторить</Button> : undefined
      }
    />
  );
}
