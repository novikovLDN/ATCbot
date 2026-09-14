/**
 * Small controls of the v5 (iOS) system: IconButton, Segmented (a
 * UISegmentedControl), ListRow (a table cell in an inset group),
 * PillProgress, StatusDot, DeltaPill.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
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

/** iOS segmented control scoping a screen or a card (period, unit…).
    `full` stretches it to the container width (phones). */
export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
  full,
  className,
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (v: T) => void;
  label: string;
  full?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("capsule-nav", full && "seg-full", className)} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          className="capsule-tab"
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
 * A table cell: leading dot or icon, title + meta, a value on the right,
 * and a chevron when the row opens something. Inside a card, rows form
 * one group with hairline separators (index.css).
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
        <span className="block truncate text-[17px] leading-[22px] lg:text-[15px] lg:leading-5">{title}</span>
        {meta && <span className="t-mute mt-0.5 block truncate text-[13px] leading-[18px]">{meta}</span>}
      </span>
      {value !== undefined && (
        <span className="tabular flex-none text-right text-[17px] leading-[22px] text-body lg:text-[15px] lg:leading-5">{value}</span>
      )}
      {trailing ?? (to || onClick ? <ChevronRight className="row-chevron -mr-1 h-[18px] w-[18px]" strokeWidth={2.2} aria-hidden="true" /> : null)}
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

/** A thin progress bar. `knob` is accepted for compatibility (v3) and ignored. */
export function PillProgress({
  value,
  label,
  valueLabel,
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
      </div>
    </div>
  );
}

export function fmtDeltaPct(pct: number): string {
  const abs = Math.abs(pct);
  if (abs < 0.1) return "±0%";
  return `${pct > 0 ? "+" : "−"}${abs.toFixed(abs < 10 ? 1 : 0).replace(".", ",")}%`;
}

/** Change against the previous period: an arrow and a coloured label.
    Direction and goodness are separate — churn going up is bad. */
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
    return period ? <span className="t-mute text-[13px]">нет базы для сравнения</span> : null;
  }
  const flat = Math.abs(pct) < 0.1;
  const good = flat ? null : pct > 0 === higherIsBetter;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5 text-[13px] leading-[18px]">
      <span
        className={cn(
          "tabular whitespace-nowrap font-semibold",
          good === null ? "text-mute" : good ? "text-success" : "text-danger",
        )}
      >
        {!flat && <span aria-hidden="true">{pct > 0 ? "↑ " : "↓ "}</span>}
        {fmtDeltaPct(pct)}
      </span>
      {period && <span className="t-mute">{period}</span>}
    </span>
  );
}
