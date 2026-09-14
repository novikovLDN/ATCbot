/** Loading / empty / error states shared by every screen. */
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden="true" />;
}

export function LoadingTiles({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-[var(--gap)] sm:grid-cols-2 xl:grid-cols-4" role="status" aria-label="Загрузка">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="tile p-4">
          <Skeleton className="mb-3 h-4 w-24" />
          <Skeleton className="h-8 w-32" />
        </div>
      ))}
    </div>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Сессия закончилась. Войдите снова.";
    if (error.status >= 500) return `Сервер не смог посчитать данные (${error.detail}).`;
    return error.detail;
  }
  return error instanceof Error ? error.message : "Не удалось загрузить данные.";
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("tile flex flex-wrap items-center justify-between gap-3 p-4", className)}>
      <div className="flex min-w-0 items-start gap-3">
        <span className="dot dot-err mt-[7px]" aria-hidden="true" />
        <p className="min-w-0 break-words text-[15px] leading-5">{describe(error)}</p>
      </div>
      {onRetry && (
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-1.5 py-4">
      <p className="text-[15px] font-medium">{title}</p>
      {hint && <p className="t-mute max-w-[60ch] text-[13px] leading-[18px]">{hint}</p>}
      {action}
    </div>
  );
}
