/**
 * «Ещё» — every section that is not a tab, as an iOS grouped list, plus
 * appearance and sign-out. On wide screens the same list is the sidebar.
 */
import { NavLink } from "react-router-dom";
import { ChevronRight, LogOut } from "lucide-react";
import { MORE_GROUPS, logout } from "@/lib/nav";
import { useBranding } from "@/lib/branding";
import { usePrefs, type Theme } from "@/store/prefs";
import { PageHeader } from "@/components/ui/Surface";
import { Segmented } from "@/components/ui/controls";

const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "Как в системе" },
  { value: "light", label: "Светлая" },
  { value: "dark", label: "Тёмная" },
];

export function More() {
  const brand = useBranding();
  const theme = usePrefs((s) => s.theme);
  const setTheme = usePrefs((s) => s.setTheme);
  return (
    <>
      <PageHeader title="Ещё" />
      {MORE_GROUPS.map((g) => (
        <section key={g.title} className="mb-7">
          <h2 className="section-h mb-1.5 px-4">{g.title}</h2>
          <ul className="nav-group tile overflow-hidden">
            {g.items.map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <NavLink to={to} className="nav-row">
                  <span className="nav-icon">
                    <Icon className="h-[18px] w-[18px]" strokeWidth={2} aria-hidden="true" />
                  </span>
                  <span className="nav-row-body">
                    <span className="nav-row-label">{label}</span>
                    <ChevronRight className="row-chevron h-[18px] w-[18px]" strokeWidth={2.2} aria-hidden="true" />
                  </span>
                </NavLink>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <section className="mb-7">
        <h2 className="section-h mb-1.5 px-4">Оформление</h2>
        <div className="tile p-3">
          <Segmented label="Тема" value={theme} options={THEMES} onChange={setTheme} full />
        </div>
        <p className="t-mute mt-1.5 px-4 text-[13px] leading-[18px]">
          «Как в системе» переключает светлую и тёмную тему вместе с iPhone.
        </p>
      </section>

      <section className="mb-4">
        <ul className="nav-group tile overflow-hidden">
          <li>
            <button type="button" className="nav-row w-full text-danger" onClick={() => void logout()}>
              <span className="nav-icon bg-danger/12 text-danger">
                <LogOut className="h-[18px] w-[18px]" strokeWidth={2} aria-hidden="true" />
              </span>
              <span className="nav-row-body">
                <span className="nav-row-label text-left">Выйти</span>
              </span>
            </button>
          </li>
        </ul>
        <p className="t-mute mt-1.5 px-4 text-[13px] leading-[18px]">{brand.admin_title}</p>
      </section>
    </>
  );
}
