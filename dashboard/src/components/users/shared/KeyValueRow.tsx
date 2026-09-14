/**
 * One labelled fact: label on the left, value on the right.
 *
 * Replaces the page-local `Row`, which took `value: string` and so forced
 * every caller to stringify first — that is why dates, booleans and null
 * all arrived here as the literal text "undefined" in places. Taking a
 * ReactNode lets a badge or a copy button sit in the value slot without
 * a second component.
 *
 * `mono` exists because UUIDs, keys and URLs are read character by
 * character when someone is comparing them against a provider dashboard,
 * and a proportional font makes that materially harder.
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type RowTone = "default" | "muted" | "ok" | "warn" | "err";

const TONE_CLASS: Record<RowTone, string> = {
  default: "text-ink",
  muted: "t-mute",
  ok: "text-success",
  warn: "text-warning",
  err: "text-danger",
};

export interface KeyValueRowProps {
  label: string;
  value: ReactNode;
  /** Renders the value in a monospace face and truncates it. */
  mono?: boolean;
  tone?: RowTone;
  /** Native tooltip — the full value when the visible one is truncated. */
  title?: string;
  /** Trailing slot, in practice a CopyButton. */
  action?: ReactNode;
}

export function KeyValueRow({
  label,
  value,
  mono = false,
  tone = "default",
  title,
  action,
}: KeyValueRowProps) {
  const empty = value === null || value === undefined || value === "";

  return (
    <div className="flex min-h-[28px] items-center justify-between gap-3 text-[13px]">
      <span className="t-mute shrink-0">{label}</span>
      <span className="flex min-w-0 items-center gap-1">
        <span
          className={cn(
            "min-w-0 truncate text-right font-medium",
            // Tabular figures so a column of these lines up by place
            // value rather than drifting with the digit widths.
            mono ? "font-mono text-[12px]" : "tabular",
            empty ? "text-ash" : TONE_CLASS[tone],
          )}
          title={title ?? (typeof value === "string" ? value : undefined)}
        >
          {empty ? "—" : value}
        </span>
        {action}
      </span>
    </div>
  );
}
