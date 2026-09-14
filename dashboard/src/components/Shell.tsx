/**
 * Shell — the device the whole admin lives in.
 *
 * A single rounded "device" floats on the lit wall. Its top bar holds the
 * brand mark, the section tabs as capsules inside a darker capsule, and
 * circular controls on the right (live status, theme, settings, sign out).
 * Secondary tools sit behind "Ещё" so the primary row stays short enough
 * for a phone.
 */
import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { LogOut, Moon, MoreHorizontal, Settings as SettingsIcon, ShieldCheck, Sun } from "lucide-react";
import { endpoints } from "@/lib/api";
import { auth } from "@/lib/auth";
import { useBranding } from "@/lib/branding";
import { useEventStream } from "@/lib/ws";
import { usePrefs } from "@/store/prefs";
import { InstallHint } from "./InstallHint";
import { RouteTransition } from "./RouteTransition";
import { IconButton, StatusDot } from "./ui/controls";

export const PRIMARY_NAV = [
  { to: "/", label: "Обзор", end: true },
  { to: "/money", label: "Деньги" },
  { to: "/subscribers", label: "Подписчики" },
  { to: "/health", label: "Здоровье" },
  { to: "/panel", label: "Панель" },
  { to: "/users", label: "Пользователи" },
];

export const MORE_NAV = [
  { to: "/engagement", label: "Вовлечённость" },
  { to: "/broadcasts", label: "Рассылки" },
  { to: "/statistics", label: "Продажи по тарифам" },
  { to: "/automated-notifications", label: "Автоуведомления" },
  { to: "/pricing", label: "Цены и скидки" },
  { to: "/promo", label: "Промокоды" },
  { to: "/links", label: "Ссылки" },
  { to: "/referrals", label: "Рефералы" },
  { to: "/bgift", label: "Гифт-ГБ" },
  { to: "/beta-applications", label: "VPN-Инноватор" },
  { to: "/audit", label: "Журнал действий" },
  { to: "/bypass-audit", label: "Bypass-аудит" },
  { to: "/traffic-audit", label: "Аудит трафика" },
  { to: "/service", label: "Сервис" },
];

function BrandMark() {
  const brand = useBranding();
  return (
    <NavLink to="/" className="flex min-w-0 items-center gap-3" aria-label={`${brand.admin_title}: обзор`}>
      <span className="icon-btn overflow-hidden">
        {brand.logo_url ? (
          <img src={brand.logo_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <ShieldCheck className="h-[18px] w-[18px] text-accent" strokeWidth={2} />
        )}
      </span>
      <span className="on-shell hidden truncate text-[15px] font-semibold lg:block">{brand.short}</span>
    </NavLink>
  );
}

function LiveDot() {
  const [status, setStatus] = useState<"connecting" | "live" | "offline">("connecting");
  const lastBeat = useRef(Date.now());
  useEventStream(() => {
    lastBeat.current = Date.now();
    setStatus("live");
  });
  useEffect(() => {
    const t = window.setInterval(() => {
      if (Date.now() - lastBeat.current > 60_000) setStatus("offline");
    }, 5000);
    return () => window.clearInterval(t);
  }, []);
  const label =
    status === "live" ? "Живые события подключены" : status === "offline" ? "Нет связи с событиями" : "Подключение…";
  return (
    <span className="icon-btn cursor-default" role="status" aria-label={label} title={label}>
      <StatusDot tone={status === "live" ? "ok" : status === "offline" ? "err" : "warn"} />
    </span>
  );
}

function MoreMenu() {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const ref = useRef<HTMLDivElement>(null);
  const firstLink = useRef<HTMLAnchorElement>(null);
  const inMore = MORE_NAV.some((i) => loc.pathname.startsWith(i.to));

  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    if (!open) return;
    firstLink.current?.focus();
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        className="capsule-tab"
        aria-expanded={open}
        aria-controls="more-nav"
        aria-pressed={inMore}
        onClick={() => setOpen((v) => !v)}
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
        Ещё
      </button>
      {open && (
        // Phones: the nav row scrolls horizontally, which clipped an
        // absolute dropdown to one visible item. Below md the menu is a
        // fixed bottom sheet (outside that clip, above the home indicator).
        <button
          type="button"
          aria-label="Закрыть меню"
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}
      {open && (
        <div
          id="more-nav"
          className="tile fixed inset-x-2 bottom-[max(0.5rem,env(safe-area-inset-bottom))] z-50 max-h-[75dvh] overflow-y-auto p-2 shadow-[0_24px_48px_-16px_rgb(0_0_0/0.5)] animate-fade-in md:absolute md:inset-x-auto md:bottom-auto md:right-0 md:mt-2 md:max-h-[calc(100dvh-8rem)] md:w-[280px]"
        >
          <ul className="flex flex-col gap-1">
            {MORE_NAV.map((it, i) => (
              <li key={it.to}>
                <NavLink
                  ref={i === 0 ? firstLink : undefined}
                  to={it.to}
                  className="list-row min-h-[44px] bg-transparent text-[14px] aria-[current=page]:bg-tile-3"
                >
                  {it.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function Shell() {
  const theme = usePrefs((s) => s.theme);
  const setTheme = usePrefs((s) => s.setTheme);

  const logout = async () => {
    try {
      await endpoints.authLogout();
    } catch {
      //
    }
    auth.clear();
    window.location.assign("/dashboard/");
  };

  return (
    <div className="min-h-[100svh] p-2 sm:p-4 lg:p-6" style={{ paddingTop: "max(0.5rem, env(safe-area-inset-top))" }}>
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 btn-primary">
        К содержимому
      </a>
      <div className="device mx-auto flex min-h-[calc(100svh-1rem)] max-w-[1480px] flex-col p-3 sm:min-h-[calc(100svh-2rem)] sm:p-4 lg:min-h-[calc(100svh-3rem)] lg:p-5">
        <header className="flex flex-wrap items-center gap-3">
          <BrandMark />
          <nav
            aria-label="Разделы"
            className="order-last -mx-1 w-full overflow-x-auto px-1 scrollbar-none md:order-none md:mx-0 md:w-auto md:flex-1 md:overflow-visible md:px-0"
          >
            <div className="capsule-nav">
              {PRIMARY_NAV.map((it) => (
                <NavLink key={it.to} to={it.to} end={it.end} className="capsule-tab">
                  {it.label}
                </NavLink>
              ))}
              <MoreMenu />
            </div>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <LiveDot />
            <IconButton
              label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </IconButton>
            <NavLink to="/settings" className="icon-btn aria-[current=page]:icon-btn-accent" aria-label="Настройки" title="Настройки">
              <SettingsIcon className="h-4 w-4" />
            </NavLink>
            <IconButton label="Выйти" onClick={logout}>
              <LogOut className="h-4 w-4" />
            </IconButton>
          </div>
        </header>
        <main id="main" className="mt-5 flex-1 pb-[env(safe-area-inset-bottom)]" tabIndex={-1}>
          <RouteTransition />
        </main>
      </div>
      <InstallHint />
    </div>
  );
}
