/**
 * Desktop / iPad-landscape sidebar (≥ 1024px): every section as a grouped
 * list, like an iPad app. A lazy chunk — phones never download it.
 */
import { Link, NavLink } from "react-router-dom";
import { LogOut, ShieldCheck } from "lucide-react";
import { useBranding } from "@/lib/branding";
import { MORE_GROUPS, TABS, logout } from "@/lib/nav";
import { navIcon } from "./navIcons";

export function Sidebar() {
  const brand = useBranding();
  return (
    <aside
      aria-label="Разделы"
      className="sidebar fixed inset-y-0 left-0 z-20 flex w-[272px] flex-col overflow-y-auto border-r border-sep/80 bg-app px-3 pb-4 pt-[max(1rem,env(safe-area-inset-top))]"
    >
      <Link to="/" className="mb-4 flex items-center gap-3 rounded-[10px] px-2 py-1.5">
        <span className="grid h-9 w-9 flex-none place-items-center overflow-hidden rounded-[9px] bg-accent text-onaccent">
          {brand.logo_url ? (
            <img src={brand.logo_url} alt="" className="h-full w-full object-cover" />
          ) : (
            <ShieldCheck className="h-5 w-5" strokeWidth={2} aria-hidden="true" />
          )}
        </span>
        <span className="min-w-0 truncate text-[17px] font-semibold">{brand.admin_title}</span>
      </Link>
      <ul className="flex flex-col gap-0.5">
        {TABS.map(({ to, label, icon: Icon, end }) => (
          <li key={to}>
            <NavLink to={to} end={end} className="nav-row">
              <span className="nav-icon h-7 w-7">
                <Icon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
              </span>
              <span className="nav-row-label">{label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
      {MORE_GROUPS.map((g) => (
        <div key={g.title} className="mt-5">
          <p className="section-h mb-1 px-2.5 text-[12px]">{g.title}</p>
          <ul className="flex flex-col gap-0.5">
            {g.items.map(({ to, label }) => {
              const Icon = navIcon(to);
              return (
                <li key={to}>
                  <NavLink to={to} className="nav-row">
                    <span className="nav-icon h-7 w-7">
                      <Icon className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
                    </span>
                    <span className="nav-row-label">{label}</span>
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <button type="button" className="nav-row mt-5 text-danger" onClick={() => void logout()}>
        <span className="nav-icon h-7 w-7 bg-danger/12 text-danger">
          <LogOut className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
        </span>
        <span className="nav-row-label text-left">Выйти</span>
      </button>
    </aside>
  );
}
