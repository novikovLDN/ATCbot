/**
 * Hint — a small "?" next to a metric that opens its exact definition.
 *
 * A click/tap popover rather than a hover tooltip: the admin reads the
 * dashboard on a phone as often as on a desktop, and hover does not exist
 * there. The visible circle is 20px; the tap target is 44px (Apple HIG)
 * via the `tap-target` pseudo-element, so it never crowds the label.
 *
 * The popover is position: fixed and clamped to the viewport, so a hint
 * in the rightmost tile of a phone layout does not run off-screen.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

const POP_W = 300;
const MARGIN = 12;

export function Hint({ text, label = "Как считается", className }: { text: ReactNode; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const id = useId();

  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    const width = Math.min(POP_W, vw - MARGIN * 2);
    const left = Math.max(MARGIN, Math.min(r.left + r.width / 2 - width / 2, vw - width - MARGIN));
    const up = r.bottom + 180 > vh && r.top > 200;
    setPos({ left, top: up ? r.top - 8 : r.bottom + 8, up });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      const t = e.target as Node;
      if (pop.current?.contains(t) || btn.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btn.current?.focus();
      }
    };
    const onScroll = () => setOpen(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btn}
        type="button"
        className={cn("hint-btn tap-target", className)}
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        ?
      </button>
      {open && pos && (
        <div
          ref={pop}
          id={id}
          role="tooltip"
          className="hint-pop"
          style={{
            left: pos.left,
            top: pos.top,
            width: Math.min(POP_W, document.documentElement.clientWidth - MARGIN * 2),
            transform: pos.up ? "translateY(-100%)" : undefined,
          }}
        >
          {text}
        </div>
      )}
    </>
  );
}
