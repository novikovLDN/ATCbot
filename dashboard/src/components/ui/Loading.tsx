import { useEffect, useRef, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

/**
 * Лестница загрузки. Пороги — из NN/g (ux-patterns §3.1), не выдуманы:
 *
 *   до 1 с      ничего не показываем — задержка не осознаётся, мигание
 *               скелетоном хуже самой задержки;
 *   1–2 с       скелетон блока;
 *   2–10 с      крутилка и текст, что именно происходит;
 *   больше 10 с процент выполнения и кнопка «Прервать» — 10 секунд это предел
 *               удержания внимания, дальше человек уходит в другую вкладку.
 *
 * Хук возвращает текущую ступень. Он же гарантирует, что ступени не
 * перескакивают назад на дребезге: как только загрузка кончилась, всё
 * сбрасывается разом.
 */
export type LoadStage = "idle" | "quiet" | "skeleton" | "spinner" | "progress";

export function useLoadStage(loading: boolean): LoadStage {
  const [stage, setStage] = useState<LoadStage>("idle");
  const timers = useRef<number[]>([]);

  useEffect(() => {
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
    if (!loading) {
      setStage("idle");
      return;
    }
    setStage("quiet");
    timers.current.push(window.setTimeout(() => setStage("skeleton"), 1000));
    timers.current.push(window.setTimeout(() => setStage("spinner"), 2000));
    timers.current.push(window.setTimeout(() => setStage("progress"), 10000));
    return () => {
      timers.current.forEach(window.clearTimeout);
      timers.current = [];
    };
  }, [loading]);

  return stage;
}

/** Крутилка. Отдельным компонентом, потому что она нужна и вне лестницы —
 *  внутри кнопки, внутри модалки. Скелетоном её подменять нельзя. */
export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2" role="status" aria-live="polite">
      <Loader2 className={cn("h-4 w-4 animate-spin text-fg-muted", className)} aria-hidden />
      {label && <span className="text-base text-fg-muted">{label}</span>}
    </span>
  );
}

/**
 * Полоса выполнения. Появляется, когда операция перевалила за 10 секунд.
 * Статичных индикаторов не бывает: если процента нет, показываем крутилку.
 */
export function ProgressBar({
  value,
  label,
  onAbort,
  className,
}: {
  /** 0…100. undefined — процент неизвестен, рисуем неопределённое состояние. */
  value?: number;
  label?: ReactNode;
  onAbort?: () => void;
  className?: string;
}) {
  const known = typeof value === "number";
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="text-base text-fg-muted">{label}</div>
        {known && <div className="text-base tabular-nums text-fg">{Math.round(value)}%</div>}
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-bg-subtle"
        role="progressbar"
        aria-valuenow={known ? Math.round(value) : undefined}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn("h-full rounded-full bg-accent-9 transition-[width] duration-300")}
          style={{ width: known ? `${Math.min(100, Math.max(0, value))}%` : "35%" }}
        />
      </div>
      {onAbort && (
        <div>
          <Button size="sm" onClick={onAbort}>
            Прервать
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * Обёртка, которая сама разводит ступени лестницы.
 *
 * Использование:
 *   <LoadingGate loading={q.isLoading} skeleton={<SkeletonTable />}
 *                message="Считаю платежи за 30 дней"
 *                progress={pct} onAbort={cancel}>
 *     <PaymentsTable … />
 *   </LoadingGate>
 *
 * Скелетон передаётся снаружи: универсального скелетона не бывает, он обязан
 * повторять форму того, что грузится.
 */
export function LoadingGate({
  loading,
  skeleton,
  message,
  progress,
  onAbort,
  children,
}: {
  loading: boolean;
  skeleton?: ReactNode;
  /** Что именно происходит. «Загрузка…» — плохой текст, он ничего не говорит. */
  message?: string;
  progress?: number;
  onAbort?: () => void;
  children: ReactNode;
}) {
  const stage = useLoadStage(loading);

  if (!loading) return <>{children}</>;

  // До секунды — ничего. Старое содержимое остаётся на экране, если оно есть.
  if (stage === "quiet") return <>{children}</>;

  if (stage === "skeleton" && skeleton) return <>{skeleton}</>;

  if (stage === "progress") {
    return (
      <div className="rounded-lg border border-border p-4">
        <ProgressBar value={progress} label={message ?? "Выполняю операцию"} onAbort={onAbort} />
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center rounded-lg border border-border p-6">
      <Spinner label={message ?? "Загружаю данные"} />
    </div>
  );
}
