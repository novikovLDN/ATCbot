/**
 * iOS bottom sheet with Cancel / Confirm. Escape and the backdrop cancel.
 * Shared by the Settings job cards (Remnawave tags, premium > 5 years).
 */
import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Spinner } from "@/components/Spinner";

export function ConfirmSheet({
  title,
  children,
  confirmLabel,
  danger,
  pending,
  confirmDisabled,
  onCancel,
  onConfirm,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  pending?: boolean;
  confirmDisabled?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  // onCancel is a new function on every render (status polls every 2 s): keep
  // it in a ref so the effect runs once and never steals focus back.
  const cancelFn = useRef(onCancel);
  cancelFn.current = onCancel;
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && cancelFn.current();
    document.addEventListener("keydown", onKey);
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      html.style.overflow = prev;
    };
  }, []);

  return createPortal(
    <>
      <div className="sheet-backdrop" aria-hidden="true" onClick={onCancel} />
      <div role="dialog" aria-modal="true" aria-label={title} className="sheet">
        <div className="sheet-grabber" aria-hidden="true" />
        <div className="mx-auto max-w-[520px]">
          <h2 className="text-[20px] font-semibold leading-[25px]">{title}</h2>
          <div className="t-body mt-2 text-[17px] leading-[24px]">{children}</div>
          <div className="mt-5 grid grid-cols-2 gap-2">
            <button ref={cancelRef} type="button" className="btn-secondary w-full" onClick={onCancel}>
              Отмена
            </button>
            <button
              type="button"
              className={danger ? "btn-danger w-full" : "btn-primary w-full"}
              onClick={onConfirm}
              disabled={pending || confirmDisabled}
            >
              {pending && <Spinner />}
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
