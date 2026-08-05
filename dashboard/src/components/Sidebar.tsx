import { NavLink } from "react-router-dom";
import { LogOut, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/cn";
import { auth } from "@/lib/auth";
import { endpoints } from "@/lib/api";
import { NAV_BY_GROUP, GROUP_LABELS } from "@/lib/nav";

/**
 * Боковая навигация.
 *
 * Что изменилось против прошлой версии:
 *  - девять пунктов вместо шестнадцати (research §9.2). Ничего не удалено:
 *    бывшие пункты стали вкладками разделов, старые адреса редиректят;
 *  - из подписей убраны эмодзи (📊, 💸). Эмодзи рисуются системным шрифтом,
 *    их ширина и базовая линия отличаются на macOS, Windows и Android, и
 *    строка меню съезжает по-разному на разных машинах;
 *  - заголовки групп на одном языке и по одному основанию — как часто
 *    открывают. Было «Main» / «Маркетинг» / «System»: два языка и три разных
 *    основания деления;
 *  - активный пункт — сплошная заливка акцентом плюс aria-current от NavLink,
 *    а не только цвет текста;
 *  - фокус с клавиатуры виден: глобальное кольцо :focus-visible из index.css
 *    здесь ничем не перебивается.
 */
export function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-bg-subtle px-3 py-4 md:flex">
      <div className="mb-6 flex items-center gap-2.5 px-2">
        <div className="grid h-8 w-8 place-items-center rounded-md bg-accent-9 text-white">
          <ShieldCheck className="h-4 w-4" strokeWidth={2.25} aria-hidden />
        </div>
        <div className="min-w-0">
          <div className="text-base font-semibold leading-tight text-fg">Atlas</div>
          <div className="text-2xs uppercase tracking-[0.16em] text-fg-subtle">Админка</div>
        </div>
      </div>

      <nav aria-label="Разделы" className="flex flex-1 flex-col gap-5 overflow-y-auto">
        {NAV_BY_GROUP.map(({ group, items }) => (
          <div key={group}>
            <div className="mb-1 px-2.5 text-2xs font-medium uppercase tracking-[0.14em] text-fg-subtle">
              {GROUP_LABELS[group]}
            </div>
            <ul className="flex flex-col gap-0.5">
              {items.map((it) => (
                <li key={it.to}>
                  <NavLink
                    to={it.to}
                    end={it.to === "/"}
                    className={({ isActive }) =>
                      cn(
                        "group flex min-h-tap items-center gap-2.5 rounded-md px-2.5 py-2 text-base transition-colors duration-100",
                        isActive
                          ? "bg-accent-9 font-medium text-white"
                          : "font-normal text-fg-muted hover:bg-bg-elevated hover:text-fg",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <it.icon
                          className={cn(
                            "h-4 w-4 shrink-0",
                            isActive ? "text-white" : "text-fg-subtle group-hover:text-fg-muted",
                          )}
                          strokeWidth={2}
                          aria-hidden
                        />
                        <span className="truncate">{it.label}</span>
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
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
        className="mt-4 flex min-h-tap items-center gap-2.5 rounded-md px-2.5 py-2 text-base text-fg-muted transition-colors hover:bg-danger/10 hover:text-danger"
      >
        <LogOut className="h-4 w-4" aria-hidden />
        Выйти
      </button>
    </aside>
  );
}
