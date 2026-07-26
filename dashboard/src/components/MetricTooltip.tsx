import { useEffect, useRef, useState, ReactNode } from "react";

/**
 * MetricTooltip — inline (?) кнопка с подсказкой о метрике.
 * На десктопе показывается по hover, на мобильных — по tap.
 * Позиционируется через absolute; закрывается кликом вне.
 *
 * Пример:
 *   <MetricTooltip text="ARPU = revenue / paying_users за N дней">
 *     ARPU
 *   </MetricTooltip>
 */
export function MetricTooltip({
  text,
  children,
  side = "top",
}: {
  text: string;
  children?: ReactNode;
  side?: "top" | "bottom";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <span className="inline-flex items-center gap-1 align-middle" ref={ref}>
      {children}
      <span
        role="button"
        tabIndex={0}
        className="hint-dot"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        aria-label="Что это?"
      >
        ?
      </span>
      {open && (
        <span
          className={
            side === "bottom"
              ? "absolute z-40 mt-6 max-w-[240px] rounded-lg border border-border bg-bg-card px-3 py-2 text-[11px] leading-snug text-fg shadow-matte animate-fade-in"
              : "absolute z-40 -mt-2 -translate-y-full max-w-[240px] rounded-lg border border-border bg-bg-card px-3 py-2 text-[11px] leading-snug text-fg shadow-matte animate-fade-in"
          }
          style={{ pointerEvents: "none" }}
        >
          {text}
        </span>
      )}
    </span>
  );
}
