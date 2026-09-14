/**
 * One vocabulary for health on every screen: the system status
 * (ok / degraded / down), section verdicts (ok / warning / critical) and
 * the reasons behind them. Colour is never the only signal — every badge
 * carries a word, every reason a dot plus text.
 */
import type { Reason, SectionStatus, SystemStatus } from "@/lib/metricsApi";
import { cn } from "@/lib/cn";
import { StatusDot, type Tone } from "./controls";

type AnyStatus = SectionStatus | SystemStatus;

const META: Record<AnyStatus, { tone: Tone; label: string }> = {
  ok: { tone: "ok", label: "В норме" },
  warning: { tone: "warn", label: "Внимание" },
  critical: { tone: "err", label: "Критично" },
  degraded: { tone: "warn", label: "Деградация" },
  down: { tone: "err", label: "Сбой" },
  unknown: { tone: "idle", label: "Нет данных" },
};

export function statusTone(status: AnyStatus | undefined | null): Tone {
  return META[status ?? "unknown"]?.tone ?? "idle";
}

export function statusLabel(status: AnyStatus | undefined | null): string {
  return META[status ?? "unknown"]?.label ?? "Нет данных";
}

export function StatusBadge({ status, className }: { status: AnyStatus | undefined | null; className?: string }) {
  return (
    <span className={cn("capsule-label", className)}>
      <StatusDot tone={statusTone(status)} />
      {statusLabel(status)}
    </span>
  );
}

const LEVEL_TONE: Record<Reason["level"], Tone> = {
  down: "err",
  critical: "err",
  degraded: "warn",
  warning: "warn",
  info: "info",
};

/** Reasons behind a status, worst first (the backend sorts them). */
export function ReasonList({
  reasons,
  empty = "Всё в норме.",
  limit,
}: {
  reasons: Reason[];
  empty?: string;
  limit?: number;
}) {
  const shown = limit ? reasons.slice(0, limit) : reasons;
  if (!shown.length) {
    return (
      <p className="flex items-center gap-2 text-[13px]">
        <StatusDot tone="ok" /> {empty}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {shown.map((r) => (
        <li key={r.key} className="flex items-start gap-2.5 text-[13px] leading-5">
          <span className="mt-1.5 flex-none">
            <StatusDot tone={LEVEL_TONE[r.level] ?? "idle"} />
          </span>
          <span className="min-w-0">{r.text}</span>
        </li>
      ))}
      {limit && reasons.length > limit && (
        <li className="t-mute pl-5 text-[12px]">И ещё {reasons.length - limit}.</li>
      )}
    </ul>
  );
}
