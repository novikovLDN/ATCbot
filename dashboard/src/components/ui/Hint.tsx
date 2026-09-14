/**
 * Hint — an ⓘ info button next to a metric that opens its exact
 * definition.
 *
 * On a phone (or any touch screen) the definition opens as an iOS bottom
 * sheet with a «Готово» button; with a mouse on a wide screen it is a
 * small popover under the button. The visible glyph is 18px; the tap
 * target is 44px (Apple HIG) via `tap-target`, so it never crowds the
 * label. Both are portalled to <body>, so no ancestor (a blurred bar, a
 * scrolling table) can clip or offset them.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";
import { cn } from "@/lib/cn";

const POP_W = 320;
const MARGIN = 12;

function wantsSheet(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(max-width: 767px), (pointer: coarse)").matches;
}

export function Hint({ text, label = "Как считается", className }: { text: ReactNode; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const done = useRef<HTMLButtonElement>(null);
  const id = useId();
  const title = label.replace(/^Как считается:\s*/, "");

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) btn.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open || sheet || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    const width = Math.min(POP_W, vw - MARGIN * 2);
    const left = Math.max(MARGIN, Math.min(r.left + r.width / 2 - width / 2, vw - width - MARGIN));
    const up = r.bottom + 200 > vh && r.top > 220;
    setPos({ left, top: up ? r.top - 8 : r.bottom + 8, up });
  }, [open, sheet]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    if (sheet) {
      done.current?.focus();
      const html = document.documentElement;
      const prev = html.style.overflow;
      html.style.overflow = "hidden";
      return () => {
        document.removeEventListener("keydown", onKey);
        html.style.overflow = prev;
      };
    }
    const onDown = (e: Event) => {
      const t = e.target as Node;
      if (pop.current?.contains(t) || btn.current?.contains(t)) return;
      close(false);
    };
    const onScroll = () => close(false);
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sheet]);

  return (
    <>
      <button
        ref={btn}
        type="button"
        className={cn("hint-btn tap-target", className)}
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? id : undefined}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (open) {
            close(false);
            return;
          }
          setSheet(wantsSheet());
          setOpen(true);
        }}
      >
        <Info className="h-[18px] w-[18px]" strokeWidth={2} aria-hidden="true" />
      </button>
      {open &&
        sheet &&
        createPortal(
          <>
            <div className="sheet-backdrop" aria-hidden="true" onClick={() => close()} />
            <div id={id} role="dialog" aria-modal="true" aria-label={label} className="sheet">
              <div className="sheet-grabber" aria-hidden="true" />
              <h2 className="text-[20px] font-semibold leading-[25px]">{title === "Как считается" ? "Как считается" : title}</h2>
              <div className="t-body mt-2 text-[17px] leading-[24px]">{text}</div>
              <button ref={done} type="button" className="btn-secondary mt-5 w-full" onClick={() => close()}>
                Готово
              </button>
            </div>
          </>,
          document.body,
        )}
      {open &&
        !sheet &&
        pos &&
        createPortal(
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
          </div>,
          document.body,
        )}
    </>
  );
}
