import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  TrendingUp,
  Megaphone,
  ScrollText,
  LogOut,
  ShieldCheck,
  Share2,
  Gift,
  Tag,
  Wrench,
  CreditCard,
  Settings as SettingsIcon,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { auth } from "@/lib/auth";
import { endpoints } from "@/lib/api";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  badge?: string;
}

const items: NavItem[] = [
  { to: "/", label: "Главная", icon: LayoutDashboard },
  { to: "/users", label: "Пользователи", icon: Users },
  { to: "/analytics", label: "Аналитика", icon: TrendingUp },
  { to: "/payments", label: "Платежи", icon: CreditCard },
  { to: "/broadcasts", label: "Рассылки", icon: Megaphone },
  { to: "/promo", label: "Промокоды", icon: Tag },
  { to: "/referrals", label: "Рефералы", icon: Share2 },
  { to: "/bgift", label: "Гифт-ГБ", icon: Gift },
  { to: "/audit", label: "Аудит", icon: ScrollText },
  { to: "/service", label: "Сервис", icon: Wrench },
  { to: "/settings", label: "Настройки", icon: SettingsIcon },
];

export function Sidebar() {
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-bg-subtle/40 px-4 py-6 md:flex">
      <div className="mb-8 flex items-center gap-3 px-2">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent to-secondary text-bg shadow-glow-sm">
          <ShieldCheck className="h-[18px] w-[18px]" strokeWidth={2.5} />
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight text-fg">Atlas</div>
          <div className="text-[11px] uppercase tracking-wider text-fg-subtle">Admin</div>
        </div>
      </div>

      <div className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.15em] text-fg-subtle">
        Main
      </div>
      <nav className="flex flex-1 flex-col gap-1">
        {items.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.to === "/"}
            className={({ isActive }) =>
              cn(
                "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all",
                isActive
                  // Holo-style highlight: horizontal lime→cyan gradient
                  // with soft glow underneath. Foreground stays dark so
                  // the icon + label read crisp on the bright fill.
                  ? "bg-gradient-to-r from-accent/85 via-accent/40 to-secondary/35 text-bg shadow-glow-sm ring-1 ring-accent/40"
                  : "text-fg-muted hover:bg-bg-card hover:text-fg",
              )
            }
          >
            {({ isActive }) => (
              <>
                <it.icon
                  className={cn(
                    "h-4 w-4 transition-colors",
                    isActive ? "text-bg" : "text-fg-subtle group-hover:text-fg-muted",
                  )}
                  strokeWidth={isActive ? 2.5 : 2}
                />
                <span className={cn("flex-1", isActive && "font-semibold")}>
                  {it.label}
                </span>
                {it.badge && (
                  <span className="rounded-full bg-bg-elevated px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-fg-subtle">
                    {it.badge}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

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
        className="mt-4 flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-fg-muted transition-all hover:bg-danger/10 hover:text-danger"
      >
        <LogOut className="h-4 w-4" />
        Выйти
      </button>
    </aside>
  );
}
