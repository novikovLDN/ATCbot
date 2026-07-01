import { useState, useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  CreditCard,
  Megaphone,
  MoreHorizontal,
  X,
  TrendingUp,
  Share2,
  Gift,
  Tag,
  ScrollText,
  Wrench,
  Settings as SettingsIcon,
  LogOut,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { auth } from "@/lib/auth";
import { endpoints } from "@/lib/api";

interface Item {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
}

const PRIMARY: Item[] = [
  { to: "/", label: "Главная", icon: LayoutDashboard },
  { to: "/users", label: "Юзеры", icon: Users },
  { to: "/payments", label: "Платежи", icon: CreditCard },
  { to: "/broadcasts", label: "Рассылки", icon: Megaphone },
];

const MORE: Item[] = [
  { to: "/analytics", label: "Аналитика", icon: TrendingUp },
  { to: "/promo", label: "Промокоды", icon: Tag },
  { to: "/referrals", label: "Рефералы", icon: Share2 },
  { to: "/bgift", label: "Гифт-ГБ", icon: Gift },
  { to: "/audit", label: "Аудит", icon: ScrollText },
  { to: "/service", label: "Сервис", icon: Wrench },
  { to: "/settings", label: "Настройки", icon: SettingsIcon },
];

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Close the sheet whenever the user navigates.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  // Block body scroll while the sheet is open so iOS doesn't rubber-band
  // the page underneath.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const inMore = MORE.some((it) => location.pathname.startsWith(it.to));

  return (
    <>
      <nav
        className="fixed inset-x-2 z-30 flex justify-around rounded-2xl border border-border bg-bg-card/90 px-2 py-1.5 backdrop-blur-md md:hidden"
        style={{ bottom: "max(0.5rem, env(safe-area-inset-bottom))" }}
      >
        {PRIMARY.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.to === "/"}
            className={({ isActive }) =>
              cn(
                "flex flex-1 flex-col items-center gap-0.5 rounded-xl px-2 py-1.5 text-[10px] font-medium transition-all duration-200",
                // Active = solid lime pill с тёмным текстом и shadow-glow
                // (как «Dashboard» внизу из brand-deck).
                isActive
                  ? "bg-accent text-bg font-semibold shadow-glow-sm"
                  : "text-fg-subtle hover:text-fg",
              )
            }
          >
            <it.icon className="h-4 w-4" strokeWidth={2.25} />
            {it.label}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={cn(
            "flex flex-1 flex-col items-center gap-0.5 rounded-xl px-2 py-1.5 text-[10px] font-medium transition-all duration-200",
            inMore
              ? "bg-accent text-bg font-semibold shadow-glow-sm"
              : "text-fg-subtle hover:text-fg",
          )}
        >
          <MoreHorizontal className="h-4 w-4" strokeWidth={2.25} />
          Ещё
        </button>
      </nav>

      {open && (
        <div
          className="fixed inset-0 z-40 flex items-end bg-black/60 backdrop-blur-sm animate-fade-in md:hidden"
          onClick={() => setOpen(false)}
        >
          <div
            className="w-full rounded-t-3xl border-t border-x border-border bg-bg-subtle shadow-[0_-12px_40px_-8px_rgba(0,0,0,0.6)] animate-slide-up"
            style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border" />
            <div className="flex items-center justify-between px-5 pt-3 pb-2">
              <div>
                <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-fg-subtle">
                  Меню
                </div>
                <h3 className="text-base font-semibold text-fg">Все разделы</h3>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="grid h-9 w-9 place-items-center rounded-xl bg-bg-elevated text-fg-muted ring-1 ring-border"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2 px-5 py-3">
              {MORE.map((it) => (
                <NavLink
                  key={it.to}
                  to={it.to}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 rounded-xl border px-3 py-3 text-sm font-medium transition-colors",
                      isActive
                        ? "border-accent/40 bg-accent/10 text-accent"
                        : "border-border bg-bg-card text-fg hover:border-fg-subtle",
                    )
                  }
                >
                  <it.icon className="h-4 w-4 shrink-0" strokeWidth={2} />
                  <span className="truncate">{it.label}</span>
                </NavLink>
              ))}
            </div>

            <div className="px-5 pt-2 pb-4">
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
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm font-medium text-danger transition-colors hover:bg-danger/15"
              >
                <LogOut className="h-4 w-4" />
                Выйти
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
