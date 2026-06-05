import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

interface Props {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  tone?: "default" | "success" | "warning" | "danger" | "accent";
  loading?: boolean;
}

const TONES = {
  default: "from-bg-elevated to-bg-card text-fg",
  accent: "from-accent/15 to-bg-card text-accent",
  success: "from-success/15 to-bg-card text-success",
  warning: "from-warning/15 to-bg-card text-warning",
  danger: "from-danger/15 to-bg-card text-danger",
};

export function StatCard({ label, value, hint, icon: Icon, tone = "default", loading }: Props) {
  return (
    <div className="card card-hover relative overflow-hidden p-5 animate-fade-in">
      <div
        className={cn(
          "pointer-events-none absolute inset-0 -z-10 bg-gradient-to-br opacity-50",
          TONES[tone],
        )}
      />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium uppercase tracking-wider text-fg-subtle">{label}</div>
          <div className="mt-2 truncate text-2xl font-semibold tracking-tight text-fg">
            {loading ? <span className="inline-block h-6 w-24 rounded bg-bg-elevated" /> : value}
          </div>
          {hint && <div className="mt-1 truncate text-xs text-fg-muted">{hint}</div>}
        </div>
        {Icon && (
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-bg-elevated/80 ring-1 ring-border">
            <Icon className="h-4 w-4 text-fg-muted" />
          </div>
        )}
      </div>
    </div>
  );
}
