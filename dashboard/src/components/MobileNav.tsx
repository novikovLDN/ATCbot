import { useState, useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { MoreHorizontal, X, LogOut } from "lucide-react";
import { cn } from "@/lib/cn";
import { auth } from "@/lib/auth";
import { endpoints } from "@/lib/api";
import { NAV } from "@/lib/nav";

/**
 * Нижняя навигация телефона: четыре пункта и «Ещё».
 *
 * Набор основных пунктов пересобран по research §9.4. «Рассылки» из нижней
 * панели убраны сознательно: отправку рассылки нельзя отменить, и ставить её
 * в один ряд с просмотром — приглашение к беде на ходу. Их место заняли
 * «События»: посмотреть, что произошло, — как раз мобильная задача. Сами
 * рассылки никуда не делись, они в «Ещё».
 *
 * Отступ безопасной зоны считается ровно один раз — здесь. Раньше он был и в
 * body, и в контейнере контента, и на айфоне с вырезом набегало около 100 px
 * пустоты сверху и лишняя полоса снизу.
 */

const PRIMARY = NAV.filter((s) => s.mobilePrimary);
const MORE = NAV.filter((s) => !s.mobilePrimary);

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const sheetRef = useRef<HTMLDivElement>(null);

  // Закрываем панель на каждый переход.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  // Блокируем прокрутку страницы, пока панель открыта, иначе iOS тянет фон
  // резинкой. Плюс Esc — на телефоне с клавиатурой это тоже работает.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sheetRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const inMore = MORE.some(
    (it) => location.pathname === it.to || location.pathname.startsWith(it.to + "/"),
  );

  return (
    <>
      <nav
        aria-label="Основные разделы"
        className="fixed inset-x-2 z-30 flex justify-around rounded-xl border border-border bg-bg-card/95 px-1 py-1 backdrop-blur md:hidden"
        style={{ bottom: "max(0.5rem, env(safe-area-inset-bottom))" }}
      >
        {PRIMARY.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.to === "/"}
            className={({ isActive }) =>
              cn(
                "flex min-h-tap-touch flex-1 flex-col items-center justify-center gap-0.5 rounded-md px-1 py-1 text-2xs transition-colors",
                isActive
                  ? "bg-accent-9 font-medium text-white"
                  : "text-fg-subtle hover:text-fg",
              )
            }
          >
            <it.icon className="h-4 w-4" strokeWidth={2.25} aria-hidden />
            {it.label}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={open}
          className={cn(
            "flex min-h-tap-touch flex-1 flex-col items-center justify-center gap-0.5 rounded-md px-1 py-1 text-2xs transition-colors",
            inMore ? "bg-accent-9 font-medium text-white" : "text-fg-subtle hover:text-fg",
          )}
        >
          <MoreHorizontal className="h-4 w-4" strokeWidth={2.25} aria-hidden />
          Ещё
        </button>
      </nav>

      {open && (
        <div
          className="fixed inset-0 z-40 flex items-end bg-bg-overlay/50 md:hidden"
          onClick={() => setOpen(false)}
        >
          <div
            ref={sheetRef}
            role="dialog"
            aria-modal="true"
            aria-label="Остальные разделы"
            tabIndex={-1}
            className="w-full rounded-t-xl border-x border-t border-border bg-bg-card shadow-lg animate-slide-up"
            style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border" />
            <div className="flex items-center justify-between px-4 pb-2 pt-3">
              <h2 className="text-lg font-semibold text-fg">Остальные разделы</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Закрыть"
                className="grid h-11 w-11 place-items-center rounded-md text-fg-muted hover:bg-bg-subtle hover:text-fg"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>

            <ul className="grid grid-cols-2 gap-2 px-4 py-2">
              {MORE.map((it) => (
                <li key={it.to}>
                  <NavLink
                    to={it.to}
                    className={({ isActive }) =>
                      cn(
                        "flex min-h-tap-touch items-center gap-2.5 rounded-md border px-3 py-3 text-base transition-colors",
                        isActive
                          ? "border-accent-7 bg-accent-3 font-medium text-accent-text"
                          : "border-border bg-bg-card text-fg hover:bg-bg-subtle",
                      )
                    }
                  >
                    <it.icon className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
                    <span className="truncate">{it.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>

            <div className="px-4 pb-2 pt-3">
              <button
                type="button"
                onClick={async () => {
                  try {
                    await endpoints.authLogout();
                  } catch {
                    //
                  }
                  auth.clear();
                  window.location.assign("/dashboard/");
                }}
                className="flex min-h-tap-touch w-full items-center justify-center gap-2 rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-base font-medium text-danger transition-colors hover:bg-danger/15"
              >
                <LogOut className="h-4 w-4" aria-hidden />
                Выйти
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
