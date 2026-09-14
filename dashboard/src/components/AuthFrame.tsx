/**
 * Shared frame for the sign-in and first-setup screens: the same device
 * as the dashboard, narrow, with the brand from GET /api/branding.
 */
import type { ReactNode } from "react";
import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import { useBranding } from "@/lib/branding";

export function AuthFrame({ title, sub, children, footer }: {
  title: string;
  sub: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const brand = useBranding();
  return (
    <div
      className="grid min-h-[100svh] place-items-center p-3 sm:p-6"
      // Standalone iOS app draws under a translucent status bar.
      style={{ paddingTop: "max(0.75rem, env(safe-area-inset-top))", paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
    >
      <main className="device w-full max-w-[440px] p-3 animate-mount-card">
        <div className="flex items-center gap-3 px-2 pb-4 pt-2">
          <span className="icon-btn overflow-hidden">
            {brand.logo_url ? (
              <img src={brand.logo_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <ShieldCheck className="h-[18px] w-[18px] text-accent" strokeWidth={2} />
            )}
          </span>
          <span className="on-shell text-[15px] font-semibold">{brand.admin_title}</span>
        </div>
        <section className="tile p-6">
          <h1 className="text-[26px] font-semibold leading-8">{title}</h1>
          <p className="t-mute mt-2 text-[14px] leading-5">{sub}</p>
          <div className="mt-6">{children}</div>
        </section>
        {footer && <div className="tile tile-raised mt-3 p-4 text-[13px] leading-5">{footer}</div>}
      </main>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="t-body mb-1.5 block text-[13px]">{label}</span>
      <span className="relative block">{children}</span>
      {hint && <span className="mt-1.5 block text-[12px] text-danger">{hint}</span>}
    </label>
  );
}

export function RevealButton({ shown, onToggle }: { shown: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="absolute right-2 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-full text-mute hover:text-ink"
      aria-label={shown ? "Скрыть пароль" : "Показать пароль"}
      aria-pressed={shown}
    >
      {shown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </button>
  );
}

export function FormError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="flex items-start gap-2 rounded-[14px] bg-danger/10 px-3 py-2 text-[13px] text-danger">
      <span className="dot dot-err mt-1.5" aria-hidden="true" />
      {children}
    </p>
  );
}
