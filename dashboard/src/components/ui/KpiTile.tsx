/**
 * KpiTile — label capsule, a large numeral, the change against the
 * previous period, an optional sparkline. `size="hero"` is for the one
 * figure a screen is about.
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
  /** Exact definition — shown behind a "?" next to the label. */
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
  hero: "text-[44px] leading-[48px] md:text-[56px] md:leading-[60px] track-hero",
  md: "text-[30px] leading-[36px] track-metric",
  sm: "text-[22px] leading-[28px] track-metric",
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
  variant = "ink",
  size = "md",
  to,
  notch,
  loading,
  className,
  footer,
}: KpiTileProps) {
  const onLight = variant === "accent" || variant === "fog" || variant === "mist";
  return (
    <Surface
      variant={variant}
      to={to}
      toLabel={label}
      notch={notch}
      label={label}
      hint={hint}
      className={cn("flex flex-col", className)}
    >
      <div>
        {loading ? (
          <div className={cn("skeleton", size === "hero" ? "h-14 w-48" : "h-9 w-28")} />
        ) : (
          <div className={cn("tabular font-semibold", VALUE_SIZE[size])}>{value}</div>
        )}
        {sub && <div className="t-mute mt-1.5 text-[13px] leading-5">{sub}</div>}
      </div>
      {(delta !== undefined || (trend && trend.length > 1)) && (
        <div className="mt-auto flex items-end justify-between gap-4 pt-4">
          {delta !== undefined ? (
            <DeltaPill pct={delta} period={deltaPeriod} higherIsBetter={higherIsBetter} />
          ) : (
            <span />
          )}
          {trend && trend.length > 1 && (
            <div className="w-[45%] max-w-[180px] flex-none">
              <Sparkline
                data={trend}
                height={size === "hero" ? 48 : 32}
                color={onLight ? "currentColor" : "rgb(var(--c-accent))"}
                showEndDot={false}
              />
            </div>
          )}
        </div>
      )}
      {footer && <div className="mt-4">{footer}</div>}
    </Surface>
  );
}
