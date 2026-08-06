import { AlertCircle, CheckCircle2, Send } from "lucide-react";

import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import type { SendProgress } from "./useSendProgress";

/**
 * Ход отправки одной рассылки.
 *
 * ЦВЕТ ЗДЕСЬ НЕ ЕДИНСТВЕННЫЙ НОСИТЕЛЬ СМЫСЛА. Раньше состояние
 * различалось только заливкой полосы: синяя — идёт, зелёная — готово,
 * красная — сбой. Для человека, который не различает эти цвета, три
 * разных исхода выглядели одинаково. Теперь у каждого состояния своя
 * иконка и своё слово, а полоса лишь повторяет их.
 */

const LOOKS = {
  running: {
    icon: Send,
    word: "Отправляю",
    bar: "bg-accent-9",
    text: "text-accent-11",
  },
  done: {
    icon: CheckCircle2,
    word: "Отправлено",
    bar: "bg-success",
    text: "text-success",
  },
  failed: {
    icon: AlertCircle,
    word: "Отправка сорвалась",
    bar: "bg-danger",
    text: "text-danger",
  },
} as const;

export function SendProgressBar({
  progress,
  compact,
}: {
  progress: SendProgress;
  /** Строка списка — только полоса и числа, без слова состояния. */
  compact?: boolean;
}) {
  const look = LOOKS[progress.status];
  const Icon = look.icon;

  const pct =
    progress.total > 0
      ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
      : progress.status === "done"
        ? 100
        : 0;

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs">
        {!compact && (
          <span className={cn("inline-flex items-center gap-1.5 font-medium", look.text)}>
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {look.word}
          </span>
        )}
        <span className="tabular-nums text-fg-muted">
          {fmtNum(progress.processed)} из {fmtNum(progress.total)}
          {progress.total > 0 && ` · ${pct}%`}
        </span>
        <span className="tabular-nums text-fg-muted">
          дошло {fmtNum(progress.sent)}
          {progress.failed > 0 && (
            <span className="ml-1.5 text-danger">не дошло {fmtNum(progress.failed)}</span>
          )}
        </span>
      </div>

      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-bg-subtle"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${look.word}: ${progress.processed} из ${progress.total}`}
      >
        {/* Ширина без анимации перехода: полоса обновляется по событию из
            шины несколько раз в секунду, и плавное «доползание» отстаёт
            от чисел рядом (research §6.1 — данные не анимируются). */}
        <div className={cn("h-full rounded-full", look.bar)} style={{ width: `${pct}%` }} />
      </div>

      {progress.status === "failed" && progress.error && (
        <div className="break-words text-xs text-danger">{progress.error}</div>
      )}
    </div>
  );
}
