import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

interface Props {
  label: string;
  value: string;
  hint?: string;
  /** Right-side small pill like the Holo "+0.8h" callout. */
  pill?: string;
  icon?: LucideIcon;
  tone?: "default" | "success" | "warning" | "danger" | "accent";
  loading?: boolean;
}

const TONES = {
  default: "from-bg-elevated to-bg-card",
  accent: "from-accent/12 to-bg-card",
  success: "from-success/12 to-bg-card",
  warning: "from-warning/12 to-bg-card",
  danger: "from-danger/12 to-bg-card",
};

const ICON_TONES = {
  default: "text-fg-muted",
  accent: "text-accent",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};

export function StatCard({
  label,
  value,
  hint,
  pill,
  icon: Icon,
  tone = "default",
  loading,
}: Props) {
  return (
    <div className="card card-hover relative overflow-hidden p-5 animate-fade-in">
      <div
        className={cn(
          "pointer-events-none absolute inset-0 -z-10 bg-gradient-to-br opacity-60",
          TONES[tone],
        )}
      />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-fg-subtle">
            {label}
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            {loading ? (
              <span className="inline-block h-8 w-28 rounded-md bg-bg-elevated" />
            ) : (
              <span className="stat-num truncate">{value}</span>
            )}
            {pill && !loading && (
              <span className="stat-pill shrink-0">{pill}</span>
            )}
          </div>
          {hint && <div className="mt-1 truncate text-xs text-fg-muted">{hint}</div>}
        </div>
        {Icon && (
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated/80 ring-1 ring-border">
            <Icon className={cn("h-4 w-4", ICON_TONES[tone])} strokeWidth={2} />
          </div>
        )}
      </div>
    </div>
  );
}
