import { useCallback, useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Модальное окно.
 *
 * Что здесь обязательно и почему:
 *  - Фокус переводится внутрь окна на открытии и возвращается на элемент,
 *    который его открыл, на закрытии. Без этого клавиатурный пользователь
 *    остаётся «под» окном (WAI-ARIA APG, ux-patterns §1.5).
 *  - Tab ходит по кругу внутри окна и наружу не выходит.
 *  - Esc закрывает; закрытие по клику на подложку можно выключить — для
 *    диалогов, где случайный промах мимо кнопки не должен отменять ввод.
 *  - Скролл страницы блокируется, пока окно открыто.
 *  - Скелетона у модалки не бывает: Carbon запрещает это дословно
 *    (ux-patterns §3.3). Пока данные грузятся — крутилка с текстом внутри окна.
 */

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  children,
  size = "md",
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** Одна строка под заголовком: что произойдёт, с чем именно. */
  description?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** false — закрывается только кнопкой; клик по подложке игнорируется. */
  dismissible?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descId = useId();

  // Запоминаем, откуда пришли, и возвращаем фокус туда же при закрытии.
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();
    return () => restoreRef.current?.focus?.();
  }, [open]);

  // Блокировка прокрутки фона: иначе колесо мыши уводит страницу под окном.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null,
      );
      if (items.length === 0) return;
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      // Замыкаем круг вручную: браузер сам за пределы окна выпустит.
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    },
    [onClose],
  );

  if (!open) return null;

  const width = size === "sm" ? "max-w-sm" : size === "lg" ? "max-w-2xl" : "max-w-md";

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-6"
      onKeyDown={onKeyDown}
    >
      <div
        className="absolute inset-0 bg-bg-overlay/50"
        onClick={dismissible ? onClose : undefined}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={cn(
          "relative z-10 w-full animate-slide-up rounded-xl border border-border bg-bg-card shadow-lg",
          "max-h-[90vh] overflow-y-auto outline-none",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold text-fg">
              {title}
            </h2>
            {description && (
              <p id={descId} className="mt-1 text-base text-fg-muted">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="-mr-1 -mt-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        {children && <div className="px-4 pb-4">{children}</div>}

        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border-subtle px-4 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
