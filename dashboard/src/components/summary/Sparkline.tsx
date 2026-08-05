import { useId, useMemo } from "react";
import { cn } from "@/lib/cn";

/**
 * Спарклайн — 30 точек выручки рядом с главным числом.
 *
 * ПОЧЕМУ РУКАМИ, А НЕ RECHARTS
 * Recharts в проекте уже есть и остаётся на «Аналитике». Но он приезжает
 * отдельным чанком на 113 КБ gzip, и подтягивать его ради тридцати точек
 * значило бы сделать первый экран самым тяжёлым в приложении. Здесь нет
 * ни осей, ни легенды, ни подсказок — только форма кривой, и на неё
 * хватает одного <path>. Новой зависимости при этом не заводится.
 *
 * ЧТО ЗДЕСЬ ВАЖНО НЕ СЛОМАТЬ
 * viewBox фиксированный, размеры задаёт CSS: график тянется вместе с
 * карточкой без пересчёта в JS и без ResizeObserver.
 *
 * Ряд из одинаковых значений (в том числе из одних нулей) рисуется прямой
 * по центру, а не по нижней кромке: деление на нулевой размах дало бы
 * NaN в координатах, и путь просто исчез бы — без ошибки в консоли.
 *
 * Цвет не несёт смысла: линия одна, сравнивать нечего. Смысл несёт подпись
 * рядом с числом.
 */

const W = 240;
const H = 56;
const PAD = 3;

export function Sparkline({
  points,
  className,
  label,
}: {
  points: Array<{ date: string; rubles: number }>;
  className?: string;
  /** Текст для скринридера: график сам по себе ему ничего не говорит. */
  label: string;
}) {
  const gradientId = useId();

  const geometry = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => p.rubles);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min;
    const stepX = (W - PAD * 2) / (points.length - 1);

    const y = (v: number) =>
      span === 0 ? H / 2 : H - PAD - ((v - min) / span) * (H - PAD * 2);

    const coords = values.map((v, i) => [PAD + i * stepX, y(v)] as const);
    const line = coords
      .map(([x, yy], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${yy.toFixed(1)}`)
      .join(" ");
    const area = `${line} L${coords[coords.length - 1][0].toFixed(1)} ${H} L${coords[0][0].toFixed(1)} ${H} Z`;
    const last = coords[coords.length - 1];
    return { line, area, last };
  }, [points]);

  if (!geometry) {
    // Одна точка или ни одной — рисовать нечего. Пустое место честнее
    // прямой линии, которая читалась бы как «выручка не менялась».
    return (
      <div
        className={cn("h-14 rounded-md border border-dashed border-border", className)}
        role="img"
        aria-label="Данных за период пока нет"
      />
    );
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={cn("h-14 w-full text-accent-9", className)}
      role="img"
      aria-label={label}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.18" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={geometry.area} fill={`url(#${gradientId})`} />
      <path
        d={geometry.line}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* Последний день — точка. Без неё непонятно, где «сейчас». */}
      <circle cx={geometry.last[0]} cy={geometry.last[1]} r="2.5" fill="currentColor" />
    </svg>
  );
}
