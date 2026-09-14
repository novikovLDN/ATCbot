/**
 * Sparkline — a trend inside a stat tile.
 *
 * No axes, no gridlines, no labels: at 28–32px tall none of them are
 * legible, and the shape is the whole point. A full chart earns its
 * space two or three times per screen; every other metric gets this.
 *
 * Renders every point it is given, including zero days. A series with
 * gaps silently rescales its x-axis, so a quiet week reads as a cliff.
 *
 * Feed it aggregated data. This renders SVG, and so does Recharts: a
 * series of 5000 points is 5000 DOM nodes and a frozen tab. Thirty days
 * is thirty points, not 43200 minutes.
 */
import { useId } from "react";

interface SparklineProps {
  /** Values in chronological order. Fewer than two points renders flat. */
  data: number[];
  /** Line colour. Any CSS colour, including a var(). */
  color?: string;
  height?: number;
  className?: string;
  /** Marks the final point. Off for dense grids where dots collide. */
  showEndDot?: boolean;
}

export function Sparkline({
  data,
  color = "var(--accent)",
  height = 32,
  className = "",
  showEndDot = true,
}: SparklineProps) {
  // The gradient needs a document-unique id. It used to be derived from
  // the colour string, which silently collided whenever two tiles shared
  // a colour — and broke outright once colours became var(--token), since
  // parentheses are not valid in an id.
  const gradientId = useId().replace(/:/g, "");

  // A single point has no shape to show, and dividing by a zero range
  // below would produce NaN coordinates and an invisible path.
  if (!data || data.length < 2) {
    return <div className={className} style={{ height }} aria-hidden="true" />;
  }

  const w = 100;
  const h = 100;
  const min = Math.min(...data);
  const max = Math.max(...data);
  // Flat series (all zeros, or a plateau) would divide by zero; draw it
  // along the middle instead of collapsing onto the baseline, where it
  // would be indistinguishable from the container edge.
  const range = max - min || 1;
  const flat = max === min;

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = flat ? h / 2 : h - ((v - min) / range) * h;
    return [x, y] as const;
  });

  const path = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(" ");
  const area = `${path} L${w},${h} L0,${h} Z`;
  const [lastX, lastY] = points[points.length - 1];

  return (
    <svg
      className={className}
      style={{ height, width: "100%", display: "block" }}
      viewBox={`0 0 ${w} ${h}`}
      // The stroke must not thicken when the tile stretches.
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.16} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        // preserveAspectRatio="none" scales the stroke with the box;
        // this keeps it 2px regardless of tile width.
        vectorEffect="non-scaling-stroke"
      />
      {showEndDot && (
        <circle
          cx={lastX}
          cy={lastY}
          r={3}
          fill={color}
          stroke="var(--surface-1)"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
