import { useEffect, useState, ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Collapsible — раскрывающаяся секция.
 * Header + опциональный chevron. Открытие через CSS-only
 * grid-template-rows animation (без measure высоты).
 *
 * Props:
 *   title       — левый header
 *   subtitle    — под-title справа (мелкий, muted)
 *   defaultOpen — открыт ли изначально
 *   remember    — сохранять состояние в localStorage под этим ключом
 *   badge       — маленький chip справа (напр. "3 новых" / "beta")
 */
export function Collapsible({
  title,
  subtitle,
  children,
  defaultOpen = false,
  remember,
  badge,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  remember?: string;
  badge?: ReactNode;
  className?: string;
}) {
  const storageKey = remember ? `collapsible:${remember}` : null;
  const [open, setOpen] = useState<boolean>(() => {
    if (!storageKey) return defaultOpen;
    try {
      const v = localStorage.getItem(storageKey);
      if (v === "1") return true;
      if (v === "0") return false;
    } catch {
      /* localStorage disabled — fall through */
    }
    return defaultOpen;
  });

  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [open, storageKey]);

  return (
    <section className={cn("space-y-2", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "tap-scale no-tap-highlight",
          "flex w-full items-center justify-between gap-3 rounded-xl",
          "border border-border bg-bg-card px-4 py-2.5 text-left",
          "transition-colors hover:border-fg/15 hover:bg-bg-subtle/40",
        )}
        aria-expanded={open}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
              {title}
            </span>
            {badge && <span className="shrink-0">{badge}</span>}
          </div>
          {subtitle && (
            <div className="mt-0.5 text-[11px] text-fg-subtle">{subtitle}</div>
          )}
        </div>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-fg-subtle transition-transform duration-300 ease-out",
            open && "rotate-180 text-fg-muted",
          )}
        />
      </button>

      <div className="collapsible" data-open={open ? "true" : "false"}>
        <div className="collapsible-inner">{children}</div>
      </div>
    </section>
  );
}
