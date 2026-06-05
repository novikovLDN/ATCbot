import { NavLink } from "react-router-dom";
import { LayoutDashboard, Users, TrendingUp, Megaphone, Share2 } from "lucide-react";
import { cn } from "@/lib/cn";

const items = [
  { to: "/", label: "Главная", icon: LayoutDashboard },
  { to: "/users", label: "Юзеры", icon: Users },
  { to: "/analytics", label: "Метрики", icon: TrendingUp },
  { to: "/broadcasts", label: "Рассылки", icon: Megaphone },
  { to: "/referrals", label: "Рефералы", icon: Share2 },
];

export function MobileNav() {
  return (
    <nav
      className="fixed inset-x-2 z-30 flex justify-around rounded-2xl border border-border bg-bg-card/80 px-2 py-1.5 backdrop-blur-md md:hidden"
      style={{ bottom: "max(0.5rem, env(safe-area-inset-bottom))" }}
    >
      {items.map((it) => (
        <NavLink
          key={it.to}
          to={it.to}
          end={it.to === "/"}
          className={({ isActive }) =>
            cn(
              "flex flex-1 flex-col items-center gap-0.5 rounded-xl px-2 py-1.5 text-[10px] font-medium transition-colors",
              isActive ? "text-accent" : "text-fg-subtle",
            )
          }
        >
          <it.icon className="h-4 w-4" />
          {it.label}
        </NavLink>
      ))}
    </nav>
  );
}
