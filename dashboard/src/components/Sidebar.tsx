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
} from "lucide-react";
import { cn } from "@/lib/cn";
import { auth } from "@/lib/auth";

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
  { to: "/broadcasts", label: "Рассылки", icon: Megaphone },
  { to: "/promo", label: "Промокоды", icon: Tag },
  { to: "/referrals", label: "Рефералы", icon: Share2 },
  { to: "/bgift", label: "Гифт-ГБ", icon: Gift },
  { to: "/audit", label: "Аудит", icon: ScrollText },
  { to: "/service", label: "Сервис", icon: Wrench },
];

export function Sidebar() {
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-bg-subtle/40 px-4 py-6 md:flex">
      <div className="mb-8 flex items-center gap-3 px-2">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent to-violet-500 text-white shadow-[0_4px_12px_-2px_rgba(99,102,241,0.4)]">
          <ShieldCheck className="h-[18px] w-[18px]" strokeWidth={2.5} />
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight text-fg">Atlas</div>
          <div className="text-[11px] uppercase tracking-wider text-fg-subtle">Admin</div>
        </div>
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
                  ? "bg-accent/15 text-fg shadow-[inset_0_0_0_1px_rgba(99,102,241,0.25)]"
                  : "text-fg-muted hover:bg-bg-card hover:text-fg",
              )
            }
          >
            {({ isActive }) => (
              <>
                <it.icon
                  className={cn(
                    "h-4 w-4 transition-colors",
                    isActive ? "text-accent" : "text-fg-subtle group-hover:text-fg-muted",
                  )}
                />
                <span className="flex-1">{it.label}</span>
                {it.badge && (
                  <span className="rounded-full bg-bg-elevated px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-fg-subtle">
                    {it.badge}
                  </span>
                )}
                {isActive && (
                  <span className="absolute -left-4 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-accent" />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <button
        type="button"
        onClick={() => {
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
