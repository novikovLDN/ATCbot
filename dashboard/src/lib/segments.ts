/**
 * Broadcast segment keys: fixed ("paid_lapsed_any") or parametric
 * "<base>:<window>" — window "Nd" (1–3650 days), "Nm" (1–120 calendar
 * months) or "any". Same grammar as database/segments.py (the backend
 * validates again and answers 400 on a bad key).
 */
import type { BroadcastSegment } from "@/lib/api";

export type WindowUnit = "d" | "m";

export interface SegWindow {
  any: boolean;
  n: number;
  unit: WindowUnit;
}

const WINDOW_RE = /^(?:([1-9][0-9]{0,3})([dm])|any)$/;

export function parseWindow(raw: string | null | undefined): SegWindow | null {
  const m = WINDOW_RE.exec(raw ?? "");
  if (!m) return null;
  if (raw === "any") return { any: true, n: 30, unit: "d" };
  return { any: false, n: Number(m[1]), unit: m[2] as WindowUnit };
}

export function windowKey(w: SegWindow): string {
  return w.any ? "any" : `${w.n}${w.unit}`;
}

export function splitSegmentKey(key: string | null | undefined): { base: string; window: string | null } {
  const k = key ?? "";
  const i = k.indexOf(":");
  return i < 0 ? { base: k, window: null } : { base: k.slice(0, i), window: k.slice(i + 1) };
}

export function joinSegmentKey(base: string, w: SegWindow): string {
  return `${base}:${windowKey(w)}`;
}

export function windowMax(unit: WindowUnit, spec: Pick<BroadcastSegment, "max_days" | "max_months">): number {
  return unit === "d" ? spec.max_days ?? 3650 : spec.max_months ?? 120;
}

export function isWindowValid(w: SegWindow, spec: BroadcastSegment): boolean {
  if (w.any) return Boolean(spec.allow_any);
  return Number.isInteger(w.n) && w.n >= 1 && w.n <= windowMax(w.unit, spec);
}

/** Full key of a picked segment: a parametric base gets its default window. */
export function defaultKeyOf(spec: BroadcastSegment): string {
  return spec.parametric && spec.default_window ? `${spec.key}:${spec.default_window}` : spec.key;
}

/** Is `key` a complete, valid key for one of `segments`? */
export function isSegmentKeyValid(key: string, segments: BroadcastSegment[] | undefined): boolean {
  const { base, window } = splitSegmentKey(key);
  const spec = segments?.find((s) => s.key === base);
  if (!spec) return false;
  if (!spec.parametric) return window === null;
  const w = parseWindow(window);
  return w !== null && isWindowValid(w, spec);
}

/** Quick picks under the number input. */
export function presetsFor(direction: BroadcastSegment["direction"]): SegWindow[] {
  const d = (n: number): SegWindow => ({ any: false, n, unit: "d" });
  const m = (n: number): SegWindow => ({ any: false, n, unit: "m" });
  if (direction === "future") return [d(3), d(7), d(14), d(30)];
  if (direction === "idle") return [d(7), d(30), d(90), m(6)];
  return [d(7), d(30), m(3), m(6), m(12)];
}

export function shortWindow(w: SegWindow): string {
  if (w.any) return "всё время";
  return `${w.n} ${w.unit === "d" ? "д" : "мес"}`;
}
