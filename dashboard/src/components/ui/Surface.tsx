/**
 * Surface — the bento tile.
 *
 * Tiles are told apart by shade, never by a border:
 *   ink     charcoal, the default primary tile
 *   raised  graphite, a quieter neighbour
 *   steel   mid gray with light text
 *   fog / mist  light grays with dark text — secondary facts
 *   accent  the brand colour — one key figure per screen, at most
 *
 * `notch` bites a corner out of the tile (concave inner curve, convex
 * fillets) so the tile flows around what sits there. With `to`, a
 * circular ↗ link to the detail screen is placed in that bite.
 */
import type { ElementType, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { Hint } from "./Hint";

export type SurfaceVariant = "ink" | "raised" | "steel" | "fog" | "mist" | "accent";
export type Corner = "tl" | "tr" | "bl" | "br";

const VARIANT: Record<SurfaceVariant, string> = {
  ink: "",
  raised: "tile-raised",
  steel: "tile-steel",
  fog: "tile-fog",
  mist: "tile-mist",
  accent: "tile-accent",
};

export interface SurfaceProps {
  variant?: SurfaceVariant;
  notch?: Corner;
  /** Detail route; renders a ↗ badge in the notch (defaults to top-right). */
  to?: string;
  toLabel?: string;
  /** Small capsule label at the top of the tile. */
  label?: ReactNode;
  /** Right side of the label row (a segmented control, a count…). */
  aside?: ReactNode;
  /** Exact definition of the figure in this tile — renders a "?" popover. */
  hint?: ReactNode;
  padded?: boolean;
  className?: string;
  children?: ReactNode;
  as?: ElementType;
  "aria-label"?: string;
}

export function Surface({
  variant = "ink",
  notch,
  to,
  toLabel,
  label,
  aside,
  hint,
  padded = true,
  className,
  children,
  as: Tag = "section",
  ...rest
}: SurfaceProps) {
  const corner = notch ?? (to ? "tr" : undefined);
  return (
    <Tag className={cn("tile min-w-0", VARIANT[variant], padded && "p-5", className)} {...rest}>
      {corner && (
        <span className="notch" data-corner={corner} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      )}
      {to && corner && (
        <span className="corner-slot" data-corner={corner}>
          <Link to={to} className="icon-btn" aria-label={toLabel ? `Открыть: ${toLabel}` : "Подробнее"}>
            <ArrowUpRight className="h-4 w-4" strokeWidth={2} />
          </Link>
        </span>
      )}
      {(label || aside || hint) && (
        <div
          className={cn(
            "mb-4 flex min-h-[24px] flex-wrap items-center justify-between gap-3",
            corner === "tr" && "pr-12",
            corner === "tl" && "pl-12",
          )}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            {label ? <span className="capsule-label min-w-0 truncate">{label}</span> : null}
            {hint ? <Hint text={hint} label={typeof label === "string" ? `Как считается: ${label}` : undefined} /> : null}
          </span>
          {aside}
        </div>
      )}
      {children}
    </Tag>
  );
}

/** The 12-column bento. Children set their own spans. */
export function Bento({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-1 gap-[var(--gap)] sm:grid-cols-6 xl:grid-cols-12", className)}>
      {children}
    </div>
  );
}

/** Title of a screen, set on the device (not inside a tile). */
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
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3 px-1">
      <div className="min-w-0">
        <h1 className="on-shell text-[26px] font-semibold leading-8 md:text-[30px]">{title}</h1>
        {sub && <p className="on-shell-mute mt-1 max-w-[70ch] text-[13px] leading-5">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A group of tiles with a title set on the device (between bento
    blocks), so a long screen reads as sections rather than one wall. */
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
    <div className={cn("mb-3 mt-8 flex flex-wrap items-end justify-between gap-3 px-1 first:mt-0", className)}>
      <div className="min-w-0">
        <h2 className="on-shell flex items-center gap-2 text-[18px] font-semibold leading-6">
          {title}
          {hint ? <Hint text={hint} label={`Как считается: ${title}`} className="hint-on-shell" /> : null}
        </h2>
        {sub && <p className="on-shell-mute mt-0.5 max-w-[72ch] text-[13px] leading-5">{sub}</p>}
      </div>
      {aside && <div className="flex flex-wrap items-center gap-2">{aside}</div>}
    </div>
  );
}