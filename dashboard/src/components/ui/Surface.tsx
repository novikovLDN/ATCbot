/**
 * Surface — a card in an iOS inset group.
 *
 * Every card looks the same (white / #1C1C1E): the v3 shade variants
 * (raised, steel, fog, mist, accent) and the corner notch are accepted
 * for compatibility and render identically. The header row holds the
 * card title, its ⓘ definition, an optional control and, with `to`, a
 * chevron that opens the detail screen.
 */
import type { ElementType, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { Hint } from "./Hint";

export type SurfaceVariant = "ink" | "raised" | "steel" | "fog" | "mist" | "accent";
export type Corner = "tl" | "tr" | "bl" | "br";

export interface SurfaceProps {
  variant?: SurfaceVariant;
  notch?: Corner;
  /** Detail route; renders a chevron link in the header. */
  to?: string;
  toLabel?: string;
  /** Card title. */
  label?: ReactNode;
  /** Right side of the header (a segmented control, a count…). */
  aside?: ReactNode;
  /** Exact definition of the figure in this card — renders an ⓘ sheet. */
  hint?: ReactNode;
  padded?: boolean;
  className?: string;
  children?: ReactNode;
  as?: ElementType;
  "aria-label"?: string;
}

export function Surface({
  to,
  toLabel,
  label,
  aside,
  hint,
  padded = true,
  className,
  children,
  as: Tag = "section",
  variant: _variant,
  notch: _notch,
  ...rest
}: SurfaceProps) {
  return (
    <Tag className={cn("tile min-w-0", padded && "p-4", className)} {...rest}>
      {(label || aside || hint || to) && (
        <div className="mb-3 flex min-h-[24px] flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <span className="flex min-w-0 items-center gap-1.5">
            {label ? <span className="card-title min-w-0 truncate">{label}</span> : null}
            {hint ? <Hint text={hint} label={typeof label === "string" ? `Как считается: ${label}` : undefined} /> : null}
          </span>
          {(aside || to) && (
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              {aside}
              {to && (
                <Link
                  to={to}
                  className="tap-target -mr-1 inline-flex items-center text-[15px] text-accent active:opacity-50"
                  aria-label={toLabel ? `Открыть: ${toLabel}` : "Подробнее"}
                >
                  <ChevronRight className="h-5 w-5 text-ash" strokeWidth={2.2} aria-hidden="true" />
                </Link>
              )}
            </span>
          )}
        </div>
      )}
      {children}
    </Tag>
  );
}

/** Responsive grid: one column on a phone, 6 on a tablet, 12 on desktop.
    Children set their own spans. */
export function Bento({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-1 gap-[var(--gap)] sm:grid-cols-6 xl:grid-cols-12", className)}>
      {children}
    </div>
  );
}

/** Large title of a screen (34/41 bold), its note and the screen controls. */
export function PageHeader({
  title,
  sub,
  actions,
}: {
  title: string;
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 px-1 lg:flex lg:items-end lg:justify-between lg:gap-6">
      <div className="min-w-0">
        <h1 className="large-title">{title}</h1>
        {sub && <p className="t-mute mt-1 max-w-[72ch] text-[15px] leading-5">{sub}</p>}
      </div>
      {actions && <div className="mt-3 flex flex-wrap items-center gap-2 lg:mt-0 lg:flex-none lg:justify-end">{actions}</div>}
    </div>
  );
}

/** Section header above a group of cards: small caps, secondary colour. */
export function SectionHeader({
  title,
  sub,
  aside,
  hint,
  className,
}: {
  title: string;
  sub?: ReactNode;
  aside?: ReactNode;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-2 mt-8 flex flex-wrap items-end justify-between gap-x-3 gap-y-1 px-4 first:mt-0", className)}>
      <div className="min-w-0">
        <h2 className="section-h flex items-center gap-1.5">
          {title}
          {hint ? <Hint text={hint} label={`Как считается: ${title}`} /> : null}
        </h2>
        {sub && <p className="t-mute mt-0.5 max-w-[72ch] text-[13px] leading-[18px]">{sub}</p>}
      </div>
      {aside && <div className="flex flex-wrap items-center gap-2 text-[15px]">{aside}</div>}
    </div>
  );
}
