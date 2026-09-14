/**
 * Shell — the app frame, laid out like a native iOS app:
 *   - a compact navigation bar that shows the screen title (and a back
 *     button on pushed screens) once the large title scrolls away;
 *   - a bottom tab bar: Обзор, Деньги, Подписчики, Здоровье, Ещё;
 *   - on wide screens (≥ 1024px) a sidebar with every section instead
 *     of the tab bar, like an iPad app (lazy chunk, never on a phone).
 * Safe-area insets keep everything clear of the notch and home indicator.
 */
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { ChevronLeft, MoreHorizontal } from "lucide-react";
import { useEventStream } from "@/lib/ws";
import { TABS, backFor, inMore, titleFor } from "@/lib/nav";
import { cn } from "@/lib/cn";
import { InstallHint } from "./InstallHint";
import { RouteTransition } from "./RouteTransition";
import { StatusDot } from "./ui/controls";

export { PRIMARY_NAV, MORE_NAV } from "@/lib/nav";

const Sidebar = lazy(() => import("./Sidebar").then((m) => ({ default: m.Sidebar })));
const WIDE = "(min-width: 1024px)";

function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof window.matchMedia === "function" && window.matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return wide;
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
    <span className="grid h-11 w-11 place-items-center" role="status" aria-label={label} title={label}>
      <StatusDot tone={status === "live" ? "ok" : status === "offline" ? "err" : "warn"} />
    </span>
  );
}

function TabBar() {
  const loc = useLocation();
  const more = inMore(loc.pathname);
  return (
    <nav aria-label="Разделы" className="tabbar bar-material lg:hidden">
      {TABS.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className="tab">
          <Icon className="h-6 w-6" strokeWidth={1.8} aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}
      <Link to="/more" className="tab" data-active={more} aria-current={more ? "page" : undefined}>
        <MoreHorizontal className="h-6 w-6" strokeWidth={1.8} aria-hidden="true" />
        <span>Ещё</span>
      </Link>
    </nav>
  );
}

export function Shell() {
  const loc = useLocation();
  const wide = useWide();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  // Each screen opens at its top, like a pushed view controller.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [loc.pathname]);

  const title = titleFor(loc.pathname);
  const back = backFor(loc.pathname);

  return (
    <div className="min-h-[100dvh]">
      <a href="#main" className="btn-primary sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50">
        К содержимому
      </a>
      {wide && (
        <Suspense fallback={null}>
          <Sidebar />
        </Suspense>
      )}
      <div className="lg:pl-[272px]">
        <header className={cn("navbar", scrolled && "bar-material")} data-scrolled={scrolled}>
          <div className="mx-auto grid h-11 max-w-[1240px] grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center px-1 sm:px-3 lg:px-6">
            <div className="flex min-w-0 items-center">
              {back && (
                <Link to={back.to} className="nav-back lg:hidden">
                  <ChevronLeft className="h-7 w-7 flex-none" strokeWidth={2.2} aria-hidden="true" />
                  <span className="truncate">{back.label}</span>
                </Link>
              )}
            </div>
            <div className="navbar-title max-w-[56vw] truncate text-center" aria-hidden={!scrolled}>
              {title}
            </div>
            <div className="flex items-center justify-end">
              <LiveDot />
            </div>
          </div>
        </header>
        <main
          id="main"
          tabIndex={-1}
          className="mx-auto max-w-[1240px] px-4 pb-[calc(var(--tabbar-h)+env(safe-area-inset-bottom)+2rem)] pt-1 outline-none sm:px-6 lg:px-8 lg:pb-12"
        >
          <RouteTransition />
        </main>
      </div>
      <TabBar />
      <InstallHint />
    </div>
  );
}
