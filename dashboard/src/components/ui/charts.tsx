/**
 * Monochrome charts. Every series is gray except the one that answers
 * the screen's question, which is drawn in the accent. Gridlines are
 * faint and horizontal only; the tooltip is a dark capsule.
 *
 * Feed aggregated points (days, weeks, nodes) — never raw events.
 */
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";

export interface Series {
  key: string;
  label: string;
  highlight?: boolean;
}

const ACCENT = "rgb(var(--c-accent))";
const MUTE = "rgb(var(--c-mute))";
const GRID = "rgb(var(--c-ink) / 0.07)";
const TICK = { fill: "rgb(var(--c-mute))", fontSize: 11 };

function CapsuleTooltip({
  active,
  payload,
  label,
  format,
  labelFormat,
}: TooltipProps<number, string> & {
  format: (v: number) => string;
  labelFormat?: (l: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-[14px] bg-tile-1 px-3 py-2 text-[12px] text-ink shadow-[0_8px_24px_rgb(0_0_0/0.35)]">
      <div className="t-mute mb-1">{labelFormat ? labelFormat(String(label)) : label}</div>
      {payload.map((p) => (
        <div key={String(p.dataKey)} className="flex items-center justify-between gap-4">
          <span className="t-body">{p.name}</span>
          <span className="tabular font-semibold">{format(Number(p.value ?? 0))}</span>
        </div>
      ))}
    </div>
  );
}

export function TrendChart<T extends Record<string, unknown>>({
  data,
  x,
  series,
  height = 220,
  format,
  xFormat,
  yFormat,
}: {
  data: T[];
  x: keyof T & string;
  series: Series[];
  height?: number;
  format: (v: number) => string;
  xFormat?: (v: string) => string;
  yFormat?: (v: number) => string;
}) {
  const highlight = series.find((s) => s.highlight) ?? series[0];
  const rest = series.filter((s) => s !== highlight);
  return (
    <div style={{ height }} role="img" aria-label={`График: ${series.map((s) => s.label).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={ACCENT} stopOpacity={0.22} />
              <stop offset="100%" stopColor={ACCENT} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis
            dataKey={x}
            tick={TICK}
            tickLine={false}
            axisLine={false}
            tickFormatter={xFormat}
            minTickGap={24}
          />
          <YAxis
            tick={TICK}
            tickLine={false}
            axisLine={false}
            width={56}
            tickFormatter={yFormat ?? format}
          />
          <Tooltip
            cursor={{ stroke: "rgb(var(--c-ink) / 0.2)" }}
            content={<CapsuleTooltip format={format} labelFormat={xFormat} />}
          />
          {rest.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={MUTE}
              strokeDasharray="4 4"
              strokeWidth={1.5}
              dot={false}
            />
          ))}
          <Area
            type="monotone"
            dataKey={highlight.key}
            name={highlight.label}
            stroke={ACCENT}
            strokeWidth={2}
            fill="url(#trend-fill)"
            activeDot={{ r: 4, fill: ACCENT, stroke: "rgb(var(--c-tile-1))", strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Bars in gray; the maximum (or `highlightIndex`) in the accent. */
export function BarsChart<T extends Record<string, unknown>>({
  data,
  x,
  y,
  label,
  height = 180,
  format,
  xFormat,
  highlightIndex,
}: {
  data: T[];
  x: keyof T & string;
  y: keyof T & string;
  label: string;
  height?: number;
  format: (v: number) => string;
  xFormat?: (v: string) => string;
  highlightIndex?: number;
}) {
  let hi = highlightIndex;
  if (hi === undefined && data.length) {
    let max = -Infinity;
    data.forEach((d, i) => {
      const v = Number(d[y] ?? 0);
      if (v > max) {
        max = v;
        hi = i;
      }
    });
  }
  return (
    <div style={{ height }} role="img" aria-label={`Столбцы: ${label}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey={x} tick={TICK} tickLine={false} axisLine={false} tickFormatter={xFormat} interval="preserveStartEnd" />
          <YAxis hide />
          <Tooltip
            cursor={{ fill: "rgb(var(--c-ink) / 0.05)" }}
            content={<CapsuleTooltip format={format} labelFormat={xFormat} />}
          />
          <Bar dataKey={y} name={label} radius={[8, 8, 8, 8]} maxBarSize={28}>
            {data.map((_, i) => (
              <Cell key={i} fill={i === hi ? ACCENT : "rgb(var(--c-ink) / 0.22)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal share bars — length encodes share, read pre-attentively. */
export function ShareList({
  rows,
  format,
  emptyText = "За период нет данных.",
}: {
  rows: { key: string; label: string; value: number; note?: string; highlight?: boolean }[];
  format: (v: number) => string;
  emptyText?: string;
}) {
  const total = rows.reduce((s, r) => s + Math.max(r.value, 0), 0);
  if (!rows.length || total === 0) return <p className="t-mute py-4 text-[13px]">{emptyText}</p>;
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((r) => {
        const pct = (Math.max(r.value, 0) / total) * 100;
        return (
          <li key={r.key}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3 text-[13px]">
              <span className="min-w-0 truncate">
                {r.label}
                {r.note && <span className="t-mute ml-2 text-[12px]">{r.note}</span>}
              </span>
              <span className="tabular flex-none font-medium">
                {format(r.value)}
                <span className="t-mute ml-2 font-normal">{pct.toFixed(0)}%</span>
              </span>
            </div>
            <div className="pill-track h-2">
              <div
                className="pill-fill"
                style={{
                  width: `${Math.max(pct, 1.5)}%`,
                  background: r.highlight ? "rgb(var(--c-accent))" : undefined,
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
