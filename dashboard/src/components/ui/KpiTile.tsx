/**
 * KpiTile — a card with one figure: title, a large numeral, the change
 * against the previous period and an optional sparkline. `size="hero"`
 * is for the one figure a screen is about.
 */
import type { ReactNode } from "react";
import { Sparkline } from "@/components/Sparkline";
import { cn } from "@/lib/cn";
import { Surface, type Corner, type SurfaceVariant } from "./Surface";
import { DeltaPill } from "./controls";

export interface KpiTileProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  delta?: number | null;
  deltaPeriod?: string;
  higherIsBetter?: boolean;
  trend?: number[];
  /** Exact definition — shown behind an ⓘ next to the title. */
  hint?: ReactNode;
  variant?: SurfaceVariant;
  size?: "hero" | "md" | "sm";
  to?: string;
  notch?: Corner;
  loading?: boolean;
  className?: string;
  footer?: ReactNode;
}

const VALUE_SIZE = {
  hero: "text-[34px] leading-[41px] font-bold md:text-[40px] md:leading-[48px] track-hero",
  md: "text-[28px] leading-[34px] font-bold track-metric",
  sm: "text-[22px] leading-[28px] font-semibold track-metric",
};

export function KpiTile({
  label,
  value,
  sub,
  delta,
  deltaPeriod,
  higherIsBetter = true,
  trend,
  hint,
  size = "md",
  to,
  loading,
  className,
  footer,
}: KpiTileProps) {
  return (
    <Surface to={to} toLabel={label} label={label} hint={hint} className={cn("flex flex-col", className)}>
      <div>
        {loading ? (
          <div className={cn("skeleton", size === "hero" ? "h-10 w-44" : "h-8 w-28")} />
        ) : (
          <div className={cn("tabular", VALUE_SIZE[size])}>{value}</div>
        )}
        {sub && <div className="t-mute mt-1 text-[13px] leading-[18px]">{sub}</div>}
      </div>
      {(delta !== undefined || (trend && trend.length > 1)) && (
        <div className="mt-auto flex items-end justify-between gap-4 pt-3">
          {delta !== undefined ? <DeltaPill pct={delta} period={deltaPeriod} higherIsBetter={higherIsBetter} /> : <span />}
          {trend && trend.length > 1 && (
            <div className="w-[42%] max-w-[180px] flex-none">
              <Sparkline data={trend} height={size === "hero" ? 40 : 28} color="rgb(var(--c-accent))" showEndDot={false} />
            </div>
          )}
        </div>
      )}
      {footer && <div className="mt-3">{footer}</div>}
    </Surface>
  );
}
