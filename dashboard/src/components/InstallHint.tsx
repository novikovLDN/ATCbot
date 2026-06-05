import { useEffect, useState } from "react";
import { Share, X, Smartphone } from "lucide-react";

/**
 * One-time onboarding hint for iOS Safari users:
 * "Чтобы получить иконку на экран — Поделиться → На экран Домой".
 *
 * Apple doesn't expose any programmatic install prompt on iOS Safari
 * (unlike Android Chrome's beforeinstallprompt), so the best we can
 * do is show a contextual nudge the first time the admin opens the
 * dashboard on iPhone in Safari. Dismissed → persists in localStorage.
 *
 * Hides automatically when already running as an installed PWA
 * (display-mode: standalone) or if the user previously closed it.
 */
const KEY = "atlas.admin.installhint.dismissed";

function isIosSafari(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  const isIDevice = /iPhone|iPod/i.test(ua) || (/iPad/i.test(ua) || (navigator.platform === "MacIntel" && (navigator as any).maxTouchPoints > 1));
  if (!isIDevice) return false;
  // Reject non-Safari browsers (CriOS = Chrome, FxiOS = Firefox, etc.)
  if (/CriOS|FxiOS|EdgiOS|OPiOS|YaBrowser|GSA/i.test(ua)) return false;
  return /Safari/i.test(ua);
}

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  // iOS legacy
  if ((window.navigator as any).standalone) return true;
  // Web standard
  if (window.matchMedia?.("(display-mode: standalone)").matches) return true;
  return false;
}

export function InstallHint() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (isStandalone()) return;
    try {
      if (localStorage.getItem(KEY) === "1") return;
    } catch {
      //
    }
    if (!isIosSafari()) return;
    // Delay so the user sees the dashboard first and isn't startled.
    const t = window.setTimeout(() => setShow(true), 1500);
    return () => window.clearTimeout(t);
  }, []);

  if (!show) return null;

  return (
    <div className="fixed inset-x-3 bottom-[88px] z-50 md:hidden animate-slide-up">
      <div className="card flex items-start gap-3 p-3 pr-2 shadow-[0_12px_32px_-8px_rgba(0,0,0,0.6)]">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent">
          <Smartphone className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-fg">
            Добавить как приложение?
          </div>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-fg-muted">
            Нажми{" "}
            <Share className="inline h-3.5 w-3.5 text-accent" /> внизу Safari →
            «На экран Домой»
          </div>
        </div>
        <button
          type="button"
          onClick={() => {
            try {
              localStorage.setItem(KEY, "1");
            } catch {
              //
            }
            setShow(false);
          }}
          className="rounded-md p-1 text-fg-muted hover:bg-bg-elevated hover:text-fg"
          aria-label="Закрыть"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
