/**
 * Period picker of a parametric broadcast segment: number + [дней | месяцев]
 * + «За всё время», quick picks, and the live audience of the composed key
 * (GET /broadcasts/segments/count, debounced). Emits only valid full keys.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Users as UsersIcon } from "lucide-react";
import { endpoints, type BroadcastSegment } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/Spinner";
import { Segmented, Switch } from "@/components/ui/controls";
import {
  isWindowValid,
  joinSegmentKey,
  parseWindow,
  presetsFor,
  shortWindow,
  splitSegmentKey,
  windowKey,
  windowMax,
  type SegWindow,
  type WindowUnit,
} from "@/lib/segments";

const UNIT_OPTIONS: { value: WindowUnit; label: string }[] = [
  { value: "d", label: "дней" },
  { value: "m", label: "месяцев" },
];

const PREFIX = { past: "За последние", future: "В ближайшие", idle: "Не заходил" } as const;

export function useDebounced<T>(value: T, ms = 400): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Audience of a full key; null key → nothing asked. Keeps the previous
    number on screen while the next one loads. */
export function useSegmentCount(key: string | null) {
  const k = useDebounced(key, 400);
  const q = useQuery({
    queryKey: ["broadcasts", "segment-count", k],
    queryFn: () => endpoints.broadcastSegmentCount(k as string),
    enabled: Boolean(k),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
  const settled = k === key && !q.isPlaceholderData;
  return {
    count: q.data && q.data.count >= 0 ? q.data.count : null,
    label: settled ? q.data?.label ?? null : null,
    pending: k !== key || q.isFetching,
    error: q.isError ? q.error : null,
  };
}

function fallbackWindow(spec: BroadcastSegment): SegWindow {
  return parseWindow(spec.default_window) ?? { any: false, n: 30, unit: "d" };
}

export function SegmentWindowPicker({
  spec,
  value,
  onChange,
}: {
  spec: BroadcastSegment;
  value: string;
  onChange: (key: string) => void;
}) {
  const direction = spec.direction ?? "past";
  const initial = parseWindow(splitSegmentKey(value).window) ?? fallbackWindow(spec);
  const [draft, setDraft] = useState(String(initial.n));
  const [unit, setUnit] = useState<WindowUnit>(initial.unit);
  const [any, setAny] = useState(initial.any);

  // The key changed from outside (another base picked, a clone): follow it.
  // An `any` key keeps the number the admin typed.
  useEffect(() => {
    const w = parseWindow(splitSegmentKey(value).window);
    if (!w) return;
    setAny(w.any);
    if (!w.any) {
      setDraft(String(w.n));
      setUnit(w.unit);
    }
  }, [value]);

  const n = Number(draft);
  const current: SegWindow = { any, n, unit };
  const valid = isWindowValid(current, spec);
  const max = windowMax(unit, spec);

  const emit = (next: SegWindow) => {
    if (isWindowValid(next, spec)) onChange(joinSegmentKey(spec.key, next));
  };

  const count = useSegmentCount(valid ? joinSegmentKey(spec.key, current) : null);

  return (
    <div className="flex flex-col gap-3 rounded-row bg-tile-2 p-3">
      <div className={cn("flex flex-wrap items-center gap-2", any && "pointer-events-none opacity-50")}>
        <span className="text-[14px]">{PREFIX[direction]}</span>
        <input
          className="input tabular w-[84px]"
          type="number"
          inputMode="numeric"
          min={1}
          max={max}
          step={1}
          value={draft}
          disabled={any}
          aria-label="Сколько"
          aria-invalid={!any && !valid}
          onChange={(e) => {
            setDraft(e.target.value);
            emit({ any: false, n: Number(e.target.value), unit });
          }}
        />
        <Segmented
          label="Единица периода"
          value={unit}
          options={UNIT_OPTIONS}
          onChange={(u) => {
            const clamped = Math.min(Math.max(Math.trunc(n) || 1, 1), windowMax(u, spec));
            setUnit(u);
            setDraft(String(clamped));
            emit({ any: false, n: clamped, unit: u });
          }}
        />
        {direction === "idle" && <span className="text-[14px]">и дольше</span>}
      </div>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Быстрый выбор периода">
        {presetsFor(direction).map((p) => {
          const active = !any && p.n === n && p.unit === unit;
          return (
            <button
              key={windowKey(p)}
              type="button"
              aria-pressed={active}
              className={cn("badge tap-target", active ? "badge-accent" : "bg-tile-1 t-body")}
              onClick={() => {
                setAny(false);
                setDraft(String(p.n));
                setUnit(p.unit);
                emit(p);
              }}
            >
              {shortWindow(p)}
            </button>
          );
        })}
      </div>

      {spec.allow_any && (
        <div className="flex min-h-[44px] items-center justify-between gap-3">
          <span className="text-[14px]">За всё время</span>
          <Switch
            label="За всё время"
            checked={any}
            onChange={(on) => {
              if (on) {
                setAny(true);
                emit({ any: true, n, unit });
                return;
              }
              const back = isWindowValid({ any: false, n, unit }, spec)
                ? { any: false, n, unit }
                : fallbackWindow(spec);
              setAny(false);
              setDraft(String(back.n));
              setUnit(back.unit);
              emit(back);
            }}
          />
        </div>
      )}

      {!any && !valid ? (
        <p role="alert" className="text-[13px] text-danger">
          Введите целое число от 1 до {max}.
        </p>
      ) : (
        <div className="flex items-center justify-between gap-3 text-[13px]" aria-live="polite">
          <span className="t-mute min-w-0">{count.label ?? "Считаю аудиторию…"}</span>
          <span className="badge-accent tabular inline-flex shrink-0 items-center gap-1">
            {count.pending ? <Spinner /> : <UsersIcon className="h-3 w-3" />}
            {count.count !== null ? fmtNum(count.count) : "—"}
          </span>
        </div>
      )}
    </div>
  );
}
