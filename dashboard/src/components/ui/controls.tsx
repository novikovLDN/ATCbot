/**
 * Small controls of the v3 system: IconButton, Segmented, ListRow,
 * PillProgress, StatusDot, DeltaPill. Everything is a capsule or a circle.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/cn";

export function IconButton({
  label,
  children,
  accent,
  small,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  accent?: boolean;
  small?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn("icon-btn", accent && "icon-btn-accent", small && "icon-btn-sm", className)}
      {...rest}
    >
      {children}
    </button>
  );
}

export interface SegmentedOption<T extends string | number> {
  value: T;
  label: string;
}

/** Capsule tabs scoping a whole screen (period, unit…). */
export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="capsule-nav" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          className="capsule-tab h-8 px-3 text-[12px]"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export type Tone = "ok" | "warn" | "err" | "info" | "idle" | "accent";

export function StatusDot({ tone, label }: { tone: Tone; label?: string }) {
  return (
    <span
      className={cn("dot", `dot-${tone}`)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}

/**
 * A stacked capsule row: leading dot or icon, title + meta, a value, and a
 * circular control on the right (↗ when the row links somewhere).
 */
export function ListRow({
  leading,
  title,
  meta,
  value,
  to,
  onClick,
  trailing,
  className,
}: {
  leading?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  value?: ReactNode;
  to?: string;
  onClick?: () => void;
  trailing?: ReactNode;
  className?: string;
}) {
  const body = (
    <>
      {leading && <span className="grid w-5 flex-none place-items-center">{leading}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-medium">{title}</span>
        {meta && <span className="t-mute block truncate text-[12px]">{meta}</span>}
      </span>
      {value !== undefined && (
        <span className="tabular flex-none text-right text-[14px] font-semibold">{value}</span>
      )}
      {trailing ??
        (to || onClick ? (
          <span className="icon-btn icon-btn-sm bg-tile-1" aria-hidden="true">
            <ArrowUpRight className="h-3.5 w-3.5" />
          </span>
        ) : null)}
    </>
  );
  if (to) {
    return (
      <Link to={to} className={cn("list-row", className)}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={cn("list-row w-full text-left", className)}>
        {body}
      </button>
    );
  }
  return <div className={cn("list-row", className)}>{body}</div>;
}

/** Progress as a pill with the cream knob at the value. */
export function PillProgress({
  value,
  label,
  valueLabel,
  knob = true,
}: {
  /** 0–100; null renders an empty track. */
  value: number | null;
  label?: ReactNode;
  valueLabel?: ReactNode;
  knob?: boolean;
}) {
  const v = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div>
      {(label || valueLabel) && (
        <div className="mb-2 flex items-baseline justify-between gap-3 text-[13px]">
          <span className="t-body min-w-0 truncate">{label}</span>
          <span className="tabular font-semibold">{valueLabel}</span>
        </div>
      )}
      <div
        className="pill-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value == null ? undefined : Math.round(v)}
        aria-label={typeof label === "string" ? label : undefined}
      >
        <div className="pill-fill" style={{ width: `${v}%` }} />
        {knob && value != null && <div className="pill-knob" style={{ left: `${Math.max(v, 4)}%` }} />}
      </div>
    </div>
  );
}

function fmtDeltaPct(pct: number): string {
  const abs = Math.abs(pct);
  if (abs < 0.1) return "±0%";
  return `${pct > 0 ? "+" : "−"}${abs.toFixed(abs < 10 ? 1 : 0)}%`;
}

/** Change against the previous period. Direction and goodness are
    separate: churn going up is bad. Arrow + dot, never colour alone. */
export function DeltaPill({
  pct,
  period,
  higherIsBetter = true,
}: {
  pct: number | null | undefined;
  period?: string;
  higherIsBetter?: boolean;
}) {
  if (pct == null || !Number.isFinite(pct)) {
    return period ? <span className="t-mute text-[12px]">нет базы для сравнения</span> : null;
  }
  const flat = Math.abs(pct) < 0.1;
  const good = flat ? null : pct > 0 === higherIsBetter;
  return (
    <span className="inline-flex items-center gap-2 text-[12px]">
      <span className="capsule-label tabular">
        <StatusDot tone={good === null ? "idle" : good ? "ok" : "err"} />
        {!flat && <span aria-hidden="true">{pct > 0 ? "↑" : "↓"}</span>}
        {fmtDeltaPct(pct)}
      </span>
      {period && <span className="t-mute">{period}</span>}
    </span>
  );
}
